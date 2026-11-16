"""Tests for the hand-picked Browse hero."""
import json

import pytest

import set_house_pick

KNOWN = {"raycast", "iterm2", "old-app", "font-fira-code"}
EM_DASH = chr(0x2014)


def _fetch(token):
    return {"old-app": {"deprecated": True}}.get(token, {"deprecated": False, "disabled": False})


def test_valid_pick_is_normalized():
    pick = set_house_pick.build_pick("  Raycast ", f"  Fast {EM_DASH} launcher\n ", KNOWN, _fetch)
    assert pick == {"token": "raycast", "note": "Fast - launcher"}


def test_blank_note_becomes_null():
    assert set_house_pick.build_pick("iterm2", "   ", KNOWN, _fetch)["note"] is None


@pytest.mark.parametrize(("token", "reason"), [
    ("../etc", "not a cask token"),
    ("font-fira-code", "font"),
    ("unknown-app", "not in categories.json"),
    ("old-app", "deprecated"),
])
def test_invalid_tokens_are_rejected(token, reason):
    with pytest.raises(ValueError, match=reason):
        set_house_pick.build_pick(token, None, KNOWN, _fetch)


def test_missing_homebrew_cask_is_rejected():
    with pytest.raises(ValueError, match="does not exist"):
        set_house_pick.build_pick("raycast", None, KNOWN, lambda _: None)


def test_long_note_is_rejected():
    with pytest.raises(ValueError, match="limit is 120"):
        set_house_pick.build_pick("raycast", "x" * 121, KNOWN, _fetch)


def test_main_writes_pick_file(tmp_path, monkeypatch):
    categories = tmp_path / "categories.json"
    categories.write_text(json.dumps({"tokenToCategory": {"raycast": {}}}), encoding="utf-8")
    pick = tmp_path / "house_pick.json"
    monkeypatch.setattr(set_house_pick, "CATEGORIES", categories)
    monkeypatch.setattr(set_house_pick, "PICK", pick)
    monkeypatch.setattr(set_house_pick, "fetch_cask", _fetch)

    assert set_house_pick.main(["--token", "raycast", "--note", "Launcher"]) == 0
    assert pick.read_text(encoding="utf-8") == '{\n  "token": "raycast",\n  "note": "Launcher"\n}\n'
    assert set_house_pick.main(["--token", "missing"]) == 1


def test_committed_pick_is_publishable():
    data = json.loads((set_house_pick.PICK).read_text(encoding="utf-8"))
    known = set(json.loads(set_house_pick.CATEGORIES.read_text(encoding="utf-8"))["tokenToCategory"])
    assert set(data) == {"token", "note"}
    assert data["token"] in known and not data["token"].startswith("font-")
