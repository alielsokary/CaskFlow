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
from unicodedata import normalize
from xml.parsers.expat import ExpatError, ParserCreate

from style_standards import write_json

MANIFEST = "app_identities.json"
ROOT = Path(__file__).resolve().parent.parent
IDENTIFIER = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\Z")
EXTRACTION_VERSION = 4


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "casks": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != 1 or not isinstance(data.get("casks"), dict):
        raise ValueError(f"Unsupported identity manifest: {path}")
    return data


def _app_entries(stanza: object, kind: str = "app") -> list[tuple[str, str]]:
    if not isinstance(stanza, dict) or not isinstance(stanza.get(kind), list):
        return []
    entries = []
    for item in stanza[kind]:
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
    return result + declared_artifact_apps(cask)


def _artifact_app_name(source: str, target: str) -> str | None:
    destination = target.replace("$APPDIR/", "/Applications/", 1)
    path = PurePosixPath(destination)
    if (_safe_relative(source) and source.endswith(".app") and path.name.endswith(".app")
            and destination.split("/") == ["", "Applications", path.name] and _safe_relative(path.name)
            and path.name == PurePosixPath(source).name):
        # Renamed generic apps need source/target provenance in adoption first.
        return path.name
    return None


def declared_artifact_apps(cask: dict) -> list[tuple[str, str]]:
    """Generic artifacts qualify only when explicitly moved to the app directory."""
    result = []
    for stanza in cask.get("artifacts") or []:
        if not isinstance(stanza, dict):
            continue
        entries = stanza.get("artifact", [])
        if not isinstance(entries, list) or len(entries) != 2:
            continue
        source, options = entries
        if not isinstance(source, str) or not isinstance(options, dict):
            continue
        target = options.get("target", "")
        if not isinstance(target, str):
            continue
        if name := _artifact_app_name(source, target):
            result.append((source, name))
    return result


def _application_bundles(root: Path) -> list[Path]:
    return [path for path in _directories(root) if path.name.endswith(".app")]


def _directories(root: Path) -> list[Path]:
    found = []
    for parent, directories, _ in os.walk(root, followlinks=False):
        for name in list(directories):
            path = Path(parent) / name
            if path.is_symlink():
                directories.remove(name)
            else:
                found.append(path)
                if name.endswith(".app"):
                    directories.remove(name)  # embedded helpers are not top-level apps
    return found


def _bundle_identifier(bundle: Path) -> tuple[str | None, str | None]:
    info_path = bundle / "Contents" / "Info.plist"
    if info_path.is_symlink() or info_path.parent.is_symlink() or not info_path.resolve().is_relative_to(bundle.resolve()):
        return None, "symlinked or escaping Info.plist"
    try:
        data = info_path.read_bytes()
        # Valid XML plists may start with a DOCTYPE instead of an XML declaration.
        fmt = plistlib.FMT_BINARY if data.startswith(b"bplist00") else plistlib.FMT_XML
        info = plistlib.loads(data, fmt=fmt)
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError) as error:
        return None, f"unreadable Info.plist: {type(error).__name__}"
    identifier = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
    if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
        return None, f"unsupported CFBundleIdentifier: {identifier!r}"
    return identifier, None


def artifact_matches(path: Path, source: str) -> bool:
    """Honor macOS case/Unicode equivalence while leaving duplicate checks to callers."""
    parts = PurePosixPath(normalize("NFC", source).casefold()).parts
    return bool(parts) and tuple(normalize("NFC", p).casefold() for p in path.parts[-len(parts):]) == parts


def _safe_relative(source: str) -> bool:
    path = PurePosixPath(source)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts and not any(
        char in source for char in "$*?[]~")


def _parse_package_xml(path: Path, start_element, end_element=None) -> None:
    """Use native Expat without permitting DTDs or entity expansion."""
    def reject_doctype(*args):
        raise ValueError("Package XML DTDs are not supported")

    parser = ParserCreate()
    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.StartDoctypeDeclHandler = reject_doctype
    with path.open("rb") as source:
        parser.ParseFile(source)


def _package_info(info: Path) -> dict:
    attributes = None

    def start_element(name, attrs):
        nonlocal attributes
        if attributes is None:
            if name != "pkg-info":
                raise ValueError("not a package info document")
            attributes = attrs

    _parse_package_xml(info, start_element)
    return attributes


def package_selection_reason(cask: dict) -> str | None:
    if any(isinstance(option, dict) and option.get("choices")
           for stanza in cask.get("artifacts") or [] if isinstance(stanza, dict)
           for option in stanza.get("pkg", [])):
        return "package choices require component selection; identity inspection deferred"
    return None


def _distribution_components(root: Path) -> set[str] | None:
    """Accept only unconditional product choices; never evaluate installer scripts."""
    distribution = root / "Distribution"
    if distribution.is_symlink():
        raise ValueError("symlinked Distribution")
    if not distribution.exists():
        return None
    stack, components, outlined, choices = [], set(), set(), set()
    choice_ids = {"line": ("choice", outlined), "choice": ("id", choices)}

    def start_element(name, attrs):
        if not stack and name != "installer-gui-script":
            raise ValueError("not a package Distribution")
        if name == "script" or attrs.keys() & {"selected", "start_selected", "enabled", "start_enabled",
                                              "active", "script", "customLocation"}:
            raise ValueError("conditional package Distribution requires component selection")
        if name in choice_ids:
            attribute, seen = choice_ids[name]
            choice = attrs.get(attribute, "")
            if choice in seen:
                raise ValueError("duplicate package Distribution choice")
            seen.add(choice)
        if name == "pkg-ref" and stack and stack[-1] == "choice":
            components.add(attrs.get("id", ""))
        stack.append(name)

    _parse_package_xml(distribution, start_element, lambda _: stack.pop())
    if not components or "" in components | choices | outlined or choices != outlined:
        raise ValueError("ambiguous package Distribution choices")
    return components


def _package_info_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("PackageInfo")
                  if not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
                  and "Payload" not in path.relative_to(root).parts
                  and not any(p.endswith(".app") for p in path.relative_to(root).parts))


def _package_payload(info: Path, root: Path, location: PurePosixPath) -> Path:
    payload = info.parent / "Payload"
    if (not location.is_absolute() or ".." in location.parts or payload.is_symlink()
            or not payload.is_dir() or not payload.resolve().is_relative_to(root.resolve())):
        raise ValueError("unsafe or missing package payload")
    return payload


def _installed_package_apps(payload: Path, location: PurePosixPath) -> list[tuple[Path, str]]:
    # Some packages install Payload/Contents directly into /Applications/App.app.
    bundles = [payload] if location.name.endswith(".app") else _application_bundles(payload)
    candidates = []
    for bundle in bundles:
        installed = location / bundle.relative_to(payload)
        if (not installed.is_relative_to("/Applications") or not installed.name.endswith(".app")
                or any(p.endswith(".app") for p in installed.parts[:-1])):
            continue  # Library helpers and embedded apps are not application ownership evidence.
        candidates.append((bundle, installed.name))
    return candidates


def _package_apps(root: Path, diagnostics: list[dict]) -> list[tuple[Path, str, dict]]:
    """Use package install locations, never icon guesses, to identify payload apps."""
    candidates = []
    try:
        components = _distribution_components(root)
    except (OSError, ExpatError, ValueError) as error:
        diagnostics.append({"artifact": "Distribution", "reason": str(error)})
        return []
    infos = _package_info_files(root)
    if not infos:
        diagnostics.append({"artifact": "pkg", "reason": "no PackageInfo in expanded package"})
    for info in infos:
        try:
            attributes = _package_info(info)
            if components is not None and attributes.get("identifier") not in components:
                continue
            identifier = attributes.get("identifier")
            if not identifier or attributes.get("relocatable") == "true":
                raise ValueError("missing package identifier or relocatable payload")
            location = PurePosixPath(attributes.get("install-location", "/"))
            payload = _package_payload(info, root, location)
            candidates.extend((bundle, name, {"packageIdentifier": identifier,
                                              "installedPath": str(location / bundle.relative_to(payload))})
                              for bundle, name in _installed_package_apps(payload, location))
        except (OSError, ExpatError, ValueError) as error:
            diagnostics.append({"artifact": str(info.relative_to(root)), "reason": f"invalid PackageInfo: {error}"})
    return candidates


def _declared_app_candidates(cask: dict, bundles: list[Path], diagnostics: list[dict]) -> list[tuple[Path, str, dict]]:
    candidates = []
    for source, target in declared_apps(cask):
        matches = [p for p in bundles if artifact_matches(p, source)]
        if len(matches) != 1:
            diagnostics.append({"artifact": source, "reason": f"declared app matched {len(matches)} bundles"})
            continue
        candidates.append((matches[0], target, {}))
    return candidates


def _safe_suite(stanza: dict, source: str, target: str) -> bool:
    target_paths = [item["target"] for item in stanza["suite"]
                    if isinstance(item, dict) and isinstance(item.get("target"), str)]
    return _safe_relative(source) and _safe_relative(target) and all(_safe_relative(p) for p in target_paths)


def _suite_candidates(cask: dict, directories: list[Path], bundles: list[Path],
                      diagnostics: list[dict]) -> list[tuple[Path, str, dict]]:
    candidates = []
    for stanza in cask.get("artifacts") or []:
        for source, target in _app_entries(stanza, "suite"):
            if not _safe_suite(stanza, source, target):
                diagnostics.append({"artifact": source, "reason": "suite requires unsupported staging or target path"})
                continue
            matches = [p for p in directories if artifact_matches(p, source)]
            if len(matches) != 1:
                diagnostics.append({"artifact": source, "reason": f"declared suite matched {len(matches)} directories"})
                continue
            candidates.extend((p, p.name, {}) for p in bundles if p.is_relative_to(matches[0]))
    return candidates


def _verified_apps(candidates: list[tuple[Path, str, dict]], diagnostics: list[dict]) -> list[dict]:
    # A basename alone cannot distinguish multiple installed app locations.
    by_name: dict[str, set[Path]] = {}
    for bundle, name, _ in candidates:
        by_name.setdefault(normalize("NFC", name).casefold(), set()).add(bundle)
    apps = []
    for bundle, name, provenance in candidates:
        if len(by_name[normalize("NFC", name).casefold()]) != 1:
            diagnostics.append({"artifact": name, "reason": "ambiguous installed bundle name"})
            continue
        identifier, reason = _bundle_identifier(bundle)
        if identifier is not None:
            record = {"bundleName": name, "bundleIdentifier": identifier, **provenance}
            if record not in apps:
                apps.append(record)
        else:
            diagnostics.append({"artifact": name, "reason": reason})
    return apps


def extract_identities(cask: dict, root: Path, artifact: Path, *,
                       package_roots: tuple[Path, ...] = (), diagnostics: tuple[dict, ...] = ()) -> dict:
    """Inspect declared apps, suites and payloads without heuristic identity selection."""
    directories = _directories(root)
    bundles = [path for path in directories if path.name.endswith(".app")]
    diagnostics = list(diagnostics)
    candidates = _declared_app_candidates(cask, bundles, diagnostics)
    candidates.extend(_suite_candidates(cask, directories, bundles, diagnostics))
    package_reason = package_selection_reason(cask)
    if package_reason:
        diagnostics.append({"artifact": "pkg", "reason": package_reason})
    else:
        for package_root in package_roots:
            candidates.extend(_package_apps(package_root, diagnostics))
    apps = _verified_apps(candidates, diagnostics)
    if not apps and not diagnostics:
        diagnostics.append({"artifact": cask["token"], "reason": "no supported application bundles in declared artifacts"})
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"caskVersion": cask.get("version"), "sourceURL": cask["url"],
            "artifactSHA256": digest.hexdigest(), "apps": apps,
            "extractionVersion": EXTRACTION_VERSION, "diagnostics": diagnostics}


def needs_refresh(cask: dict, entry: dict | None) -> bool:
    if entry is None:
        return True
    checksum = cask.get("sha256")
    return (entry.get("caskVersion") != cask.get("version")
            or ((not entry.get("apps") or any("pkg" in a for a in cask.get("artifacts") or []))
                and entry.get("extractionVersion", 0) < EXTRACTION_VERSION)
            or entry.get("sourceURL") != cask.get("url")
            or (checksum not in (None, "no_check") and checksum != entry.get("artifactSHA256")))


def record_extraction(cask: dict, root: Path, artifact: Path, output: Path, *,
                      package_roots: tuple[Path, ...], diagnostics: tuple[dict, ...]) -> None:
    manifest = load_manifest(output / MANIFEST)
    entry = extract_identities(cask, root, artifact, package_roots=package_roots, diagnostics=diagnostics)
    manifest["casks"][cask["token"]] = entry
    write_json(output / MANIFEST, manifest, trailing_newline=True)
    for diagnostic in entry["diagnostics"]:
        print(f"  identity {cask['token']}: {diagnostic['artifact']} - {diagnostic['reason']}", flush=True)


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
    projected = {"bundleName": name, "bundleIdentifier": identifier}
    if "packageIdentifier" in record or "installedPath" in record:
        package_id = record.get("packageIdentifier")
        installed = record.get("installedPath")
        if not isinstance(package_id, str) or not package_id or not isinstance(installed, str):
            raise ValueError(f"Incomplete package identity for {token}")
        path = PurePosixPath(installed)
        if (not path.is_relative_to("/Applications") or ".." in path.parts or path.name != name
                or any(part.endswith(".app") for part in path.parts[:-1])):
            raise ValueError(f"Invalid package app path for {token}")
        projected.update(packageIdentifier=package_id, installedPath=installed)
    return projected


def compose_release(categories: Path, extracted: Path, variants: Path, output: Path,
                    seed: Path | None = None) -> None:
    """Keep provenance in the manifest and embed its matching projection in categories."""
    catalog = json.loads(categories.read_text(encoding="utf-8"))
    manifest = load_manifest(seed) if seed is not None else {"schemaVersion": 1, "casks": {}}
    manifest["casks"].update(load_manifest(extracted)["casks"])
    reviewed = load_manifest(variants)["casks"]
    identities = {}
    for token in sorted(manifest["casks"].keys() | reviewed.keys()):
        # Identity coverage must not wait for the separately reviewed category catalog.
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
