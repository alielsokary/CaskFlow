"""Summarize untrusted pilot artifacts without promoting them to product ownership."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from observe_package_install import PILOT_TOKENS, RUNNERS, save_evidence
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


def build_report(directory: Path) -> dict:
    plan = directory / "package-observation-plan"
    selected = read_json(plan / "matrix.json")["include"]
    revisions = read_json(plan / "revisions.json")
    passive = read_json(plan / "passive-identities.json")["casks"]
    known = defaultdict(set)
    for token, record in passive.items():
        for pair in identities(record.get("apps", []) + record.get("packageCandidates", [])):
            known[pair].add(token)
    rows, observed_claims = [], defaultdict(set)
    for job in selected:
        token, architecture = job["token"], job["architecture"]
        if token not in PILOT_TOKENS or architecture not in RUNNERS:
            raise ValueError("Unexpected pilot job")
        row = {"token": token, "architecture": architecture, "status": "missing_evidence"}
        file = directory / f"package-observation-{token}-{architecture}" / "observation.json"
        try:
            data = read_json(file)
            if (data.get("schemaVersion") != 1 or data.get("token") != token
                    or data.get("architecture") != architecture or data.get("ownershipVerified") is not False
                    or data.get("brewRevision") != revisions["brewRevision"]
                    or data.get("caskRevision") != revisions["caskRevision"]
                    or data.get("status") not in STATUSES):
                raise ValueError("Observation does not match the frozen plan")
            row["status"] = data["status"] if data["status"] != "installing" else "incomplete_observation"
            if row["status"] in OBSERVED and (
                    data.get("install", {}).get("returncode") != 0 or data["install"].get("timedOut")
                    or data.get("homebrewRegistered") is not True or data.get("snapshotsStable") is not True
                    or data.get("checksumVerified") is not True
                    or not re.fullmatch(r"[0-9a-f]{64}", str(data.get("artifactSHA256", "")))
                    or data.get("artifactSHA256") != data.get("expectedSHA256") or data.get("errors")):
                raise ValueError("Successful status contradicts the collected evidence")
            row["seconds"] = data.get("seconds")
            row["error"] = data.get("error")
            changes = data.get("applicationChanges", {})
            apps = changes.get("added", []) + changes.get("changed", [])
            if not isinstance(apps, list) or any(not isinstance(app, dict) for app in apps):
                raise ValueError("Invalid application observations")
            actual = identities(apps)
            valid = identities([app for app in apps if app.get("identifierAccepted") is True
                                and app.get("executablePresent") is True and "error" not in app])
            baseline = passive.get(token, {})
            expected = identities(baseline.get("apps", []) + baseline.get("packageCandidates", []))
            row.update(observedIdentities=pairs_json(actual), passiveNotObserved=pairs_json(expected - actual),
                       newObservedIdentities=pairs_json(valid - expected),
                       baselineRecordVersion=baseline.get("caskVersion"),
                       observedCaskVersion=data.get("cask", {}).get("version"), ownershipVerified=False)
            if row["status"] in OBSERVED:
                for pair in valid:
                    observed_claims[pair].add(token)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            row.update(status="invalid_evidence", error=str(error))
        rows.append(row)
    collisions = []
    for pair, tokens in sorted(observed_claims.items()):
        others = known[pair] | tokens
        if len(others) > 1:
            collisions.append({**pairs_json({pair})[0], "observedTokens": sorted(tokens),
                               "candidateTokens": sorted(others)})
    return {"schemaVersion": 1, "revisions": revisions, "counts": dict(Counter(row["status"] for row in rows)),
            "selectedJobs": len(selected), "observations": rows, "sharedIdentities": collisions,
            "ownershipVerified": False,
            "limitations": "Results describe tested installs, not ownership or complete platform coverage. "
                           "An absent passive identity can be optional, stale, or unsupported; it is not automatically a regression."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.directory)
    args.output.mkdir(parents=True, exist_ok=True)
    save_evidence(args.output / "summary.json", report)
    lines = ["# Package installation observations", "", report["limitations"], "",
             "| Cask | Architecture | Outcome |", "|---|---|---|"]
    lines.extend(f"| {row['token']} | {row['architecture']} | {row['status']} |" for row in report["observations"])
    lines += ["", f"Shared identity groups: {len(report['sharedIdentities'])}.",
              "", "No ownership mappings or release assets were published."]
    write_text(args.output / "summary.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
