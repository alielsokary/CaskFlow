import json

import pytest

import tap_registry
from tap_registry import RegistryError


@pytest.mark.parametrize("name", ["saffsanity/sill", "nikitabobko/tap", "a1/b-c.d_e"])
def test_valid_names(name):
    assert tap_registry.validate_name(name) is None


@pytest.mark.parametrize(
    "name",
    ["Homebrew/cask", "homebrew/foo", "a/b/c", "a;b/c", "$(x)/y", "User/repo", "user", "", "-a/b", "a/b "],
)
def test_invalid_names(name):
    assert tap_registry.validate_name(name) is not None


def write(tmp_path, taps):
    path = tmp_path / "taps.json"
    path.write_text(json.dumps({"version": 1, "taps": taps}), encoding="utf-8")
    return path


def test_load_returns_entries(tmp_path):
    entries = tap_registry.load(write(tmp_path, [{"name": "a/b"}, {"name": "c/d", "remote": "https://github.com/c/d"}]))
    assert [e["name"] for e in entries] == ["a/b", "c/d"]


@pytest.mark.parametrize(
    "taps",
    [
        [{"name": "a/b"}, {"name": "a/b"}],
        [{"name": "c/d"}, {"name": "a/b"}],
        [{"name": "homebrew/cask"}],
        [{"name": "a/b", "remote": "http://github.com/a/b"}],
        [{"name": "a/b", "remote": "https://gitlab.com/a/b"}],
        [{"name": "a/b", "extra": 1}],
    ],
)
def test_load_rejects_bad_registries(tmp_path, taps):
    with pytest.raises(RegistryError):
        tap_registry.load(write(tmp_path, taps))


def test_load_rejects_wrong_version(tmp_path):
    path = tmp_path / "taps.json"
    path.write_text(json.dumps({"version": 2, "taps": []}), encoding="utf-8")
    with pytest.raises(RegistryError):
        tap_registry.load(path)


def test_default_remote():
    assert tap_registry.default_remote("saffsanity/sill") == "https://github.com/saffsanity/homebrew-sill"
    assert tap_registry.remote_of({"name": "c/d", "remote": "https://github.com/c/d"}) == "https://github.com/c/d"
    assert tap_registry.remote_of({"name": "a/b"}) == "https://github.com/a/homebrew-b"


def test_parse_issue_title_takes_the_tap_from_a_plain_title():
    assert tap_registry.parse_issue_title("Add tap: saffsanity/sill") == "saffsanity/sill"
    assert tap_registry.parse_issue_title("  add TAP:  Saffsanity/Sill ") == "saffsanity/sill"


@pytest.mark.parametrize("title", [
    "Add tap: a/b; rm -rf ~",
    "Add tap: ",
    "Add tap: user/repo extra",
    "Add tap: homebrew/cask",
    "Add tap:\u00a0good/tap",
    "Add tap: good/tap\u200b",
    "Add tap: \u212aelvin/tap",
    "Tap: good/tap",
])
def test_parse_issue_title_refuses_anything_but_one_ascii_tap_name(title):
    assert tap_registry.parse_issue_title(title) is None


def test_with_tap_inserts_sorted_and_skips_existing():
    entries = [{"name": "a/b"}, {"name": "m/n"}]
    assert [e["name"] for e in tap_registry.with_tap(entries, "c/d")] == ["a/b", "c/d", "m/n"]
    assert tap_registry.with_tap(entries, "a/b") == entries


def test_repo_registry_is_valid():
    tap_registry.load(tap_registry.REGISTRY_PATH)


def test_add_registers_a_tap_sorted(tmp_path, monkeypatch):
    path = write(tmp_path, [{"name": "a/b"}, {"name": "m/n"}])
    monkeypatch.setattr(tap_registry, "REGISTRY_PATH", path)
    monkeypatch.setattr(tap_registry.load, "__defaults__", (path,))

    assert tap_registry.main(["--add", "c/d", "e/f", "a/b"]) == 0
    assert [entry["name"] for entry in tap_registry.load(path)] == ["a/b", "c/d", "e/f", "m/n"]



def test_only_readable_taps_are_registered(tmp_path, monkeypatch):
    path = write(tmp_path, [{"name": "m/n"}])
    monkeypatch.setattr(tap_registry, "REGISTRY_PATH", path)
    monkeypatch.setattr(tap_registry.load, "__defaults__", (path,))
    run = tmp_path / "tap_run.json"
    run.write_text(json.dumps({"readable": ["a/b"], "current": {}, "prunedTaps": []}), encoding="utf-8")

    assert tap_registry.main(["--add", "a/b", "c/d", "--only-readable", str(run)]) == 0
    assert [entry["name"] for entry in tap_registry.load(path)] == ["a/b", "m/n"]
