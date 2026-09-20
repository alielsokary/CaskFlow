"""Identity extraction must not turn icon-selection guesses into ownership evidence."""
#  Created by Ali Elsokary on 06/09/2026.
#  Copyright © 2026 BuildingLink. All rights reserved.
import hashlib
import json
import plistlib

import pytest

from app_identities import (EXTRACTION_VERSION, MANIFEST, compose_release, extract_identities,
                            load_manifest, merge_extractions)
from style_standards import write_json


def app(root, name, identifier):
    bundle = root / name
    (bundle / "Contents").mkdir(parents=True)
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier}))
    return bundle


def cask():
    return {"token": "sample", "version": "1", "url": "https://vendor.example/app.zip",
            "artifacts": [{"app": ["Original.app", {"target": "Renamed.app"}]}]}


def test_exact_artifact_rename_and_checksum_are_recorded(tmp_path):
    app(tmp_path, "Original.app", "com.example.original")
    app(tmp_path / "Original.app/Contents", "Helper.app", "com.example.helper")
    archive = tmp_path / "app.zip"
    archive.write_bytes(b"archive")
    result = extract_identities(cask(), tmp_path, archive)
    assert result == {"caskVersion": "1", "sourceURL": cask()["url"],
                      "artifactSHA256": hashlib.sha256(b"archive").hexdigest(),
                      "apps": [{"bundleName": "Renamed.app", "bundleIdentifier": "com.example.original"}],
                      "extractionVersion": EXTRACTION_VERSION, "diagnostics": []}


@pytest.mark.parametrize("duplicate", [False, True])
def test_canonically_equivalent_artifact_paths_match_but_duplicates_are_rejected(tmp_path, duplicate):
    app(tmp_path / "one", "Re\u0301lease/U\u0308bersicht.app", "tracesOf.Uebersicht")
    if duplicate:
        app(tmp_path / "two", "R\u00e9lease/\u00dcbersicht.app", "com.example.other")
    source = {**cask(), "artifacts": [{"app": ["R\u00e9lease/\u00dcbersicht.app"]}]}
    archive = tmp_path / "app.zip"
    archive.write_bytes(b"archive")
    expected = [] if duplicate else [
        {"bundleName": "\u00dcbersicht.app", "bundleIdentifier": "tracesOf.Uebersicht"}]
    assert extract_identities(source, tmp_path, archive)["apps"] == expected


@pytest.mark.parametrize("encoding", ["doctype-only", "binary", "malformed"])
def test_identity_plist_formats_and_malformed_metadata(tmp_path, encoding):
    bundle = app(tmp_path, "Original.app", "io.github.wickenico.wailbrew")
    info = bundle / "Contents/Info.plist"
    if encoding == "doctype-only":
        info.write_bytes(info.read_bytes().split(b"\n", 1)[1])
    elif encoding == "binary":
        info.write_bytes(plistlib.dumps({"CFBundleIdentifier": "io.github.wickenico.wailbrew"},
                                       fmt=plistlib.FMT_BINARY))
    else:
        info.write_bytes(b"<plist><dict><key>CFBundleIdentifier</key><string>broken</dict></plist>")
    archive = tmp_path / "app.zip"
    archive.write_bytes(b"archive")
    expected = [] if encoding == "malformed" else [
        {"bundleName": "Renamed.app", "bundleIdentifier": "io.github.wickenico.wailbrew"}]
    assert extract_identities(cask(), tmp_path, archive)["apps"] == expected


@pytest.mark.parametrize("case", ["wrong-name", "duplicate", "symlink", "plist-symlink", "empty-id"])
def test_ambiguous_or_untrusted_bundles_produce_no_identity(tmp_path, case):
    archive = tmp_path / "app.zip"
    archive.write_bytes(b"archive")
    if case == "wrong-name":
        app(tmp_path, "Similar.app", "com.example.similar")
    elif case == "duplicate":
        app(tmp_path / "one", "Original.app", "com.example.one")
        app(tmp_path / "two", "Original.app", "com.example.two")
    elif case == "symlink":
        app(tmp_path, "Elsewhere.app", "com.example.elsewhere")
        (tmp_path / "Original.app").symlink_to(tmp_path / "Elsewhere.app", target_is_directory=True)
    elif case == "plist-symlink":
        other = app(tmp_path, "Other.app", "com.example.other")
        target = tmp_path / "Original.app/Contents"
        target.mkdir(parents=True)
        (target / "Info.plist").symlink_to(other / "Contents/Info.plist")
    else:
        app(tmp_path, "Original.app", "")
    assert extract_identities(cask(), tmp_path, archive)["apps"] == []


def test_identity_publication_merges_only_successfully_inspected_dirty_tokens(tmp_path):
    destination, source = tmp_path / "published.json", tmp_path / "incoming.json"
    write_json(destination, {"schemaVersion": 1, "casks": {"old": {"apps": [1]}, "failed": {"apps": [2]}}})
    write_json(source, {"schemaVersion": 1, "casks": {"old": {"apps": []}, "not-dirty": {"apps": [3]}}})
    merge_extractions(destination, source, {"old", "failed"})
    assert load_manifest(destination)["casks"] == {"old": {"apps": []}, "failed": {"apps": [2]}}


def test_release_projects_reviewed_variants_and_revokes_removed_aliases(tmp_path):
    categories, extracted, variants = [tmp_path / name for name in ["categories.json", "extracted.json", "variants.json"]]
    direct = {"bundleName": "Mail.app", "bundleIdentifier": "com.example.mail"}
    store = {"bundleName": "Mail.app", "bundleIdentifier": "com.example.mail.store", "evidence": "Vendor docs"}
    write_json(categories, {"tokenToCategory": {"mail": {"primary": "communication"}}})
    write_json(extracted, {"schemaVersion": 1, "casks": {"mail": {"apps": [direct], "caskVersion": "1"}}})
    write_json(variants, {"schemaVersion": 1, "casks": {"mail": [store]}})
    output = tmp_path / MANIFEST
    compose_release(categories, extracted, variants, output)
    catalog = json.loads(categories.read_text())
    assert catalog["appIdentities"]["mail"] == [direct, {k: v for k, v in store.items() if k != "evidence"}]
    assert "metadataUpdatedAt" in catalog
    assert load_manifest(output)["casks"]["mail"]["reviewedVariants"] == [store]
    write_json(variants, {"schemaVersion": 1, "casks": {}})
    compose_release(categories, output, variants, output)
    assert json.loads(categories.read_text())["appIdentities"]["mail"] == [direct]


def test_extraction_records_identity_even_when_app_has_no_extractable_icon(tmp_path, monkeypatch):
    import extract_icons
    root, output = tmp_path / "expanded", tmp_path / "output"
    bundle = app(root, "Original.app", "com.example.original")
    output.mkdir()
    archive = root / "app.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(extract_icons.tempfile, "mkdtemp", lambda **kw: str(root))
    monkeypatch.setattr(extract_icons, "download", lambda *args: archive)
    monkeypatch.setattr(extract_icons, "expand", lambda *args: root)
    monkeypatch.setattr(extract_icons, "_locate_app", lambda *args: (bundle, "exact"))
    monkeypatch.setattr(extract_icons, "_icon_status", lambda *args: ("no_icon", "none"))
    assert extract_icons.extract_one(cask(), output) == ("no_icon", "none")
    assert load_manifest(output / MANIFEST)["casks"]["sample"]["apps"][0]["bundleIdentifier"] == "com.example.original"


def test_backfill_revisits_missing_changed_sources_and_changed_checksums():
    from app_identities import needs_refresh
    source = cask()
    source["sha256"] = "abc"
    entry = {"caskVersion": "1", "sourceURL": source["url"], "artifactSHA256": "abc", "apps": []}
    assert needs_refresh(source, None)
    assert needs_refresh(source, entry)  # reinspect old empty results once with the improved extractor
    entry["extractionVersion"] = EXTRACTION_VERSION
    assert not needs_refresh(source, entry)  # a verified empty result is not retried forever
    assert needs_refresh({**source, "sha256": "different"}, entry)
    assert needs_refresh({**source, "url": "https://vendor.example/new.zip"}, entry)


def test_identity_only_batch_is_flushed_without_a_png(tmp_path, monkeypatch):
    import extract_icons
    calls = []
    monkeypatch.setattr(extract_icons, "FLUSH_EVERY", 1)
    monkeypatch.setattr(extract_icons, "publish_batch", lambda *args, **kw: calls.append(args))
    dirty = {"no-icon"}
    extract_icons._flush_if_due({}, {}, dirty, tmp_path / MANIFEST)
    assert len(calls) == 1
    assert calls[0][3] == tmp_path / MANIFEST
    assert not dirty


def test_first_partial_publication_keeps_seed_and_honors_explicit_revocation(tmp_path):
    categories, seed, extracted, variants, output = [tmp_path / name for name in
        ["categories.json", "seed.json", "extracted.json", "variants.json", "output.json"]]
    identity = {"bundleName": "Known.app", "bundleIdentifier": "org.example.known"}
    write_json(categories, {"tokenToCategory": {"known": {}, "new": {}}})
    write_json(seed, {"schemaVersion": 1, "casks": {"known": {"apps": [identity]}}})
    write_json(extracted, {"schemaVersion": 1, "casks": {"new": {"apps": [identity]}}})
    write_json(variants, {"schemaVersion": 1, "casks": {}})
    compose_release(categories, extracted, variants, output, seed)
    assert set(json.loads(categories.read_text())["appIdentities"]) == {"known", "new"}
    write_json(extracted, {"schemaVersion": 1, "casks": {"known": {"apps": []}}})
    compose_release(categories, extracted, variants, output, seed)
    assert not json.loads(categories.read_text())["appIdentities"]


def test_release_keeps_identities_before_category_classification(tmp_path):
    categories, extracted, variants, output = [tmp_path / name for name in
        ["categories.json", "extracted.json", "variants.json", "output.json"]]
    identity = {"bundleName": "New.app", "bundleIdentifier": "com.example.new"}
    write_json(categories, {"tokenToCategory": {"known": {}}, "totalCasks": 1})
    write_json(extracted, {"schemaVersion": 1, "casks": {"new": {"apps": [identity]}}})
    write_json(variants, {"schemaVersion": 1, "casks": {}})
    compose_release(categories, extracted, variants, output)
    catalog = json.loads(categories.read_text())
    assert catalog["appIdentities"] == {"new": [identity]}
    assert catalog["tokenToCategory"] == {"known": {}}  # no invented classification
    assert catalog["totalCasks"] == 1


def test_case_equivalent_artifact_name_and_empty_diagnostics(tmp_path):
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    bundle = app(tmp_path, "original.app", "com.example.actual")
    result = extract_identities(cask(), tmp_path, archive)
    assert result["apps"] == [{"bundleName": "Renamed.app", "bundleIdentifier": "com.example.actual"}]
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "invalid_id"}))
    result = extract_identities(cask(), tmp_path, archive)
    assert result["apps"] == []
    assert result["diagnostics"] == [{"artifact": "Renamed.app", "reason": "unsupported CFBundleIdentifier: 'invalid_id'"}]


@pytest.mark.parametrize("identifier", ["blockbench", "AigcPanel", "com.example.app",
                                       "", "invalid_id", "white space", "path/app", "trailing\n"])
def test_identifier_validation_from_extraction_through_release(tmp_path, identifier):
    app(tmp_path, "Original.app", identifier)
    archive = tmp_path / "app.zip"
    archive.write_bytes(b"archive")
    result = extract_identities(cask(), tmp_path, archive)
    valid = identifier in {"blockbench", "AigcPanel", "com.example.app"}
    expected = [{"bundleName": "Renamed.app", "bundleIdentifier": identifier}] if valid else []
    assert result["apps"] == expected
    assert bool(result["diagnostics"]) == (not valid)
    categories, extracted, variants, output = [tmp_path / name for name in
        ["categories.json", "extracted.json", "variants.json", "output.json"]]
    write_json(categories, {"tokenToCategory": {}})
    write_json(extracted, {"schemaVersion": 1, "casks": {"sample": result}})
    write_json(variants, {"schemaVersion": 1, "casks": {}})
    compose_release(categories, extracted, variants, output)
    assert json.loads(categories.read_text())["appIdentities"] == ({"sample": expected} if valid else {})


@pytest.mark.parametrize("case", ["valid", "ambiguous", "staged", "unsafe-target"])
def test_suite_uses_only_declared_directory_and_excludes_embedded_helpers(tmp_path, case):
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    app(tmp_path / "Suite", "One.app", "com.example.one")
    app(tmp_path / "Suite/One.app/Contents", "Helper.app", "com.example.helper")
    app(tmp_path / "Suite", "Two.app", "com.example.two")
    app(tmp_path, "Unrelated.app", "com.example.unrelated")
    stanza = {"suite": ["Suite", {"target": "Renamed Suite"}]}
    if case == "ambiguous":
        app(tmp_path / "other/Suite", "One.app", "com.example.other")
    elif case == "staged":
        stanza = {"suite": ["$HOMEBREW_PREFIX/Caskroom/sample/1"]}
    elif case == "unsafe-target":
        stanza = {"suite": ["Suite", {"target": "$HOMEBREW_PREFIX/share/suite"}]}
    result = extract_identities({**cask(), "artifacts": [stanza]}, tmp_path, archive)
    assert {a["bundleIdentifier"] for a in result["apps"]} == (
        {"com.example.one", "com.example.two"} if case == "valid" else set())
    assert bool(result["diagnostics"]) == (case != "valid")


@pytest.mark.parametrize("location", ["/", "/Applications", "/Applications/Installed.app"])
def test_real_package_payload_and_nested_archive_through_extraction(tmp_path, monkeypatch, location):
    import shutil
    import subprocess
    import zipfile
    import extract_icons

    if shutil.which("pkgbuild") is None:
        pytest.skip("macOS package tools required")
    payload = tmp_path / "payload"
    if location.endswith(".app"):
        bundle = app(tmp_path, "Installed.app", "com.example.installed")
        shutil.copytree(bundle, payload)
        app(payload / "Contents/Library/LoginItems", "Helper.app", "com.example.helper")
    else:
        app(payload / ("Applications" if location == "/" else ""), "Installed.app", "com.example.installed")
        helper_root = payload / ("Library" if location == "/" else "Installed.app/Contents/Library")
        app(helper_root, "Helper.app", "com.example.helper")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    marker = tmp_path / "installer-was-run"
    postinstall = scripts / "postinstall"
    postinstall.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    postinstall.chmod(0o755)
    package = tmp_path / "Installer.pkg"
    subprocess.run(["pkgbuild", "--root", str(payload), "--identifier", "com.example.pkg",
                    "--install-location", location, "--scripts", str(scripts), str(package)],
                   check=True, capture_output=True)
    archive = package
    if location == "/Applications":
        archive = tmp_path / "wrapper.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(package, "Installer.pkg")
    source = {**cask(), "url": archive.as_uri(), "artifacts": [{"pkg": ["Installer.pkg"]}]}
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(extract_icons, "download", lambda c, work: archive)
    monkeypatch.setattr(extract_icons, "_icon_status", lambda *a: ("no_icon", "fixture has no icon"))
    extract_icons.extract_one(source, output)
    entry = load_manifest(output / MANIFEST)["casks"]["sample"]
    assert entry["apps"] == [{"bundleName": "Installed.app", "bundleIdentifier": "com.example.installed"}]
    assert entry["diagnostics"] == []
    assert not marker.exists()


@pytest.mark.parametrize("case", ["traversal", "library-helper", "payload-symlink", "malformed"])
def test_package_metadata_never_accepts_unsafe_locations_or_helpers(tmp_path, case):
    package = tmp_path / "expanded"
    package.mkdir()
    location = "/Applications/../Library" if case == "traversal" else "/Library"
    app(package / "Payload", "Helper.app", "com.example.helper")
    if case == "payload-symlink":
        location = "/Applications"
        (package / "Payload").rename(tmp_path / "elsewhere")
        (package / "Payload").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    (package / "PackageInfo").write_text("<broken" if case == "malformed" else
                                       f'<pkg-info install-location="{location}"/>')
    archive = tmp_path / "app.pkg"
    archive.write_bytes(b"archive")
    source = {**cask(), "artifacts": [{"pkg": ["app.pkg"]}]}
    result = extract_identities(source, package, archive, package_roots=(package,))
    assert result["apps"] == []
    assert result["diagnostics"]


def test_package_expansion_failure_does_not_publish_a_fresh_empty_record(tmp_path, monkeypatch):
    import extract_icons
    from icons_state import ExtractError

    root = tmp_path / "work"
    root.mkdir()
    package = root / "Installer.pkg"
    package.write_bytes(b"broken package")
    archive = root / "archive.zip"
    archive.write_bytes(b"archive")
    output = tmp_path / "output"
    output.mkdir()
    source = {**cask(), "artifacts": [{"pkg": ["Installer.pkg"]}]}
    monkeypatch.setattr(extract_icons.tempfile, "mkdtemp", lambda **kw: str(root))
    monkeypatch.setattr(extract_icons, "download", lambda *a: archive)
    monkeypatch.setattr(extract_icons, "expand", lambda *a: root)
    monkeypatch.setattr(extract_icons, "_locate_app", lambda *a: None)

    def fail(*args):
        raise ExtractError("pkgutil failed")

    monkeypatch.setattr(extract_icons, "_expand_pkg", fail)
    assert extract_icons._extract_status(source, output) == ("failed", "pkgutil failed")
    assert not (output / MANIFEST).exists()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_package_info_rejects_dtd_entities_before_interpreting_install_location(tmp_path, encoding):
    from app_identities import _package_location
    info = tmp_path / "PackageInfo"
    xml = f'''<?xml version="1.0" encoding="{encoding}"?>
<!DOCTYPE pkg-info [<!ENTITY target "/Applications/Fake.app">]>
<pkg-info install-location="&target;"/>'''
    info.write_bytes(xml.encode(encoding))
    with pytest.raises(ValueError, match="DTDs are not supported"):
        _package_location(info)
