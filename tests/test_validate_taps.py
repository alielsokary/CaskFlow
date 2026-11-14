import subprocess

import validate_taps


def test_changed_entries_are_new_or_edited():
    base = [{"name": "a/b"}, {"name": "c/d"}]
    head = [{"name": "a/b"}, {"name": "c/d", "remote": "https://github.com/c/d"}, {"name": "e/f"}]
    assert [e["name"] for e in validate_taps.changed_entries(base, head)] == ["c/d", "e/f"]


def fake_runner(results):
    calls = []

    def run(cmd, **_):
        calls.append(cmd)
        out = results.get(cmd[1], (0, ""))
        return subprocess.CompletedProcess(cmd, out[0], stdout=out[1], stderr="")

    return run, calls


def test_reachable_tap_with_casks_passes(tmp_path):
    run, calls = fake_runner({"ls-tree": (0, "100644 blob aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\tCasks/x.rb\n")})
    assert validate_taps.check_tap({"name": "a/b"}, run, tmp_path) is None
    assert calls[0][:2] == ["git", "clone"] and "https://github.com/a/homebrew-b" in calls[0]


def test_unreachable_tap_fails(tmp_path):
    run, _ = fake_runner({"clone": (128, "")})
    assert "not reachable or has no casks" in validate_taps.check_tap({"name": "a/b"}, run, tmp_path)


def test_tap_without_casks_fails(tmp_path):
    run, _ = fake_runner({"ls-tree": (0, "")})
    assert "not reachable or has no casks" in validate_taps.check_tap({"name": "a/b"}, run, tmp_path)
