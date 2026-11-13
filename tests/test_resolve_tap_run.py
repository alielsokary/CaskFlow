import pytest

import resolve_tap_run as resolve

REGISTRY = [{"name": "a/b"}, {"name": "c/d"}]


def OK(entry):
    return None


def test_push_processes_added_or_changed_taps():
    taps, register, prune = resolve.resolve("push", base=[{"name": "a/b"}], head=REGISTRY)
    assert (taps, register, prune) == (["c/d"], [], [])


def test_schedule_sweeps_every_registered_tap():
    assert resolve.resolve("schedule", base=REGISTRY, head=REGISTRY) == (["a/b", "c/d"], [], [])


def test_schedule_picks_up_add_tap_issues_whose_run_was_dropped():
    titles = ["Add tap: e/f", "Add tap: a/b", "Add tap: $(whoami)/x", "something else"]
    assert resolve.resolve("schedule", base=REGISTRY, head=REGISTRY, issue_titles=titles, check=OK) == (["a/b", "c/d", "e/f"], ["e/f"], [])


def test_dispatch_registers_a_new_tap():
    assert resolve.resolve("workflow_dispatch", base=REGISTRY, head=REGISTRY, dispatch_tap="E/F", check=OK) == (["e/f"], ["e/f"], [])
    assert resolve.resolve("workflow_dispatch", base=REGISTRY, head=REGISTRY, dispatch_tap="a/b") == (["a/b"], [], [])


def test_issue_reads_the_tap_from_its_title():
    taps, register, prune = resolve.resolve("issues", base=REGISTRY, head=REGISTRY, issue_title="Add tap: saffsanity/sill", check=OK)
    assert (taps, register, prune) == (["saffsanity/sill"], ["saffsanity/sill"], [])


@pytest.mark.parametrize("event,kwargs", [
    ("workflow_dispatch", {"dispatch_tap": "a/b; curl x|sh"}),
    ("issues", {"issue_title": "Add tap: $(whoami)/x"}),
    ("issues", {"issue_title": "no tap here"}),
])
def test_bad_requests_are_refused(event, kwargs):
    with pytest.raises(SystemExit):
        resolve.resolve(event, base=REGISTRY, head=REGISTRY, check=OK, **kwargs)


def _event(kind, at, label=None):
    return {"event": kind, "created_at": at, **({"label": {"name": label}} if label else {})}


def test_sweep_takes_an_issue_only_when_its_label_came_after_its_last_rename():
    labeled_then_renamed = [_event("labeled", "2026-11-10T00:00:00Z", "add-tap"), _event("renamed", "2026-11-11T00:00:00Z")]
    renamed_then_labeled = [_event("renamed", "2026-11-10T00:00:00Z"), _event("labeled", "2026-11-11T00:00:00Z", "add-tap")]
    other_label = [_event("labeled", "2026-11-11T00:00:00Z", "bug")]

    assert not resolve.approved(labeled_then_renamed)
    assert resolve.approved(renamed_then_labeled)
    assert resolve.approved([_event("labeled", "2026-11-11T00:00:00Z", "add-tap")])
    assert not resolve.approved(other_label)


def test_push_prunes_taps_removed_from_the_registry():
    assert resolve.resolve("push", base=REGISTRY, head=[{"name": "a/b"}]) == ([], [], ["c/d"])


def test_requested_tap_must_exist_with_casks():
    def missing(entry):
        return f"{entry['name']}: https://github.com/x/homebrew-y is not reachable"

    with pytest.raises(SystemExit, match="not reachable"):
        resolve.resolve("workflow_dispatch", base=REGISTRY, head=REGISTRY, dispatch_tap="x/y", check=missing)
    with pytest.raises(SystemExit, match="not reachable"):
        resolve.resolve("issues", base=REGISTRY, head=REGISTRY, issue_title="Add tap: x/y", check=missing)
    checked = []
    resolve.resolve("workflow_dispatch", base=REGISTRY, head=REGISTRY, dispatch_tap="a/b", check=checked.append)
    assert checked == [], "registered taps aren't re-checked"


def test_sweep_skips_requested_taps_that_fail_the_check():
    def only_ef(entry):
        return None if entry["name"] == "e/f" else "nope"

    titles = ["Add tap: e/f", "Add tap: g/h"]
    assert resolve.resolve("schedule", base=REGISTRY, head=REGISTRY, issue_titles=titles, check=only_ef) == (
        ["a/b", "c/d", "e/f"], ["e/f"], [])
