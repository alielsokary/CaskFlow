import json

import classify_tap_casks as classify
from llm_client import Classification

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
    monkeypatch.setattr(classify, "make_client", lambda: FakeClient())

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
