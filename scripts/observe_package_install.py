"""Observe one package in a disposable CI runner; never publish matching rules."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import signal
import subprocess  # nosec B404 - fixed tool commands, argument lists, and no shell
import time
from pathlib import Path
from xml.parsers.expat import ExpatError

from app_identities import IDENTIFIER

PILOT_TOKENS = (
    "airtool", "microsoft-teams", "microsoft-office-businesspro", "onedrive",
    "zoom", "zoom-for-it-admins", "adobe-acrobat-reader", "openvpn-connect",
    "wacom-tablet", "blackhole-2ch",
)
RUNNERS = {"arm64": "macos-15", "x86_64": "macos-15-intel"}
SHA = re.compile(r"[0-9a-f]{40}\Z")
SETTLE_INTERVAL = 15
SETTLE_TIMEOUT = 120
SETTLE_EQUAL_INTERVALS = 3
CENSUS = json.loads((Path(__file__).resolve().parents[1] / "data/package_observation_census.json").read_text())
OBSERVATION_TOKENS = frozenset(PILOT_TOKENS).union(token for batch in CENSUS["batches"] for token in batch)


def save_evidence(path: Path, value: dict) -> None:
    # Untracked evidence must preserve exact filenames, unlike normalized authored prose.
    path.write_bytes((json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8"))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def matrix(tokens: str, architectures: str, census_batch: int = 0) -> list[dict]:
    if census_batch < 0 or census_batch > len(CENSUS["batches"]) or (census_batch and tokens.strip()):
        raise ValueError("Select one census batch or explicit tokens, not both")
    selected = (CENSUS["batches"][census_batch - 1] if census_batch
                else tokens.split() if tokens.strip() else list(PILOT_TOKENS))
    if (not selected or len(selected) > 25 or len(set(selected)) != len(selected)
            or set(selected) - OBSERVATION_TOKENS):
        raise ValueError("Select at most 25 distinct tokens from the fixed observation allowlist")
    arches = list(RUNNERS) if architectures == "both" else [architectures]
    if set(arches) - RUNNERS.keys():
        raise ValueError("Unsupported architecture")
    return [{"token": token, "architecture": arch, "runner": RUNNERS[arch]}
            for token in selected for arch in arches]


def build_plan(args) -> None:
    """Freeze every census batch to the same inputs; retain the small default pilot."""
    jobs = matrix(args.tokens, args.architectures, args.census_batch)
    args.output.mkdir(parents=True, exist_ok=False)
    commands = Commands(args.output)
    if args.census_batch or any(job["token"] not in PILOT_TOKENS for job in jobs):
        revisions = CENSUS["revisions"]
    else:
        tag = commands.text("brew-tag", ["gh", "api", "repos/Homebrew/brew/releases/latest", "--jq", ".tag_name"])
        revisions = {key: commands.text(key, ["gh", "api", f"repos/{repo}/commits/{ref}", "--jq", ".sha"])
                     for key, repo, ref in (("brewRevision", "Homebrew/brew", tag),
                                           ("caskRevision", "Homebrew/homebrew-cask", "HEAD"),
                                           ("passiveRevision", os.environ["GITHUB_REPOSITORY"], "icons"))}
    if not all(SHA.fullmatch(value) for value in revisions.values()):
        raise ValueError("Planning requires immutable source revisions")
    commands.run("passive-manifest", ["gh", "api",
        f"repos/{os.environ['GITHUB_REPOSITORY']}/contents/app_identities.json?ref={revisions['passiveRevision']}",
        "-H", "Accept: application/vnd.github.raw+json"])
    args.output.joinpath("passive-identities.json").write_bytes(args.output.joinpath("passive-manifest.log").read_bytes())
    save_evidence(args.output / "matrix.json", {"include": jobs})
    save_evidence(args.output / "revisions.json", revisions)
    if args.census_batch:
        save_evidence(args.output / "census.json", {"batch": args.census_batch, **CENSUS})


def require_disposable_runner(expected_arch: str) -> None:
    if not (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and os.environ.get("RUNNER_OS") == "macOS"
            and platform.system() == "Darwin"
            and platform.machine() == expected_arch):
        raise ValueError("Installation observation requires a matching GitHub-hosted macOS runner")


def executable_record(bundle: Path, info: Path, executable: str) -> dict:
    if not isinstance(executable, str) or Path(executable).name != executable or executable in (".", ".."):
        raise ValueError("Missing or unsafe CFBundleExecutable")
    binary = info.parent / ("MacOS" if info.parent.name == "Contents" else "") / executable
    if not binary.resolve().is_relative_to(bundle.resolve()):
        raise ValueError("Escaping application executable")
    return {"executablePath": str(binary), "executablePresent": binary.is_file(),
            "executableSHA256": digest(binary) if binary.is_file() else None}


def app_record(bundle: Path) -> dict:
    """Keep observations, including unsupported IDs, separate from accepted identities."""
    result = {"path": str(bundle), "bundleName": bundle.name}
    info = bundle / "Contents/Info.plist"
    if not info.exists():
        info = bundle / "Info.plist"
    try:
        if info.is_symlink() or not info.resolve().is_relative_to(bundle.resolve()):
            raise ValueError("Escaping or symlinked application plist")
        data = plistlib.loads(info.read_bytes())
        if not isinstance(data, dict):
            raise ValueError("Application plist is not a dictionary")
        identifier = data.get("CFBundleIdentifier")
        result.update(
            bundleIdentifier=identifier if isinstance(identifier, str) else None,
            identifierAccepted=isinstance(identifier, str) and bool(IDENTIFIER.fullmatch(identifier)),
            version=str(data.get("CFBundleShortVersionString", "")),
            buildVersion=str(data.get("CFBundleVersion", "")),
            infoPath=str(info), infoSHA256=digest(info),
        )
        result.update(executable_record(bundle, info, data.get("CFBundleExecutable")))
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
    return result


def receipt_snapshot(receipt_root: Path) -> tuple[dict, list[str]]:
    receipts, errors = {}, []
    if not receipt_root.is_dir():
        errors.append(f"Receipt directory unavailable: {receipt_root}")
    try:
        for path in sorted(receipt_root.glob("*.plist")):
            try:
                data = plistlib.loads(path.read_bytes())
                identifier = data.get("PackageIdentifier")
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError("Missing package identifier")
                if identifier in receipts:
                    raise ValueError(f"Duplicate receipt ID: {identifier}")
                receipts[identifier] = {
                    "identifier": identifier, "version": str(data.get("PackageVersion", "")),
                    "installLocation": str(data.get("InstallPrefixPath", "")),
                    "installedAt": str(data.get("InstallDate", "")), "sha256": digest(path),
                }
            except (OSError, ValueError, plistlib.InvalidFileException, AttributeError, ExpatError) as error:
                errors.append(f"{path}: {error}")
    except OSError as error:
        errors.append(str(error))
    return receipts, errors


def snapshot(application_roots: list[Path], receipt_root: Path) -> dict:
    receipts, errors = receipt_snapshot(receipt_root)
    apps = {}
    for root in application_roots:
        if not root.exists():
            continue
        for parent, directories, _ in os.walk(root, followlinks=False, onerror=lambda e: errors.append(str(e))):
            for name in list(directories):
                path = Path(parent) / name
                if path.is_symlink():
                    directories.remove(name)
                elif name.endswith(".app"):
                    directories.remove(name)  # embedded helpers are not separately installed products
                    apps[str(path)] = app_record(path)
    return {"applications": apps, "receipts": receipts, "errors": errors}


def changes(before: dict, after: dict) -> dict:
    return {"added": [after[key] for key in sorted(after.keys() - before.keys())],
            "changed": [after[key] for key in sorted(after.keys() & before.keys()) if before[key] != after[key]],
            "removed": [before[key] for key in sorted(before.keys() - after.keys())]}


def valid_application(app: dict) -> bool:
    return "error" not in app and app.get("identifierAccepted") is True and app.get("executablePresent") is True


def observation_diagnostics(data: dict) -> list[str]:
    """Explain recorded failures, including artifacts produced by the first pilot."""
    install = data.get("install", {})
    apps = data.get("applicationChanges", {})
    invalid_apps = any(not valid_application(app) for app in apps.get("added", []) + apps.get("changed", []))
    checks = ((install.get("timedOut"), "installer_timed_out"),
              (install.get("returncode", 0) != 0, "installer_failed"),
              (data.get("homebrewRegistered") is False, "homebrew_registration_missing"),
              (data.get("snapshotsStable") is False, "post_install_state_not_settled"),
              (bool(data.get("errors")), "evidence_read_errors"),
              (invalid_apps, "application_metadata_invalid"),
              (data.get("status") == "preexisting_software_changed", "preexisting_software_changed"),
              (bool(data.get("error")), "collection_failed"))
    return [reason for failed, reason in checks if failed]


def observation(before: dict, after: dict, install: dict, registered: bool, stable: bool) -> dict:
    apps = changes(before["applications"], after["applications"])
    receipts = changes(before["receipts"], after["receipts"])
    evidence_errors = before["errors"] + after["errors"]
    observed = apps["added"] + apps["changed"]
    if install.get("timedOut"):
        status = "install_timeout"
    elif install.get("returncode") != 0:
        status = "install_failed"
    elif not all((registered, stable, not evidence_errors, all(map(valid_application, observed)))):
        status = "incomplete_observation"
    elif any((apps["changed"], apps["removed"], receipts["changed"], receipts["removed"])):
        status = "preexisting_software_changed"
    elif observed:
        status = "observed_applications"
    else:
        status = "no_application_observed"
    return {"status": status, "applicationChanges": apps, "receiptChanges": receipts,
            "errors": evidence_errors, "homebrewRegistered": registered, "snapshotsStable": stable,
            "ownershipVerified": False}


class Commands:
    """Small process boundary: bounded commands with complete on-disk output."""

    def __init__(self, output: Path):
        """Keep command records and raw output in one evidence directory."""
        self.output = output

    def run(self, name: str, args: list[str], timeout: int = 300, *, check: bool = True) -> dict:
        started = time.monotonic()
        log = self.output / f"{name}.log"
        error_log = self.output / f"{name}.stderr.log"
        with log.open("wb") as stream, error_log.open("wb") as error_stream:
            process = subprocess.Popen(  # nosec B603 - fixed tools; validated tokens and revisions; no shell
                args, stdout=stream, stderr=error_stream, start_new_session=True)
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                process.wait()
        result = {"command": args, "returncode": process.returncode, "timedOut": timed_out,
                  "seconds": round(time.monotonic() - started, 2), "log": log.name,
                  "stderrLog": error_log.name}
        save_evidence(self.output / f"{name}.json", result)
        if check and (timed_out or process.returncode):
            raise RuntimeError(f"{name} failed; see {log.name}")
        return result

    def text(self, name: str, args: list[str], timeout: int = 300) -> str:
        self.run(name, args, timeout)
        return (self.output / f"{name}.log").read_text(encoding="utf-8").strip()


def checkout(commands: Commands, path: Path, repository: str, revision: str, name: str) -> None:
    if not SHA.fullmatch(revision):
        raise ValueError("Expected an immutable Git commit")
    if not (path / ".git").exists():
        path.mkdir(parents=True, exist_ok=True)
        commands.run(f"{name}-init", ["git", "init", str(path)])
        commands.run(f"{name}-remote", ["git", "-C", str(path), "remote", "add", "origin",
                                      f"https://github.com/{repository}.git"])
    commands.run(f"{name}-fetch", ["git", "-C", str(path), "fetch", "--depth=1", "origin", revision], 600)
    commands.run(f"{name}-checkout", ["git", "-C", str(path), "checkout", "--detach", revision])
    actual = commands.text(f"{name}-revision", ["git", "-C", str(path), "rev-parse", "HEAD"])
    if actual != revision:
        raise ValueError(f"{name} revision mismatch")


def archive_evidence(commands: Commands, cask: dict, tap_root: Path) -> dict:
    expected = cask.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Pilot requires a declared SHA-256 checksum")
    token = cask["token"]
    commands.run("fetch", ["brew", "fetch", "--cask", "--force", token], 900)
    archive = Path(commands.text("download-path", ["brew", "--cache", "--cask", token]))
    actual = digest(archive)
    if actual != expected:
        raise ValueError("Downloaded artifact differs from the declared checksum")
    source = Path(cask["ruby_source_path"])
    if source.is_absolute() or ".." in source.parts:
        raise ValueError("Invalid cask source path")
    commands.output.joinpath("cask.rb").write_bytes(tap_root.joinpath(source).read_bytes())
    return {"cask": cask, "expectedSHA256": expected, "artifactSHA256": actual,
            "checksumVerified": True, "caskSourceSHA256": digest(commands.output / "cask.rb")}


def prepare(commands: Commands, token: str, brew_revision: str, cask_revision: str) -> dict:
    brew_root = Path(commands.text("brew-root", ["brew", "--repository"]))
    checkout(commands, brew_root, "Homebrew/brew", brew_revision, "brew")
    tap_root = brew_root / "Library/Taps/homebrew/homebrew-cask"
    checkout(commands, tap_root, "Homebrew/homebrew-cask", cask_revision, "cask")
    commands.run("brew-config", ["brew", "config"])
    payload = json.loads(commands.text("cask-info", ["brew", "info", "--json=v2", "--cask", token]))
    cask = payload["casks"][0]
    if (cask["token"] != token
            or not any("pkg" in entry or "suite" in entry for entry in cask.get("artifacts", []))
            or any("installer" in entry for entry in cask.get("artifacts", []))):
        raise ValueError("Observation requires the selected official package or suite cask")
    if cask.get("installed"):
        raise ValueError("Target cask is already installed on this runner")
    # ponytail: this pilot excludes dependencies; baseline them separately before expanding its scope.
    dependencies = commands.text("dependencies", ["brew", "deps", "--cask", "--include-implicit", token])
    if dependencies:
        raise ValueError("Pilot requires a dependency-free cask; see dependencies.log")
    return archive_evidence(commands, cask, tap_root)


def preexisting_targets(cask: dict, baseline: dict) -> list[str]:
    """Conservatively reject dirty baselines; do not delete preinstalled software."""
    existing = []
    for stanza in cask.get("artifacts", []):
        for uninstall in stanza.get("uninstall", []):
            if not isinstance(uninstall, dict):
                continue
            deleted = uninstall.get("delete", [])
            deleted = [deleted] if isinstance(deleted, str) else deleted
            for path in deleted:
                if isinstance(path, str) and path.endswith(".app") and path in baseline["applications"]:
                    existing.append(path)
    return sorted(set(existing))


def receipt_patterns(cask: dict) -> list[str]:
    patterns = []
    for stanza in cask.get("artifacts", []):
        for uninstall in stanza.get("uninstall", []):
            values = uninstall.get("pkgutil", []) if isinstance(uninstall, dict) else []
            patterns.extend([values] if isinstance(values, str) else values)
    return patterns


def clean_baseline(commands: Commands, cask: dict, roots: list[Path]) -> dict:
    baseline = snapshot(roots, Path("/var/db/receipts"))
    save_evidence(commands.output / "before.json", baseline)
    if baseline["errors"] or preexisting_targets(cask, baseline):
        raise ValueError("Incomplete or contaminated baseline; see before.json")
    # Native pkgutil keeps Homebrew's receipt regular-expression semantics.
    for index, pattern in enumerate(receipt_patterns(cask)):
        query = commands.run(f"baseline-receipt-{index}", ["/usr/sbin/pkgutil", f"--pkgs={pattern}"], check=False)
        if query["timedOut"] or query["returncode"] not in (0, 1):
            raise ValueError("Could not inspect baseline receipts")
        if (commands.output / query["stderrLog"]).read_text().strip():
            raise ValueError("Baseline receipt query reported a diagnostic")
        if (commands.output / query["log"]).read_text().strip():
            raise ValueError("A matching receipt predates installation; see baseline-receipt logs")
    return baseline


def collect_receipt_evidence(commands: Commands, receipts: list[dict]) -> list[str]:
    errors = []
    for index, receipt in enumerate(receipts):
        identifier = receipt["identifier"]
        receipt["infoQuery"] = commands.run(f"receipt-{index}-info",
            ["/usr/sbin/pkgutil", "--pkg-info-plist", identifier], 30, check=False)
        receipt["filesQuery"] = commands.run(f"receipt-{index}-files",
            ["/usr/sbin/pkgutil", "--files", identifier], 30, check=False)
        if any(receipt[key]["returncode"] != 0 or receipt[key]["timedOut"]
               for key in ("infoQuery", "filesQuery")):
            errors.append(f"Incomplete receipt evidence: {identifier}")
    return errors


def snapshot_differences(before: dict, after: dict) -> list[dict]:
    differences = []
    for kind in ("applications", "receipts"):
        for key in sorted(before[kind].keys() | after[kind].keys()):
            old, new = before[kind].get(key, {}), after[kind].get(key, {})
            if old != new:
                fields = [field for field in sorted(old.keys() | new.keys()) if old.get(field) != new.get(field)]
                differences.append({"kind": kind, "key": key, "changedFields": fields, "before": old, "after": new})
    return differences


def settle_snapshots(output: Path, roots: list[Path]) -> tuple[dict, dict]:
    """Require consecutive quiet intervals without erasing earlier mutations."""
    started = time.monotonic()
    previous = snapshot(roots, Path("/var/db/receipts"))
    save_evidence(output / "after-immediate.json", previous)
    samples = [{"snapshot": "after-immediate.json", "seconds": round(time.monotonic() - started, 2), "changes": []}]
    equal_intervals = 0
    while time.monotonic() - started + SETTLE_INTERVAL <= SETTLE_TIMEOUT:
        time.sleep(SETTLE_INTERVAL)
        current = snapshot(roots, Path("/var/db/receipts"))
        name = f"after-{len(samples):03d}.json"
        save_evidence(output / name, current)
        samples.append({"snapshot": name, "seconds": round(time.monotonic() - started, 2),
                        "changes": snapshot_differences(previous, current)})
        equal_intervals = equal_intervals + 1 if current == previous else 0
        previous = current
        if equal_intervals >= SETTLE_EQUAL_INTERVALS:
            break
    save_evidence(output / "after.json", previous)
    settling = {"intervalSeconds": SETTLE_INTERVAL, "timeoutSeconds": SETTLE_TIMEOUT,
                "requiredEqualIntervals": SETTLE_EQUAL_INTERVALS, "equalIntervals": equal_intervals,
                "stable": equal_intervals >= SETTLE_EQUAL_INTERVALS,
                "changedAfterInstall": any(sample["changes"] for sample in samples), "samples": samples}
    save_evidence(output / "settling.json", settling)
    return previous, settling


def capture_install_history(output: Path, phase: str,
                            source: Path = Path("/Library/Receipts/InstallHistory.plist")) -> dict:
    """Keep native transaction groups as supporting evidence, not current ownership."""
    target = output / f"install-history-{phase}.plist"
    try:
        raw = source.read_bytes()
        records = plistlib.loads(raw)
        if not isinstance(records, list):
            raise ValueError("Expected installation history array")
        target.write_bytes(raw)
        return {"file": target.name, "sha256": digest(target), "entryCount": len(records)}
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError) as error:
        return {"error": f"{type(error).__name__}: {error}"}


def install_and_collect(commands: Commands, token: str, baseline: dict, roots: list[Path]) -> dict:
    history_before = capture_install_history(commands.output, "before")
    install = commands.run("install", ["brew", "install", "--cask", "--require-sha", token], 1800, check=False)
    after, settling = settle_snapshots(commands.output, roots)
    listing = commands.run("installed-casks", ["brew", "list", "--cask"], check=False)
    registered = listing["returncode"] == 0 and token in (commands.output / listing["log"]).read_text().splitlines()
    result = observation(baseline, after, install, registered, settling["stable"])
    result["install"] = install
    result["settling"] = settling
    result["installationHistory"] = {"before": history_before,
                                     "after": capture_install_history(commands.output, "after")}
    changed_apps = result["applicationChanges"]["added"] + result["applicationChanges"]["changed"]
    for index, app in enumerate(changed_apps):
        if app.get("infoPath"):
            try:
                evidence = commands.output / f"app-{index}-Info.plist"
                evidence.write_bytes(Path(app["infoPath"]).read_bytes())
                app["infoPlistEvidence"] = {"file": evidence.name, "sha256": digest(evidence)}
                if app["infoPlistEvidence"]["sha256"] != app["infoSHA256"]:
                    result["errors"].append(f"Application plist changed during collection: {app['path']}")
            except OSError as error:
                result["errors"].append(f"Could not preserve application plist: {error}")
        app["signingInspection"] = commands.run(f"app-{index}-signing",
            ["/usr/bin/codesign", "--display", "--verbose=4", app["path"]], 30, check=False)
    changed_receipts = result["receiptChanges"]["added"] + result["receiptChanges"]["changed"]
    result["errors"].extend(collect_receipt_evidence(commands, changed_receipts))
    if result["errors"] and result["status"] in ("observed_applications", "no_application_observed"):
        result["status"] = "incomplete_observation"
    return result


def observe(args) -> int:
    if args.token not in OBSERVATION_TOKENS:
        raise ValueError("Token is outside the fixed observation allowlist")
    if args.token not in PILOT_TOKENS and (args.cask_revision != CENSUS["revisions"]["caskRevision"]
                                         or args.brew_revision != CENSUS["revisions"]["brewRevision"]):
        raise ValueError("Census observations require the frozen census revisions")
    require_disposable_runner(args.architecture)
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    commands = Commands(output)
    result = {"schemaVersion": 1, "observationMethod": "homebrew-install", "token": args.token,
              "architecture": args.architecture, "status": "incomplete_observation", "ownershipVerified": False,
              "brewRevision": args.brew_revision, "caskRevision": args.cask_revision,
              "sourceRevision": os.environ.get("GITHUB_SHA"),
              "runId": os.environ.get("GITHUB_RUN_ID"), "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
              "macOSVersion": platform.mac_ver()[0], "runnerImage": os.environ.get("ImageOS"),
              "runnerImageVersion": os.environ.get("ImageVersion")}
    save_evidence(output / "observation.json", result)
    started = time.monotonic()
    try:
        result.update(prepare(commands, args.token, args.brew_revision, args.cask_revision))
        roots = [Path("/Applications"), Path.home() / "Applications"]
        result["applicationRoots"] = [str(root) for root in roots]
        baseline = clean_baseline(commands, result["cask"], roots)
        result["status"] = "installing"
        save_evidence(output / "observation.json", result)
        result.update(install_and_collect(commands, args.token, baseline, roots))
    except (OSError, ValueError, RuntimeError, KeyError, IndexError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
        result["status"] = "incomplete_observation"
    finally:
        result["diagnostics"] = observation_diagnostics(result)
        result["seconds"] = round(time.monotonic() - started, 2)
        save_evidence(output / "observation.json", result)
    return 0 if result["status"] in ("observed_applications", "no_application_observed") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("matrix")
    plan.add_argument("--tokens", default="")
    plan.add_argument("--architectures", choices=["both", *RUNNERS], default="both")
    plan.add_argument("--census-batch", type=int, default=0)
    planning = sub.add_parser("plan")
    planning.add_argument("--tokens", default="")
    planning.add_argument("--architectures", choices=["both", *RUNNERS], default="both")
    planning.add_argument("--census-batch", type=int, default=0)
    planning.add_argument("--output", required=True, type=Path)
    run = sub.add_parser("observe")
    run.add_argument("--token", required=True, choices=sorted(OBSERVATION_TOKENS))
    run.add_argument("--architecture", required=True, choices=RUNNERS)
    run.add_argument("--brew-revision", required=True)
    run.add_argument("--cask-revision", required=True)
    run.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "matrix":
        print(json.dumps({"include": matrix(args.tokens, args.architectures, args.census_batch)}))
    elif args.command == "plan":
        build_plan(args)
    else:
        raise SystemExit(observe(args))


if __name__ == "__main__":
    main()
