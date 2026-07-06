"""Tests for extract_icons pure helpers — eligibility, container detection, icon naming."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_icons import (  # noqa: E402
    app_names_from_artifacts,
    container_type,
    eligibility,
    resolve_icns_name,
)


def _cask(**kw) -> dict:
    return {"token": "test", "url": "https://example.com/Test.dmg",
            "artifacts": [{"app": ["Test.app"]}], **kw}


# --- eligibility -----------------------------------------------------------

def test_app_cask_is_eligible():
    assert eligibility(_cask()) is None


def test_deprecated_and_disabled_are_skipped():
    assert eligibility(_cask(deprecated=True)) == "deprecated/disabled"
    assert eligibility(_cask(disabled=True)) == "deprecated/disabled"


def test_non_app_cask_is_no_icon():
    qlplugin = _cask(artifacts=[{"qlplugin": ["Foo.qlgenerator"]}, {"zap": {"trash": []}}])
    assert eligibility(qlplugin) == "no app/suite/pkg artifact"


def test_suite_counts_as_app():
    assert eligibility(_cask(artifacts=[{"suite": ["Office Suite"]}])) is None


def test_pkg_installer_cask_is_eligible():
    zoomish = _cask(artifacts=[{"uninstall": [{}]}, {"pkg": ["Zoom.pkg"]}, {"zap": {}}])
    assert eligibility(zoomish) is None


def test_app_names_handles_target_dicts_and_strings():
    cask = _cask(artifacts=[{"app": ["A.app", {"target": "B.app"}]}, {"suite": ["S"]}])
    assert app_names_from_artifacts(cask) == ["A.app", "B.app", "S"]


# --- container detection ----------------------------------------------------

def test_container_type_from_extension():
    assert container_type({}, "Foo-1.2.dmg") == "dmg"
    assert container_type({}, "Foo.ZIP") == "zip"
    assert container_type({}, "Foo.pkg") == "pkg"
    assert container_type({}, "foo-x86.tar.xz") == "tar"
    assert container_type({}, "installer.exe") is None


def test_container_hint_wins_over_extension():
    # dmg-in-zip style: the cask's `container` field names the outer type.
    assert container_type({"container": {"type": "dmg"}}, "download.php") == "dmg"


# --- icns name resolution ----------------------------------------------------

def test_resolve_icns_name_appends_extension():
    assert resolve_icns_name({"CFBundleIconFile": "AppIcon"}) == "AppIcon.icns"
    assert resolve_icns_name({"CFBundleIconFile": "AppIcon.icns"}) == "AppIcon.icns"


def test_resolve_icns_name_missing_or_blank():
    assert resolve_icns_name({}) is None
    assert resolve_icns_name({"CFBundleIconFile": "  "}) is None
