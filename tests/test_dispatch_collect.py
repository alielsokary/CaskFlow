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


def run_collect(gh, taps, tmp_path, *, budget=600):
    ticks = [0]

    def sleep(seconds):
        ticks[0] += seconds

    return dispatch_collect.collect(
        taps, gh, "master", {}, batch_size=1, budget=budget,
        sleep=sleep, clock=lambda: ticks[0], workdir=tmp_path,
    )


def test_batches_keep_order():
    assert dispatch_collect.batches(["a/a", "b/b", "c/c"], 2) == [["a/a", "b/b"], ["c/c"]]


def test_each_run_only_speaks_for_its_own_taps(tmp_path):
    gh = FakeGitHub({
        "a/a": {"casks": {"a/a/x": cask("a/a/x"), "b/b/evil": cask("b/b/evil")}},
        "b/b": {"casks": {"b/b/real": cask("b/b/real")}},
    })

    result = run_collect(gh, ["a/a", "b/b"], tmp_path)

    assert sorted(result["casks"]) == ["a/a/x", "b/b/real"]
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
