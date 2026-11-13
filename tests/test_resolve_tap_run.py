import pytest

import resolve_tap_run as resolve

REGISTRY = [{"name": "a/b"}, {"name": "c/d"}]


def test_push_processes_added_or_changed_taps():
    taps, register = resolve.resolve("push", base=[{"name": "a/b"}], head=REGISTRY)
    assert (taps, register) == (["c/d"], [])


def test_schedule_sweeps_every_registered_tap():
    assert resolve.resolve("schedule", base=REGISTRY, head=REGISTRY) == (["a/b", "c/d"], [])


def test_schedule_picks_up_add_tap_issues_whose_run_was_dropped():
    titles = ["Add tap: e/f", "Add tap: a/b", "Add tap: $(whoami)/x", "something else"]
    assert resolve.resolve("schedule", base=REGISTRY, head=REGISTRY, issue_titles=titles) == (["a/b", "c/d", "e/f"], ["e/f"])


def test_dispatch_registers_a_new_tap():
    assert resolve.resolve("workflow_dispatch", base=REGISTRY, head=REGISTRY, dispatch_tap="E/F") == (["e/f"], ["e/f"])
    assert resolve.resolve("workflow_dispatch", base=REGISTRY, head=REGISTRY, dispatch_tap="a/b") == (["a/b"], [])


def test_issue_reads_the_tap_from_its_title():
    taps, register = resolve.resolve("issues", base=REGISTRY, head=REGISTRY, issue_title="Add tap: saffsanity/sill")
    assert (taps, register) == (["saffsanity/sill"], ["saffsanity/sill"])


@pytest.mark.parametrize("event,kwargs", [
    ("workflow_dispatch", {"dispatch_tap": "a/b; curl x|sh"}),
    ("issues", {"issue_title": "Add tap: $(whoami)/x"}),
    ("issues", {"issue_title": "no tap here"}),
])
def test_bad_requests_are_refused(event, kwargs):
    with pytest.raises(SystemExit):
        resolve.resolve(event, base=REGISTRY, head=REGISTRY, **kwargs)


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
