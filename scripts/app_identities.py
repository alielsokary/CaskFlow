"""Extract declared app identities and compose release metadata without name guesses."""
#  Created by Ali Elsokary on 06/09/2026.
#  Copyright © 2026 BuildingLink. All rights reserved.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from style_standards import write_json

MANIFEST = "app_identities.json"
ROOT = Path(__file__).resolve().parent.parent
IDENTIFIER = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z")


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "casks": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("casks"), dict):
        raise ValueError(f"Unsupported identity manifest: {path}")
    return data


def _app_entries(stanza: object) -> list[tuple[str, str]]:
    if not isinstance(stanza, dict) or not isinstance(stanza.get("app"), list):
        return []
    entries = []
    for item in stanza["app"]:
        if isinstance(item, str):
            entries.append((item, PurePosixPath(item).name))
        elif isinstance(item, dict) and isinstance(item.get("target"), str) and entries:
            entries[-1] = (entries[-1][0], PurePosixPath(item["target"]).name)
    return entries


def declared_apps(cask: dict) -> list[tuple[str, str]]:
    """Source artifact path and installed basename, honoring Homebrew target renames."""
    result = []
    for stanza in cask.get("artifacts") or []:
        for source, target in _app_entries(stanza):
            path = PurePosixPath(source)
            if not path.is_absolute() and ".." not in path.parts and source.endswith(".app") and target.endswith(".app"):
                result.append((source, target))
    return result


def _application_bundles(root: Path) -> list[Path]:
    bundles = []
    for parent, directories, _ in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(parent) / name
            if path.is_symlink():
                directories.remove(name)
            elif name.endswith(".app"):
                bundles.append(path)
                directories.remove(name)  # embedded helpers are not declared top-level apps
    return bundles


def _bundle_identifier(bundle: Path) -> str | None:
    info_path = bundle / "Contents" / "Info.plist"
    if info_path.is_symlink() or info_path.parent.is_symlink() or not info_path.resolve().is_relative_to(bundle.resolve()):
        return None
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    identifier = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
    return identifier if isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier) else None


def extract_identities(cask: dict, root: Path, artifact: Path) -> dict:
    """Inspect every declared app; reject duplicates, symlinks and heuristic selections."""
    bundles = _application_bundles(root)
    apps = []
    for source, target in declared_apps(cask):
        parts = PurePosixPath(source).parts
        matches = [p for p in bundles if p.parts[-len(parts):] == parts]
        if len(matches) != 1:
            continue
        identifier = _bundle_identifier(matches[0])
        if identifier is not None:
            apps.append({"bundleName": target, "bundleIdentifier": identifier})
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"caskVersion": cask.get("version"), "sourceURL": cask["url"],
            "artifactSHA256": digest.hexdigest(), "apps": apps}


def needs_refresh(cask: dict, entry: dict | None) -> bool:
    if entry is None:
        return True
    checksum = cask.get("sha256")
    return (entry.get("caskVersion") != cask.get("version")
            or entry.get("sourceURL") != cask.get("url")
            or (checksum not in (None, "no_check") and checksum != entry.get("artifactSHA256")))


def record_extraction(cask: dict, root: Path, artifact: Path, output: Path) -> None:
    manifest = load_manifest(output / MANIFEST)
    manifest["casks"][cask["token"]] = extract_identities(cask, root, artifact)
    write_json(output / MANIFEST, manifest, trailing_newline=True)


def merge_extractions(destination: Path, source: Path, dirty: set[str]) -> None:
    """Update only successfully inspected tokens; preserve concurrent batches and failures."""
    if not source.exists():
        return
    base = load_manifest(destination)
    incoming = load_manifest(source)["casks"]
    for token in dirty & incoming.keys():
        base["casks"][token] = incoming[token]
    write_json(destination, base, trailing_newline=True)


def _project_identity(token: str, record: dict, reviewed: list[dict]) -> dict:
    identifier = record.get("bundleIdentifier", "")
    name = record.get("bundleName", "")
    if not IDENTIFIER.fullmatch(identifier) or not name.endswith(".app") or Path(name).name != name:
        raise ValueError(f"Invalid identity for {token}")
    if record in reviewed and not record.get("evidence"):
        raise ValueError(f"Missing review evidence for {token}")
    return {"bundleName": name, "bundleIdentifier": identifier}


def compose_release(categories: Path, extracted: Path, variants: Path, output: Path,
                    seed: Path | None = None) -> None:
    """Keep provenance in the manifest and embed its matching projection in categories."""
    catalog = json.loads(categories.read_text(encoding="utf-8"))
    manifest = load_manifest(seed) if seed is not None else {"schemaVersion": 1, "casks": {}}
    manifest["casks"].update(load_manifest(extracted)["casks"])
    reviewed = load_manifest(variants)["casks"]
    identities = {}
    for token in sorted(manifest["casks"].keys() | reviewed.keys()):
        if token not in catalog["tokenToCategory"]:
            continue
        entry = manifest["casks"].setdefault(token, {"apps": []})
        entry["reviewedVariants"] = reviewed.get(token, [])
        records = entry.get("apps", []) + entry["reviewedVariants"]
        valid = []
        for record in records:
            projected = _project_identity(token, record, entry["reviewedVariants"])
            if projected not in valid:
                valid.append(projected)
        if valid:
            identities[token] = valid
    catalog["appIdentities"] = identities
    catalog["metadataUpdatedAt"] = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    write_json(categories, catalog, trailing_newline=True)
    write_json(output, manifest, trailing_newline=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--categories", type=Path, default=ROOT / "categories.json")
    parser.add_argument("--extracted", type=Path, required=True)
    parser.add_argument("--variants", type=Path, default=ROOT / "data/app_identity_variants.json")
    parser.add_argument("--seed", type=Path, help="Baseline identities; fresh extraction records take precedence")
    parser.add_argument("--output", type=Path, default=ROOT / MANIFEST)
    args = parser.parse_args()
    compose_release(args.categories, args.extracted, args.variants, args.output, args.seed)
