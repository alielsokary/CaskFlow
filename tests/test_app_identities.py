"""Identity extraction must not turn icon-selection guesses into ownership evidence."""
#  Created by Ali Elsokary on 06/09/2026.
#  Copyright © 2026 BuildingLink. All rights reserved.
import hashlib
import json
import plistlib

import pytest

from app_identities import (MANIFEST, compose_release, extract_identities,
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
                      "apps": [{"bundleName": "Renamed.app", "bundleIdentifier": "com.example.original"}]}


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
    assert not needs_refresh(source, entry)  # a verified empty result is not retried forever
    assert needs_refresh({**source, "sha256": "different"}, entry)
    assert needs_refresh({**source, "url": "https://vendor.example/new.zip"}, entry)


def test_identity_only_batch_is_flushed_without_a_png(tmp_path, monkeypatch):
    import extract_icons
    calls = []
    monkeypatch.setattr(extract_icons, "FLUSH_EVERY", 1)
    monkeypatch.setattr(extract_icons, "publish_batch", lambda *args: calls.append(args))
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
