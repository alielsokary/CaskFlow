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
