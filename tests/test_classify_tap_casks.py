import json

import classify_tap_casks as classify
from llm_client import Classification

A, B = "a" * 40, "b" * 40

MOVED_ARTIFACTS = [{"app": ["Moved.app"]}]
OFFICIAL = {"moved": {"token": "moved", "homepage": "https://moved.dev", "url": "https://moved.dev/m.dmg",
                      "artifacts": MOVED_ARTIFACTS}}
COLLECTED = {
    "casks": {
        "a/b/new": {"token": "new", "full_token": "a/b/new", "homepage": "https://new.dev"},
        "a/b/old": {"token": "old", "full_token": "a/b/old", "homepage": "https://old.dev"},
        "a/b/moved": {"token": "moved", "full_token": "a/b/moved", "homepage": "https://moved.dev",
                      "url": "https://moved.dev/m.dmg", "artifacts": MOVED_ARTIFACTS},
        "x/y/sneaky": {"token": "sneaky", "full_token": "x/y/sneaky", "homepage": "https://s.dev"},
    },
    "sameAs": {"a/b/new": "figma"},
    "failures": {},
}
VIEW = {"casks": {k: v for k, v in COLLECTED["casks"].items() if k.startswith("a/b/")}, "sameAs": {"a/b/moved": "moved"}}
EXISTING = {"version": 1, "generatedDate": "2026-11-01", "casks": {"a/b/old": {"primary": "games", "secondary": []}}, "sameAs": {}}


def test_pending_skips_aliased_and_classified_casks():
    assert classify.pending_refs(VIEW, EXISTING) == {"a/b/new"}


def test_merge_keeps_prior_entries_and_records_aliases():
    merged = classify.merge(EXISTING, {"a/b/new": Classification("utilities", ["productivity"], 0.9, "r")}, {"a/b/moved": "moved"})

    assert merged["version"] == 1
    assert merged["casks"] == {
        "a/b/new": {"primary": "utilities", "secondary": ["productivity"]},
        "a/b/old": {"primary": "games", "secondary": []},
    }
    assert merged["sameAs"] == {"a/b/moved": "moved"}
    assert merged["generatedDate"] != "2026-11-01"


def test_main_classifies_only_pending_casks(tmp_path, monkeypatch):
    collected = tmp_path / "tap_casks.json"
    collected.write_text(json.dumps(COLLECTED), encoding="utf-8")
    output = tmp_path / "tap_categories.json"
    output.write_text(json.dumps(EXISTING), encoding="utf-8")
    seen = []

    class FakeClient:
        def classify(self, cask, homepage_meta):
            seen.append(cask["full_token"])
            return Classification("utilities", [], 0.9, "r")

    monkeypatch.setattr(classify, "TAP_CATEGORIES_PATH", output)
    cache_file = tmp_path / "tap_homepages.json"

    def fake_fetch(refs, casks, cache):
        cache.update({ref: {"token": ref, "title": "t"} for ref in refs})
        return {}

    monkeypatch.setattr(classify, "fetch_tap_homepages", fake_fetch)
    monkeypatch.setattr(classify, "load_official", lambda: OFFICIAL)
    monkeypatch.setattr(classify, "TAP_HOMEPAGE_CACHE", cache_file)
    monkeypatch.setattr(classify, "make_client", FakeClient)

    assert classify.main(["--collected", str(collected), "--taps", "a/b"]) == 0
    assert seen == ["a/b/new"], "x/y is not a requested tap; the file's own aliases are ignored"
    written = json.loads(output.read_text(encoding="utf-8"))
    assert set(written["casks"]) == {"a/b/new", "a/b/old"}
    assert written["sameAs"] == {"a/b/moved": "moved"}
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"a/b/new": {"token": "a/b/new", "title": "t"}}


def test_non_public_homepages_are_never_fetched(monkeypatch):
    called = []
    monkeypatch.setattr(classify, "is_public_url", lambda url: url == "https://ok.dev")
    monkeypatch.setattr(classify, "fetch_one", lambda ref, url, open_url: called.append(url) or {"token": ref})
    casks = {"a/b/ok": {"homepage": "https://ok.dev"}, "a/b/meta": {"homepage": "http://169.254.169.254/"}}
    cache = {}

    classify.fetch_tap_homepages({"a/b/ok", "a/b/meta"}, casks, cache)

    assert called == ["https://ok.dev"]
    assert cache["a/b/meta"]["error"] == "non_public_url"


def view(casks=(), failures=(), listed=None, same_as=None, processed=None):
    return {"casks": {ref: {} for ref in casks}, "caskFailures": {ref: "" for ref in failures},
            "listed": listed or {}, "sameAs": same_as or {}, "processed": processed or {}}


def data(casks=(), same_as=None):
    return {"version": 1, "generatedDate": "", "casks": {ref: {"primary": "utilities", "secondary": []} for ref in casks},
            "sameAs": same_as or {}}


def test_renamed_cask_drops_the_old_ref():
    pruned, removed = classify.prune(data(["u/r/old"]), view(["u/r/new"], listed={"u/r": ["u/r/new"]}), [])
    assert "u/r/old" not in pruned["casks"] and removed == ["u/r/old"]


def test_failed_casks_are_never_pruned():
    pruned, removed = classify.prune(data(["u/r/x"]), view(failures=["u/r/x"], listed={"u/r": ["u/r/x"]}), [])
    assert "u/r/x" in pruned["casks"] and removed == []


def test_unread_taps_keep_their_data():
    pruned, removed = classify.prune(data(["v/w/y"], {"v/w/z": "z"}), view(listed={"u/r": []}), [])
    assert pruned["casks"] == data(["v/w/y"])["casks"] and pruned["sameAs"] == {"v/w/z": "z"} and removed == []


def test_forged_empty_tap_data_cannot_delete_anything():
    # A neighbour in the same collect run can blank this tap's casks, but not its repository's file list.
    forged = view(listed={"n/b": ["n/b/x", "n/b/y"]}) | {"readTaps": ["n/b"]}
    pruned, removed = classify.prune(data(["n/b/x"], {"n/b/y": "y"}), forged, [])
    assert pruned["casks"] == data(["n/b/x"])["casks"] and pruned["sameAs"] == {"n/b/y": "y"} and removed == []


def test_alias_follows_the_latest_comparison():
    pruned, removed = classify.prune(data(["u/r/b"], {"u/r/a": "a"}),
                                     view(["u/r/a", "u/r/b"], listed={"u/r": ["u/r/a", "u/r/b"]}, same_as={"u/r/b": "b"}), [])
    assert "u/r/a" not in pruned["sameAs"]
    assert "u/r/b" not in pruned["casks"] and pruned["sameAs"]["u/r/b"] == "b" and removed == ["u/r/b"]


def test_removed_taps_lose_everything():
    pruned, removed = classify.prune(data(["g/t/x"], {"g/t/y": "y"}), view(), ["g/t"])
    assert pruned["casks"] == {} and pruned["sameAs"] == {} and removed == ["g/t/x", "g/t/y"]


def test_run_file_keeps_icons_for_every_listed_cask():
    run = classify.run_file(view(["u/r/a", "u/r/b"], failures=["u/r/c"], listed={"u/r": ["u/r/a", "u/r/b", "u/r/c"]},
                                 same_as={"u/r/b": "b"}), ["g/t"])
    assert run == {"readable": ["u/r"], "current": {"u/r": ["u/r/a", "u/r/b", "u/r/c"]}, "prunedTaps": ["g/t"]}


def test_an_empty_listing_prunes_nothing():
    pruned, removed = classify.prune(data(["u/r/x"]), view(listed={"u/r": []}), [])
    assert "u/r/x" in pruned["casks"] and removed == []


def test_tap_files_settle_only_classified_or_aliased_casks_and_forget_gone_ones():
    merged = data(["u/r/a"], {"u/r/b": "b"})
    existing = {"u/r/gone": A, "g/t/x": A, "o/t/keep": A}
    current = view(listed={"u/r": {"u/r/a": B, "u/r/b": A, "u/r/c": A}},
                   processed={"u/r/a": B, "u/r/b": A, "u/r/c": A})

    files = classify.tap_files(existing, merged, current, ["g/t"])

    assert files == {"o/t/keep": A, "u/r/a": B, "u/r/b": A}
