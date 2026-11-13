import json
import subprocess
from pathlib import Path

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

    tap_icons.stage(wt, {"u/r/figma": png}, {"u/r/figma": {"status": "ok"}}, ["u/r"])

    assert (wt / "figma.png").read_bytes() == b"official"
    assert (wt / "taps" / "u" / "r" / "figma.png").read_bytes() == b"tap"
    assert json.loads((wt / "taps" / "icon_report.json").read_text(encoding="utf-8")) == {"u/r/figma": {"status": "ok"}}


def test_download_view_is_https_public_and_drops_custom_headers(monkeypatch):
    monkeypatch.setattr(tap_icons, "is_public_url", lambda url, schemes: url.startswith("https://good"))
    cask = {"token": "t", "full_token": "u/r/t", "url": "https://good.example/t.dmg",
            "url_specs": {"user_agent": "UA", "referer": "https://good.example", "header": ["Metadata: true"],
                          "cookies": {"a": "b"}}}

    safe = tap_icons.download_view(cask)

    assert safe["url_specs"] == {"user_agent": "UA", "referer": "https://good.example"}
    assert safe["https_only"] is True
    assert tap_icons.download_view({**cask, "url": "http://169.254.169.254/x"}) is None


def test_https_only_downloads_refuse_plain_http_redirects(tmp_path):
    cmd = extract_icons._curl_cmd({"https_only": True}, tmp_path / "a")
    assert cmd[cmd.index("--proto") + 1] == "=https"
    assert cmd[cmd.index("--proto-redir") + 1] == "=https"
    assert "--proto" not in extract_icons._curl_cmd({}, tmp_path / "a")


def test_stage_refuses_paths_that_escape_taps(tmp_path):
    import pytest
    png = tmp_path / "x.png"
    png.write_bytes(b"x")
    wt = tmp_path / "wt"
    wt.mkdir()
    with pytest.raises(ValueError):
        tap_icons.stage(wt, {"../../outside": png}, {}, ["u/r"])
    assert not (tmp_path / "outside.png").exists()


def test_extract_writes_pngs_and_report_for_a_separate_publish_job(tmp_path, monkeypatch):
    collected = tmp_path / "tap_casks.json"
    cask = {"token": "t", "full_token": "u/r/t", "url": "https://good.example/t.dmg"}
    collected.write_text(json.dumps({"casks": {"u/r/t": cask}}), encoding="utf-8")
    monkeypatch.setattr(tap_icons, "load_official", lambda: {})
    monkeypatch.setattr(tap_icons, "_branch_state", lambda: (set(), {}))
    monkeypatch.setattr(tap_icons, "is_public_url", lambda url, schemes: True)

    def fake_extract(cask, folder):
        (folder / f"{cask['token']}.png").write_bytes(b"png")
        return "ok", "single"

    monkeypatch.setattr(tap_icons.extract_icons, "_extract_status", fake_extract)
    out = tmp_path / "out"

    assert tap_icons.main(["--collected", str(collected), "--taps", "u/r", "--output-dir", str(out)]) == 0
    assert (out / "taps" / "u" / "r" / "t.png").read_bytes() == b"png"
    assert json.loads((out / "taps" / "icon_report.json").read_text(encoding="utf-8")) == {}


def test_publish_from_keeps_only_the_runs_taps(tmp_path, monkeypatch):
    for ref in ("u/r/keep", "evil/tap/figma"):
        (tmp_path / "taps" / Path(ref).parent).mkdir(parents=True, exist_ok=True)
        (tmp_path / "taps" / f"{ref}.png").write_bytes(make_png(16, 16))
    (tmp_path / "taps" / "icon_report.json").write_text(
        '{"u/r/gone": {"status": "no_icon"}, "evil/tap/x": {"status": "no_icon"}}', encoding="utf-8")
    seen = {}
    monkeypatch.setattr(tap_icons, "publish", lambda pngs, report, taps, run: seen.update(pngs=set(pngs), report=set(report), taps=taps))

    tap_icons.publish_from(tmp_path, ["u/r"], None)

    assert seen == {"pngs": {"u/r/keep"}, "report": {"u/r/gone"}, "taps": ["u/r"]}


def test_stage_replaces_only_the_runs_taps_in_the_branch_report(tmp_path):
    (tmp_path / "taps").mkdir()
    (tmp_path / "taps" / "icon_report.json").write_text(json.dumps({
        "other/tap/x": {"status": "no_icon"},
        "u/r/cleared": {"status": "failed", "attempts": 2},
    }), encoding="utf-8")

    tap_icons.stage(tmp_path, {}, {"u/r/new": {"status": "car_only"}}, ["u/r"])

    assert json.loads((tmp_path / "taps" / "icon_report.json").read_text(encoding="utf-8")) == {
        "other/tap/x": {"status": "no_icon"},
        "u/r/new": {"status": "car_only"},
    }


def test_publish_from_without_extraction_output_publishes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(tap_icons, "publish", lambda *args: (_ for _ in ()).throw(AssertionError("published")))

    tap_icons.publish_from(tmp_path, ["u/r"], None)


def test_icon_push_replays_onto_a_concurrent_writer(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.check_call(["git", "init", "-q", "--bare", str(origin)])
    seed = tmp_path / "seed"
    git = git_repo(seed)
    (seed / "a.png").write_bytes(b"a")
    git("add", "-A")
    extract_icons.write_icon_manifest(seed)
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    git("push", "-q", str(origin), "HEAD:icons")

    def clone(name):
        path = tmp_path / name
        subprocess.check_call(["git", "clone", "-q", "-b", "icons", str(origin), str(path)])
        for key, value in (("user.name", "Test"), ("user.email", "test@example.com")):
            subprocess.check_call(["git", "-C", str(path), "config", key, value])
        return path

    official, taps = clone("official"), clone("taps")
    (official / "b.png").write_bytes(b"b")
    subprocess.check_call(["git", "-C", str(official), "add", "-A"])
    extract_icons.write_icon_manifest(official)
    subprocess.check_call(["git", "-C", str(official), "add", "-A"])
    extract_icons._commit_and_push(official, {"b": official / "b.png"})

    (taps / "taps" / "u" / "r").mkdir(parents=True)
    (taps / "taps" / "u" / "r" / "t.png").write_bytes(b"t")
    subprocess.check_call(["git", "-C", str(taps), "add", "-A"])
    extract_icons.write_icon_manifest(taps)
    subprocess.check_call(["git", "-C", str(taps), "add", "-A"])
    extract_icons._commit_and_push(taps, {"u/r/t": taps / "taps" / "u" / "r" / "t.png"})

    def show(*args):
        return subprocess.check_output(["git", "-C", str(origin), *args], text=True)
    assert set(show("ls-tree", "-r", "--name-only", "icons").split()) == {"a.png", "b.png", "icons.json", "taps/u/r/t.png"}
    manifest = json.loads(show("show", "icons:icons.json"))
    assert set(manifest["hashes"]) == {"a", "b"} and set(manifest["taps"]) == {"u/r/t"}


def test_stage_deletes_icons_and_report_entries_that_are_no_longer_current(tmp_path):
    git = git_repo(tmp_path)
    for ref in ("u/r/old", "u/r/keep", "g/t/x", "o/t/z"):
        (tmp_path / "taps" / Path(ref).parent).mkdir(parents=True, exist_ok=True)
        (tmp_path / "taps" / f"{ref}.png").write_bytes(ref.encode())
    (tmp_path / "taps" / "icon_report.json").write_text(json.dumps({
        "u/r/old": {"status": "no_icon"}, "g/t/x": {"status": "no_icon"}, "o/t/z": {"status": "no_icon"},
    }), encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")

    run = {"current": {"u/r": ["u/r/keep"], "../evil": ["../evil/x"]}, "prunedTaps": ["g/t", ".."]}
    tap_icons.stage(tmp_path, {}, {}, ["u/r", "g/t"], run)
    git("add", "-A", "--", "taps")
    extract_icons.write_icon_manifest(tmp_path)

    left = sorted(str(p.relative_to(tmp_path)) for p in (tmp_path / "taps").rglob("*.png"))
    assert left == ["taps/o/t/z.png", "taps/u/r/keep.png"]
    assert json.loads((tmp_path / "taps" / "icon_report.json").read_text(encoding="utf-8")) == {"o/t/z": {"status": "no_icon"}}
    assert set(json.loads((tmp_path / "icons.json").read_text(encoding="utf-8"))["taps"]) == {"o/t/z", "u/r/keep"}


def test_publish_from_with_only_removals_publishes(tmp_path, monkeypatch):
    run_file = tmp_path / "tap_run.json"
    run_file.write_text(json.dumps({"readable": [], "current": {}, "prunedTaps": ["g/t"]}), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(tap_icons, "publish", lambda pngs, report, taps, run: seen.update(run=run, taps=taps))

    tap_icons.publish_from(tmp_path / "missing", ["g/t"], run_file)

    assert seen == {"run": {"current": {}, "prunedTaps": ["g/t"]}, "taps": ["g/t"]}


import struct  # noqa: E402
import zlib  # noqa: E402

import pytest  # noqa: E402


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def make_png(width, height, color_type=6, bit_depth=8, interlace=0, extra=b"", idat=None, first=None):
    channels = 4 if color_type == 6 else 3
    raw = b"".join(b"\x00" + bytes(width * channels) for _ in range(height))
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace))
    body = (first or b"") + ihdr + _chunk(b"gAMA", b"\x00\x00\xb1\x8f")
    body += _chunk(b"IDAT", zlib.compress(raw) if idat is None else idat) + _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body + extra


@pytest.mark.parametrize("png", [make_png(256, 256), make_png(256, 200), make_png(64, 64, color_type=2)])
def test_valid_pngs_pass(png):
    assert tap_icons.png_problem(png) is None


def _bad_crc():
    png = bytearray(make_png(32, 32))
    png[20] ^= 1
    return bytes(png)


@pytest.mark.parametrize("png", [
    b"GIF89a" + bytes(40),
    make_png(32, 32)[:-10],
    _bad_crc(),
    make_png(32, 32, first=_chunk(b"gAMA", b"\x00\x00\xb1\x8f")),
    make_png(0, 32),
    make_png(300, 32),
    make_png(32, 32, bit_depth=16),
    make_png(32, 32, interlace=1),
    make_png(32, 32, idat=zlib.compress(bytes(10))),
    make_png(32, 32, extra=b"junk"),
    make_png(32, 32, first=_chunk(b"tEXt", bytes(1024 * 1024 + 1))),
])
def test_bad_pngs_are_refused(png):
    assert tap_icons.png_problem(png) is not None


def test_publish_from_refuses_invalid_icons(tmp_path, monkeypatch):
    (tmp_path / "taps" / "u" / "r").mkdir(parents=True)
    (tmp_path / "taps" / "u" / "r" / "good.png").write_bytes(make_png(64, 64))
    (tmp_path / "taps" / "u" / "r" / "bad.png").write_bytes(b"<html>not an image</html>")
    (tmp_path / "taps" / "icon_report.json").write_text("{}", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(tap_icons, "publish", lambda pngs, report, taps, run: seen.update(pngs=set(pngs), report=report))

    tap_icons.publish_from(tmp_path, ["u/r"], None)

    assert seen["pngs"] == {"u/r/good"}
    assert seen["report"]["u/r/bad"]["status"] == "failed" and seen["report"]["u/r/bad"]["reason"].startswith("invalid png")
