import json
import subprocess

import collect_tap_casks as collect


def cask(token, full, homepage, url, artifacts):
    return {"token": token, "full_token": full, "homepage": homepage, "url": url, "artifacts": artifacts}


OFFICIAL_FUSE = cask("fuse-t", "fuse-t", "https://www.fuse-t.org/",
                     "https://github.com/macos-fuse-t/fuse-t/releases/download/1.2.9/fuse-t-macos-installer-1.2.9.pkg",
                     [{"pkg": ["fuse-t-macos-installer-1.2.9.pkg"]}])
TAP_FUSE = cask("fuse-t", "macos-fuse-t/cask/fuse-t", "https://github.com/macos-fuse-t/fuse-t",
                "https://github.com/macos-fuse-t/fuse-t/releases/download/1.2.8/fuse-t-macos-installer-1.2.8.pkg",
                [{"pkg": ["fuse-t-macos-installer-1.2.8.pkg"]}])
OFFICIAL_ORCA = cask("orca", "orca", "https://github.com/plotly/orca/",
                     "https://github.com/plotly/orca/releases/download/v1.3.1/mac-release.zip", [{"app": ["orca.app"]}])
TAP_ORCA = cask("orca", "stablyai/orca/orca", "https://orca.dev",
                "https://github.com/stablyai/orca/releases/download/v2.0/Orca.dmg", [{"app": ["Orca.app"]}])
OFFICIAL_NGROK = cask("ngrok", "ngrok", "https://ngrok.com/", "https://bin.ngrok.com/a/x1/ngrok-v3-3.39-darwin.zip",
                      [{"binary": ["ngrok"]}])
TAP_NGROK = cask("ngrok", "ngrok/ngrok/ngrok", "https://ngrok.com", "https://bin.ngrok.com/a/y2/ngrok-v3-3.38-darwin.zip",
                 [{"binary": ["ngrok", {"target": "ngrok"}]}])


def test_same_app_matches_moved_casks():
    assert collect.same_app(TAP_FUSE, OFFICIAL_FUSE)
    assert collect.same_app(TAP_NGROK, OFFICIAL_NGROK)


def test_same_name_different_app_is_not_aliased():
    assert not collect.same_app(TAP_ORCA, OFFICIAL_ORCA)


def test_matching_homepage_alone_is_not_enough():
    other = cask("ngrok", "x/y/ngrok", "https://ngrok.com", "https://bin.ngrok.com/z.zip", [{"app": ["Other.app"]}])
    assert not collect.same_app(other, OFFICIAL_NGROK)


def test_aliases_only_for_matching_twins():
    official = {c["token"]: c for c in (OFFICIAL_FUSE, OFFICIAL_ORCA)}
    casks = {c["full_token"]: c for c in (TAP_FUSE, TAP_ORCA)}
    assert collect.aliases(casks, official) == {"macos-fuse-t/cask/fuse-t": "fuse-t"}


def fake_brew(responses):
    calls = []

    def run(cmd):
        calls.append(cmd)
        code, out = responses.get(tuple(cmd[1:3]), (0, ""))
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr="boom" if code else "")

    return run, calls


def test_collect_reads_each_tap_and_records_failures():
    tap_info = json.dumps([{"name": "macos-fuse-t/cask", "cask_tokens": ["macos-fuse-t/cask/fuse-t"]}])
    info = json.dumps({"formulae": [], "casks": [TAP_FUSE]})
    run, calls = fake_brew({
        ("tap", "gone/tap"): (1, ""),
        ("tap-info", "--json"): (0, tap_info),
        ("info", "--cask"): (0, info),
    })

    result = collect.collect(
        [{"name": "gone/tap"}, {"name": "macos-fuse-t/cask", "remote": "https://github.com/macos-fuse-t/homebrew-cask"}],
        run, {"fuse-t": OFFICIAL_FUSE},
    )

    assert list(result["casks"]) == ["macos-fuse-t/cask/fuse-t"]
    assert result["sameAs"] == {"macos-fuse-t/cask/fuse-t": "fuse-t"}
    assert "gone/tap" in result["failures"]
    assert ["brew", "tap", "macos-fuse-t/cask", "https://github.com/macos-fuse-t/homebrew-cask"] in calls
    assert ["brew", "trust", "macos-fuse-t/cask"] in calls


def test_main_rejects_invalid_tap_names(capsys):
    import pytest
    with pytest.raises(SystemExit, match="not a lowercase"):
        collect.main(["--taps", "a/b; curl x|sh"])


def test_trusted_view_keeps_only_requested_well_formed_casks_and_recomputes_aliases():
    evil = cask("fuse-t", "macos-fuse-t/cask/fuse-t", "https://x", "https://x", [])
    collected = {
        "casks": {
            "macos-fuse-t/cask/fuse-t": TAP_FUSE,
            "other/tap/figma": cask("figma", "other/tap/figma", "https://f", "https://f", []),
            "macos-fuse-t/cask/spoof": evil,
            "macos-fuse-t/cask/bad;name": cask("bad;name", "macos-fuse-t/cask/bad;name", "https://b", "https://b", []),
        },
        "sameAs": {"macos-fuse-t/cask/fuse-t": "figma", "other/tap/figma": "figma"},
    }

    view = collect.trusted_view(collected, ["macos-fuse-t/cask"], {"fuse-t": OFFICIAL_FUSE})

    assert list(view["casks"]) == ["macos-fuse-t/cask/fuse-t"]
    assert view["sameAs"] == {"macos-fuse-t/cask/fuse-t": "fuse-t"}


def test_collect_stops_at_its_budget_and_hands_over_each_tap():
    run, calls = fake_brew({})
    ticks = iter([0, 50, 100, 150])
    seen = []

    result = collect.collect(
        [{"name": name} for name in ("a/b", "c/d", "e/f")], run, {},
        budget=120, clock=lambda: next(ticks), on_progress=seen.append,
    )

    assert [cmd[2] for cmd in calls if cmd[1] == "tap"] == ["a/b", "c/d"]
    assert result["unreached"] == ["e/f"]
    assert len(seen) == 2 and seen[-1]["unreached"] == []


def info_brew(broken):
    """brew info prints nothing and exits 1 when any named cask is broken, like the real one."""
    calls = []

    def run(cmd):
        calls.append(cmd)
        refs = cmd[4:]
        if broken & set(refs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"Error: Cask '{sorted(broken & set(refs))[0]}' is unavailable")
        casks = [{"token": ref.rsplit("/", 1)[1], "full_token": ref} for ref in refs]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"formulae": [], "casks": casks}), stderr="")

    return run, calls


def test_info_bisects_around_broken_casks():
    refs = [f"u/r/b{i}" for i in range(40)]
    broken = {"u/r/b3", "u/r/b17", "u/r/b29"}
    run, calls = info_brew(broken)

    casks, failures = collect._info(refs, run)

    assert len(casks) == 37 and not broken & set(casks)
    assert set(failures) == broken
    assert len(calls) < 40


def test_collect_records_cask_failures_and_read_taps():
    tap_info = json.dumps([{"name": "u/r", "cask_tokens": ["u/r/good", "u/r/bad"]}])
    info_run, _ = info_brew({"u/r/bad"})

    def run(cmd):
        if cmd[1] == "tap" and cmd[2] == "gone/tap":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        if cmd[1] == "tap-info":
            return subprocess.CompletedProcess(cmd, 0, stdout=tap_info, stderr="")
        if cmd[1] == "info":
            return info_run(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    result = collect.collect([{"name": "u/r"}, {"name": "gone/tap"}], run, {})

    assert list(result["casks"]) == ["u/r/good"]
    assert list(result["caskFailures"]) == ["u/r/bad"]
    assert "gone/tap" in result["failures"]


def test_trusted_view_filters_failures_and_read_taps():
    collected = {
        "casks": {},
        "caskFailures": {"u/r/bad": "Error: something `@mention`", "o/t/x": "forged", "../evil": "x"},
        "readTaps": ["u/r", "o/t"],
        "listed": {"u/r": {"u/r/a": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "o/t/b": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bad": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "u/r/z": "nothex"}, "o/t": {"o/t/c": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}},
        "processed": {"u/r/a": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "o/t/c": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "u/r/q": "nothex"},
    }

    view = collect.trusted_view(collected, ["u/r"], {})

    assert view["caskFailures"] == {"u/r/bad": ""}
    assert view["listed"] == {"u/r": {"u/r/a": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
    assert view["processed"] == {"u/r/a": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}


import pytest  # noqa: E402


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/A/B/releases/download/1/x.dmg", "github.com/a/b"),
    ("https://gitlab.com/a/b/-/releases/x.dmg", "gitlab.com/a/b"),
    ("https://codeberg.org/a/b/releases/download/1/x.dmg", "codeberg.org/a/b"),
    ("https://bitbucket.org/a/b/downloads/x.dmg", "bitbucket.org/a/b"),
    ("https://gitee.com/a/b/releases/download/1/x.dmg", "gitee.com/a/b"),
    ("https://git.sr.ht/~a/b/refs/download/1/x.dmg", "git.sr.ht/~a/b"),
    ("https://downloads.sourceforge.net/project/foo/1.0/x.dmg", "sourceforge.net/foo"),
    ("https://sourceforge.net/projects/foo/files/x.dmg/download", "sourceforge.net/foo"),
    ("https://storage.googleapis.com/bkt/x.dmg", "storage.googleapis.com/bkt"),
    ("https://s3.amazonaws.com/bkt/x.dmg", "s3.amazonaws.com/bkt"),
    ("https://dl.dropboxusercontent.com/s/abc/x.dmg", None),
    ("https://www.dropbox.com/s/abc/x.dmg?dl=1", None),
    ("https://drive.google.com/uc?id=1", None),
    ("https://dl.google.com/x.dmg", None),
    ("https://download.microsoft.com/x.pkg", None),
    ("https://objects.githubusercontent.com/x.dmg", None),
    ("https://download.vendor.com/x.dmg", "download.vendor.com"),
    ("", None),
])
def test_download_source_identity(url, expected):
    assert collect._download_source({"url": url}) == expected


def test_same_app_never_matches_on_missing_or_shared_sources():
    app = [{"app": ["Foo.app"]}]
    bare_tap, bare_official = cask("foo", "u/r/foo", "", "", app), cask("foo", "foo", "", "", app)
    assert not collect.same_app(bare_tap, bare_official)

    shared_tap = cask("foo", "u/r/foo", "https://a.example", "https://dl.dropboxusercontent.com/s/1/Foo.dmg", app)
    shared_official = cask("foo", "foo", "https://b.example", "https://dl.dropboxusercontent.com/s/2/Foo.dmg", app)
    assert not collect.same_app(shared_tap, shared_official)

    lab_tap = cask("foo", "u/r/foo", "https://a.example", "https://gitlab.com/x/foo/-/releases/2/Foo.dmg", app)
    lab_official = cask("foo", "foo", "https://b.example", "https://gitlab.com/x/foo/-/releases/1/Foo.dmg", app)
    other_repo = cask("foo", "foo", "https://b.example", "https://gitlab.com/y/foo/-/releases/1/Foo.dmg", app)
    assert collect.same_app(lab_tap, lab_official)
    assert not collect.same_app(lab_tap, other_repo)


def test_info_with_no_refs_asks_nothing():
    run, calls = info_brew(set())
    assert collect._info([], run) == ({}, {}) and calls == []


def test_info_gives_up_on_the_rest_at_its_deadline():
    refs = [f"u/r/b{i}" for i in range(8)]
    run, calls = info_brew(set(refs))
    ticks = iter(range(100))

    casks, failures = collect._info(refs, run, deadline=3, clock=lambda: next(ticks))

    assert casks == {} and set(failures) == set(refs)
    assert sum(reason == "out of time" for reason in failures.values()) >= 4 and len(calls) <= 4


def test_odd_brew_output_fails_only_its_tap():
    tap_info = json.dumps([{"name": "x", "cask_tokens": ["u/r/a"]}])

    def run(cmd):
        if cmd[1] == "tap-info":
            return subprocess.CompletedProcess(cmd, 0, stdout=tap_info, stderr="")
        if cmd[1] == "info":
            return subprocess.CompletedProcess(cmd, 0, stdout="[1]", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    result = collect.collect([{"name": "u/r"}, {"name": "o/t"}], run, {})

    assert set(result["failures"]) == {"u/r", "o/t"} and result["casks"] == {}


@pytest.mark.parametrize("collected", [
    {"casks": [], "caskFailures": [["x"]], "listed": {"u/r": "x"}},
    {"casks": {"u/r/a": "not a dict"}, "readTaps": [["x"]]},
    {"casks": None, "sameAs": [], "listed": [["u/r"]]},
])
def test_trusted_view_survives_malformed_output(collected):
    view = collect.trusted_view(collected, ["u/r"], {})
    assert view["casks"] == {} and view["caskFailures"] == {} and view["listed"] == {} and view["processed"] == {}
    assert "readTaps" not in view


@pytest.mark.parametrize("url,expected", [
    ("https://s3.us-west-2.amazonaws.com/bkt/x.dmg", "s3.us-west-2.amazonaws.com/bkt"),
    ("https://f001.backblazeb2.com/file/bkt/x.dmg", "backblazeb2.com/bkt"),
    ("https://dl.devmate.com/com.vendor.app/x.dmg", "dl.devmate.com/com.vendor.app"),
])
def test_more_shared_hosts_narrow_to_their_owner(url, expected):
    assert collect._download_source({"url": url}) == expected
