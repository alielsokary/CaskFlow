"""Summarize untrusted pilot artifacts without promoting them to product ownership."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from observe_package_install import PILOT_TOKENS, RUNNERS, observation_diagnostics, save_evidence, valid_application
from style_standards import write_text

OBSERVED = {"observed_applications", "no_application_observed"}
STATUSES = OBSERVED | {"install_timeout", "install_failed", "incomplete_observation",
                       "preexisting_software_changed", "installing"}


def read_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Invalid or oversized observation artifact")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def identity(record: dict) -> tuple[str, str] | None:
    name, identifier = record.get("bundleName"), record.get("bundleIdentifier")
    if isinstance(name, str) and isinstance(identifier, str):
        return name, identifier.lower()
    return None


def identities(records: list) -> set[tuple[str, str]]:
    return {pair for item in records if isinstance(item, dict) and (pair := identity(item))}


def pairs_json(pairs: set[tuple[str, str]]) -> list[dict]:
    return [{"bundleName": name, "bundleIdentifier": identifier} for name, identifier in sorted(pairs)]


def validate_success(data: dict) -> None:
    install = data.get("install", {})
    checks = (install.get("returncode") == 0, not install.get("timedOut"),
              data.get("homebrewRegistered") is True, data.get("snapshotsStable") is True,
              data.get("checksumVerified") is True,
              bool(re.fullmatch(r"[0-9a-f]{64}", str(data.get("artifactSHA256", "")))),
              data.get("artifactSHA256") == data.get("expectedSHA256"), not data.get("errors"))
    if not all(checks):
        raise ValueError("Successful status contradicts the collected evidence")


def validate_provenance(data: dict, job: dict, revisions: dict) -> None:
    expected = {"schemaVersion": 1, "token": job["token"], "architecture": job["architecture"],
                "brewRevision": revisions["brewRevision"], "caskRevision": revisions["caskRevision"]}
    if (any(data.get(key) != value for key, value in expected.items())
            or data.get("ownershipVerified") is not False or data.get("status") not in STATUSES):
        raise ValueError("Observation does not match the frozen plan")
    if data["status"] in OBSERVED:
        validate_success(data)


def observation_row(directory: Path, job: dict, revisions: dict, baseline: dict) -> tuple[dict, set]:
    token, architecture = job["token"], job["architecture"]
    row = {"token": token, "architecture": architecture, "status": "missing_evidence"}
    valid = set()
    file = directory / f"package-observation-{token}-{architecture}" / "observation.json"
    try:
        data = read_json(file)
        validate_provenance(data, job, revisions)
        row["status"] = data["status"] if data["status"] != "installing" else "incomplete_observation"
        row["seconds"] = data.get("seconds")
        row["error"] = data.get("error")
        changes = data.get("applicationChanges", {})
        apps = changes.get("added", []) + changes.get("changed", [])
        if not isinstance(apps, list) or any(not isinstance(app, dict) for app in apps):
            raise ValueError("Invalid application observations")
        actual = identities(apps)
        valid = identities(list(filter(valid_application, apps)))
        expected = identities(baseline.get("apps", []) + baseline.get("packageCandidates", []))
        row.update(observedIdentities=pairs_json(actual), passiveNotObserved=pairs_json(expected - actual),
                   newObservedIdentities=pairs_json(valid - expected),
                   baselineRecordVersion=baseline.get("caskVersion"),
                   observedCaskVersion=data.get("cask", {}).get("version"), ownershipVerified=False,
                   diagnostics=observation_diagnostics(data), settling=data.get("settling"),
                   installationHistory=data.get("installationHistory"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        row.update(status="invalid_evidence", error=str(error))
        valid = set()
    return row, valid


def shared_identities(observed_claims: dict, passive: dict) -> list[dict]:
    known = defaultdict(set)
    for token, record in passive.items():
        for pair in identities(record.get("apps", []) + record.get("packageCandidates", [])):
            known[pair].add(token)
    collisions = []
    for pair, observations in sorted(observed_claims.items()):
        tokens = {item["token"] for item in observations}
        others = known[pair] | tokens
        if len(others) > 1:
            collisions.append({**pairs_json({pair})[0], "observedTokens": sorted(tokens),
                               "candidateTokens": sorted(others), "observations": observations})
    return collisions


def build_report(directory: Path) -> dict:
    plan = directory / "package-observation-plan"
    selected = read_json(plan / "matrix.json")["include"]
    revisions = read_json(plan / "revisions.json")
    passive = read_json(plan / "passive-identities.json")["casks"]
    rows, observed_claims = [], defaultdict(list)
    for job in selected:
        token = job["token"]
        if token not in PILOT_TOKENS or job["architecture"] not in RUNNERS:
            raise ValueError("Unexpected pilot job")
        row, valid = observation_row(directory, job, revisions, passive.get(token, {}))
        rows.append(row)
        for pair in valid:
            observed_claims[pair].append({"token": token, "architecture": job["architecture"],
                                         "status": row["status"],
                                         "evidenceQuality": "stable" if row["status"] in OBSERVED else "partial"})
    return {"schemaVersion": 1, "revisions": revisions, "counts": dict(Counter(row["status"] for row in rows)),
            "selectedJobs": len(selected), "observations": rows, "sharedIdentities": shared_identities(observed_claims, passive),
            "ownershipVerified": False,
            "limitations": "Results describe tested installs, not ownership or complete platform coverage. "
                           "An absent passive identity can be optional, stale, or unsupported; it is not automatically a regression."}


def markdown_row(row: dict) -> str:
    # Diagnostic codes are derived from evidence flags, never raw installer messages.
    reasons = ", ".join(row.get("diagnostics", [])) or "-"
    settling = row.get("settling")
    changed = str(settling.get("changedAfterInstall", "unknown")).lower() if isinstance(settling, dict) else "unknown"
    return f"| {row['token']} | {row['architecture']} | {row['status']} | {reasons} | {changed} |"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.directory)
    args.output.mkdir(parents=True, exist_ok=True)
    save_evidence(args.output / "summary.json", report)
    lines = ["# Package installation observations", "", report["limitations"], "",
             "| Cask | Architecture | Outcome | Diagnostics | Changed after install |",
             "|---|---|---|---|---|"]
    lines.extend(markdown_row(row) for row in report["observations"])
    lines += ["", f"Shared identity groups: {len(report['sharedIdentities'])}.",
              "Shared groups include partial observations; each source retains its status in summary.json.",
              "", "No ownership mappings or release assets were published."]
    write_text(args.output / "summary.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
