import json
import subprocess

import extract_icons
import extract_tap_icons as tap_icons


def git_repo(path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    return git


def test_icon_path_lives_under_taps():
    assert tap_icons.icon_path("saffsanity/sill/sill") == "taps/saffsanity/sill/sill.png"


def test_pending_skips_aliased_published_and_parked_casks():
    collected = {
        "casks": {ref: {"token": ref.split("/")[-1], "full_token": ref} for ref in
                  ["u/r/new", "u/r/moved", "u/r/done", "u/r/none", "u/r/retry", "u/r/broken"]},
        "sameAs": {"u/r/moved": "moved"},
    }
    report = {
        "u/r/none": {"status": "no_icon"},
        "u/r/retry": {"status": "failed", "attempts": 1},
        "u/r/broken": {"status": "failed", "attempts": 3},
    }
    pending = tap_icons.pending(collected, {"u/r/done"}, report)
    assert [cask["full_token"] for cask in pending] == ["u/r/new", "u/r/retry"]


def test_manifest_lists_tap_icons_separately(tmp_path):
    git = git_repo(tmp_path)
    (tmp_path / "figma.png").write_bytes(b"official")
    (tmp_path / "taps" / "u" / "r").mkdir(parents=True)
    (tmp_path / "taps" / "u" / "r" / "figma.png").write_bytes(b"tap")
    git("add", "-A")

    extract_icons.write_icon_manifest(tmp_path)

    manifest = json.loads((tmp_path / "icons.json").read_text(encoding="utf-8"))
    assert set(manifest["hashes"]) == {"figma"}
    assert set(manifest["taps"]) == {"u/r/figma"}
    assert manifest["hashes"]["figma"] != manifest["taps"]["u/r/figma"]


def test_stage_never_writes_outside_taps(tmp_path):
    out = tmp_path / "out"
    (out / "taps" / "u" / "r").mkdir(parents=True)
    png = out / "taps" / "u" / "r" / "figma.png"
    png.write_bytes(b"tap")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "figma.png").write_bytes(b"official")

    tap_icons.stage(wt, {"u/r/figma": png}, {"u/r/figma": {"status": "ok"}})

    assert (wt / "figma.png").read_bytes() == b"official"
    assert (wt / "taps" / "u" / "r" / "figma.png").read_bytes() == b"tap"
    assert json.loads((wt / "taps" / "icon_report.json").read_text(encoding="utf-8")) == {"u/r/figma": {"status": "ok"}}
