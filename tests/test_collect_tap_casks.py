import json
import subprocess
from pathlib import Path

import pytest

import collect_tap_casks as collect

A, B = "a" * 40, "b" * 40


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


def info_brew(broken):
    """Fake brew whose info prints nothing and exits 1 when any named cask is broken, like the real one."""
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


def test_trusted_view_filters_failures_and_read_taps():
    collected = {
        "casks": {},
        "caskFailures": {"u/r/bad": "Error: something `@mention`", "o/t/x": "forged", "../evil": "x"},
        "readTaps": ["u/r", "o/t"],
        "listed": {"u/r": {"u/r/a": A, "o/t/b": A, "bad": A, "u/r/z": "nothex"}, "o/t": {"o/t/c": A}},
        "processed": {"u/r/a": A, "o/t/c": A, "u/r/q": "nothex"},
    }

    view = collect.trusted_view(collected, ["u/r"], {})

    assert view["caskFailures"] == {"u/r/bad": ""}
    assert view["listed"] == {"u/r": {"u/r/a": A}}
    assert view["processed"] == {"u/r/a": A}



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


TAP_INFO = json.dumps([{"name": "u/r", "cask_tokens": ["u/r/good", "u/r/bad"]}])


class FakeHost:
    """git and brew trust run on the host; brew reads run in the sandbox. One shared log of both."""

    def __init__(self, clone_code=0, tap_info=TAP_INFO, broken=frozenset()):
        self.calls, self.sandboxed = [], []
        self.clone_code, self.tap_info = clone_code, tap_info
        self.info, _ = info_brew(set(broken))

    def run(self, cmd):
        self.calls.append(cmd)
        code = self.clone_code if cmd[:2] == ["git", "clone"] else 0
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr="boom" if code else "")

    def sandbox(self):
        def run(cmd):
            self.sandboxed.append(cmd)
            if cmd[1] == "tap-info":
                return subprocess.CompletedProcess(cmd, 0, stdout=self.tap_info, stderr="")
            return self.info(cmd)
        return run


def read(host, entry, tmp_path, **kwargs):
    return collect.read_tap(entry, host.sandbox, run=host.run, taps_root=tmp_path, **kwargs)


def test_read_tap_clones_with_git_and_runs_brew_reads_only_in_the_sandbox(tmp_path):
    host = FakeHost(broken={"u/r/bad"})

    data = read(host, {"name": "u/r", "remote": "https://example.com/u/casks"}, tmp_path)

    assert host.calls == [
        ["git", "clone", "--quiet", "--depth", "1", "https://example.com/u/casks", str(tmp_path / "u" / "homebrew-r")],
        ["brew", "trust", "--tap", "u/r"],
    ]
    assert all(cmd[0] == "brew" and cmd[1] in ("tap-info", "info") for cmd in host.sandboxed)
    assert list(data["casks"]) == ["u/r/good"] and list(data["caskFailures"]) == ["u/r/bad"]


def test_read_tap_failures_cost_only_that_tap(tmp_path):
    assert read(FakeHost(clone_code=1), {"name": "u/r"}, tmp_path)["failures"]["u/r"].startswith("git clone failed")
    assert "u/r" in read(FakeHost(tap_info="[1]"), {"name": "u/r"}, tmp_path / "x")["failures"]


def test_read_tap_past_its_deadline_waits_for_the_next_run(tmp_path):
    host = FakeHost()

    assert read(host, {"name": "u/r"}, tmp_path, deadline=5, clock=lambda: 5) == {"unreached": ["u/r"]}
    assert host.calls == [] and host.sandboxed == []


def test_sandbox_profile_denies_network_writes_and_the_real_home():
    profile = collect.PROFILE
    assert "(deny network*)" in profile and "(deny file-write*)" in profile
    assert '(allow file-write*\n  (subpath (param "WORK"))' in profile
    assert '(deny file-read* (subpath (param "REAL_HOME")))' in profile


def test_sandboxed_brew_gets_a_private_home_and_no_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "secret")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".homebrew").mkdir(parents=True)
    (tmp_path / "home" / ".homebrew" / "trust.json").write_text("{}", encoding="utf-8")
    (tmp_path / "cache").mkdir()

    sandbox = collect.TapSandbox(tmp_path / "boxes", tmp_path / "cache")

    command = sandbox.command(["brew", "info"])
    assert command[:3] == ["sandbox-exec", "-p", collect.PROFILE] and command[-2:] == ["brew", "info"]
    assert f"WORK={sandbox.work}" in command and f"REAL_HOME={tmp_path / 'home'}" in command
    assert not [key for key in sandbox.env if "TOKEN" in key or key.startswith(("GITHUB_", "ACTIONS_", "RUNNER_"))]
    assert Path(sandbox.env["HOME"]).is_relative_to(sandbox.work)
    assert (Path(sandbox.env["HOME"]) / ".homebrew" / "trust.json").exists()
    assert Path(sandbox.env["HOMEBREW_CACHE"]).is_relative_to(sandbox.work)
    assert sandbox.env["HOMEBREW_NO_GITHUB_API"] == "1" and sandbox.env["HOMEBREW_API_AUTO_UPDATE_SECS"] == "86400"


def test_output_is_read_through_the_held_handle_and_capped(tmp_path):
    with open(tmp_path / "out", "w+b") as handle:
        handle.write(b"ok")
        (tmp_path / "out").unlink()
        (tmp_path / "out").symlink_to(tmp_path / "secret")
        (tmp_path / "secret").write_text("secret", encoding="utf-8")
        assert collect._read_capped(handle) == "ok"
        handle.write(b"x" * collect.MAX_OUTPUT_BYTES)
        assert collect._read_capped(handle) is None


def sandbox_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "cache").mkdir()


def test_a_tap_user_runs_brew_through_sudo_with_only_the_sandbox_environment(tmp_path, monkeypatch):
    sandbox_home(tmp_path, monkeypatch)
    calls = []
    user = collect.TapUser("_caskflowtap0", 7700)

    sandbox = collect.TapSandbox(tmp_path / "boxes", tmp_path / "cache", user=user,
                                 run=lambda cmd, env=None: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))

    command = sandbox.command(["brew", "info"])
    start = command.index("sandbox-exec")
    assert command[:6] == ["sudo", "-n", "-u", "_caskflowtap0", "/usr/bin/env", "-i"]
    assert sorted(command[6:start]) == sorted(f"{key}={value}" for key, value in sandbox.env.items())
    assert calls[:2] == [["sudo", "-n", "chown", "-R", "_caskflowtap0", str(sandbox.work)],
                         ["sudo", "-n", "chmod", "711", str(sandbox.work)]]
    assert "directory = *" in (sandbox.work / "home" / ".gitconfig").read_text(encoding="utf-8")



def test_tap_users_are_hidden_accounts_without_a_shell_or_home():
    calls = []

    user = collect.create_tap_user(1, run=lambda cmd, env=None: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))

    assert user == collect.TapUser("_caskflowtap1", 7701)
    assert ["sudo", "-n", "dscl", ".", "-create", "/Users/_caskflowtap1", "UniqueID", "7701"] in calls
    assert ["sudo", "-n", "dscl", ".", "-create", "/Users/_caskflowtap1", "UserShell", "/usr/bin/false"] in calls
    assert ["sudo", "-n", "dscl", ".", "-create", "/Users/_caskflowtap1", "NFSHomeDirectory", "/var/empty"] in calls


def test_cleaning_a_tap_user_kills_its_jobs_and_fails_closed_when_anything_is_left():
    user = collect.TapUser("_caskflowtap0", 7700)

    def runner(left):
        calls = []

        def run(cmd, env=None):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0 if cmd[2] == "pgrep" and left else (1 if cmd[2] == "pgrep" else 0), "", "")
        return run, calls

    run, calls = runner(left=False)
    assert collect.clean_tap_user(user, run) is True
    assert calls == [["sudo", "-n", "launchctl", "bootout", "user/7700"], ["sudo", "-n", "pkill", "-9", "-u", "_caskflowtap0"],
                     ["sudo", "-n", "pgrep", "-u", "_caskflowtap0"]]
    run, _ = runner(left=True)
    assert collect.clean_tap_user(user, run) is False


def test_a_call_that_leaves_tap_user_processes_kills_them_before_reading(tmp_path, monkeypatch):
    sandbox_home(tmp_path, monkeypatch)
    calls = []
    run = lambda cmd, env=None: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")  # noqa: E731
    sandbox = collect.TapSandbox(tmp_path / "boxes", tmp_path / "cache", user=collect.TapUser("_caskflowtap0", 7700), run=run)

    class Finished:
        pid = 4242

        def __init__(self, *args, **kwargs):
            pass

        def wait(self, timeout=None):
            return 0

    def killpg(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(collect.subprocess, "Popen", Finished)
    monkeypatch.setattr(collect.os, "killpg", killpg)
    calls.clear()

    assert sandbox(["brew", "info"]).returncode == 0
    assert calls == [["sudo", "-n", "pkill", "-9", "-u", "_caskflowtap0"]]
