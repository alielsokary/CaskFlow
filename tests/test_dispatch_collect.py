import json
import subprocess
from pathlib import Path

import dispatch_collect


def cask(ref):
    return {"token": ref.rsplit("/", 1)[1], "full_token": ref}


class FakeGitHub:
    """Each dispatched batch becomes a run whose artifact holds what `artifacts` says for its first tap."""

    def __init__(self, artifacts, never_finishes=()):
        self.artifacts, self.never_finishes = artifacts, set(never_finishes)
        self.runs, self.calls, self.cancelled = {}, [], []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["workflow", "run"]:
            fields = dict(arg.split("=", 1) for arg in args if "=" in arg)
            self.runs[100 + len(self.runs)] = (fields["nonce"], fields["taps"].split())
            return ""
        if args[:2] == ["run", "list"]:
            return json.dumps([{"databaseId": run_id, "displayTitle": f"Tap collect {nonce}"}
                               for run_id, (nonce, _) in self.runs.items()])
        run_id = int(args[2])
        first_tap = self.runs[run_id][1][0]
        if args[:2] == ["run", "view"]:
            return json.dumps({"status": "in_progress" if first_tap in self.never_finishes else "completed"})
        if args[:2] == ["run", "cancel"]:
            self.cancelled.append(first_tap)
            return ""
        if args[:2] == ["run", "download"]:
            folder = Path(args[args.index("-D") + 1])
            folder.mkdir(parents=True)
            (folder / "tap_casks.json").write_text(json.dumps(self.artifacts[first_tap]), encoding="utf-8")
            return ""
        raise AssertionError(args)


def run_collect(gh, taps, tmp_path, *, budget=600, lister=lambda name: None):
    ticks = [0]

    def sleep(seconds):
        ticks[0] += seconds

    return dispatch_collect.collect(
        taps, gh, "master", {}, batch_size=1, budget=budget,
        sleep=sleep, clock=lambda: ticks[0], workdir=tmp_path, lister=lister,
    )


def test_batches_keep_order():
    assert dispatch_collect.batches(["a/a", "b/b", "c/c"], 2) == [["a/a", "b/b"], ["c/c"]]


def test_each_run_only_speaks_for_its_own_taps(tmp_path):
    gh = FakeGitHub({
        "a/a": {"casks": {"a/a/x": cask("a/a/x"), "b/b/evil": cask("b/b/evil")},
                "readTaps": ["a/a", "b/b"], "caskFailures": {"b/b/real": "forged"}},
        "b/b": {"casks": {"b/b/real": cask("b/b/real")}},
    })

    result = run_collect(gh, ["a/a", "b/b"], tmp_path)

    assert sorted(result["casks"]) == ["a/a/x", "b/b/real"]
    assert result["caskFailures"] == {} and "readTaps" not in result
    dispatches = [call for call in gh.calls if call[:2] == ["workflow", "run"]]
    assert all("--ref" in call and "master" in call for call in dispatches) and len(dispatches) == 2


def test_unfinished_runs_are_cancelled_and_their_taps_wait(tmp_path):
    gh = FakeGitHub({"a/a": {"casks": {"a/a/x": cask("a/a/x")}}}, never_finishes={"b/b"})

    result = run_collect(gh, ["a/a", "b/b"], tmp_path, budget=60)

    assert sorted(result["casks"]) == ["a/a/x"]
    assert result["unreached"] == ["b/b"]
    assert gh.cancelled == ["b/b"]


def test_a_failed_download_marks_its_taps_unreached(tmp_path):
    class Broken(FakeGitHub):
        def __call__(self, args):
            if args[:2] == ["run", "download"]:
                raise subprocess.CalledProcessError(1, args)
            return super().__call__(args)

    result = run_collect(Broken({}), ["a/a"], tmp_path)

    assert result["casks"] == {} and result["unreached"] == ["a/a"]


def test_summary_lists_only_validated_names():
    result = {
        "failures": {"a/b": "Error: `rm -rf`", "x`@evil/y": "nope"},
        "caskFailures": {"a/b/c": "", "@evil": ""},
        "unreached": ["c/d", "<img src=x>"],
    }

    text = dispatch_collect.summary(result)

    for name in ("a/b", "a/b/c", "c/d"):
        assert name in text
    assert "@" not in text and "<" not in text and "rm -rf" not in text


def test_cask_listing_comes_from_git_not_from_the_runs(tmp_path):
    gh = FakeGitHub({
        "a/a": {"casks": {"a/a/x": cask("a/a/x")}, "readTaps": ["a/a", "b/b"]},
        "b/b": {"casks": {}},
    })
    listings = {"a/a": ["a/a/x"], "b/b": ["b/b/real"]}

    result = run_collect(gh, ["a/a", "b/b"], tmp_path, lister=listings.get)

    assert result["listed"] == {"a/a": ["a/a/x"], "b/b": ["b/b/real"]}


def git_runner(ls_tree, clone_code=0):
    def run(cmd, cwd=None):
        if cmd[1] == "clone":
            return subprocess.CompletedProcess(cmd, clone_code, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout=ls_tree, stderr="")
    return run


def test_list_casks_reads_cask_file_names_without_running_them(tmp_path):
    tree = "Casks/sill.rb\nCasks/s/sharded.rb\nCasks/README.md\nCasks/Bad Name.rb\n"
    assert dispatch_collect.list_casks({"name": "u/r"}, git_runner(tree), tmp_path) == ["u/r/sharded", "u/r/sill"]
    assert dispatch_collect.list_casks({"name": "u/r"}, git_runner(tree, clone_code=1), tmp_path) is None
    assert dispatch_collect.list_casks({"name": "u/r"}, git_runner(""), tmp_path) is None


def test_malformed_batch_output_only_loses_its_batch(tmp_path):
    gh = FakeGitHub({
        "a/a": {"readTaps": [[]], "casks": [], "caskFailures": [["x"]]},
        "b/b": {"casks": {"b/b/real": cask("b/b/real")}},
    })

    result = run_collect(gh, ["a/a", "b/b"], tmp_path)

    assert sorted(result["casks"]) == ["b/b/real"] and "a/a" in result["unreached"]


def test_list_casks_treats_a_hung_clone_as_unknown(tmp_path):
    def run(cmd, cwd=None):
        raise subprocess.TimeoutExpired(cmd, 120)

    assert dispatch_collect.list_casks({"name": "u/r"}, run, tmp_path) is None
