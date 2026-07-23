from __future__ import annotations

import json

import classify_new_casks as classifier
from llm_client import Classification


def _classification(confidence: float = 0.9) -> Classification:
    return Classification("utilities", [], confidence, "Utility application")


def test_manual_review_threshold_and_report():
    classifications = {
        "certain": _classification(0.75),
        "uncertain": _classification(0.74),
    }

    assert classifier.requires_manual_review(classifications)
    report = classifier.build_report(
        set(), set(), classifications, [], []
    )
    assert "1 classifications require manual review" in report
    assert "## Manual review required" in report
    assert "`uncertain`" in report


def test_no_manual_review_at_or_above_threshold():
    assert not classifier.requires_manual_review({"exact": _classification(0.75)})


def test_fetch_homepages_can_skip_cache_persistence(monkeypatch):
    cache = {}
    persisted = []

    def persist(value):
        persisted.append(value)

    monkeypatch.setattr(
        classifier,
        "fetch_one",
        lambda token, url: {"token": token, "homepage": url, "title": "Example"},
    )
    monkeypatch.setattr(classifier, "_persist_cache", persist)

    result = classifier.fetch_homepages_for(
        {"example"},
        {"example": {"homepage": "https://example.com"}},
        cache,
        persist=False,
    )

    assert result["example"]["title"] == "Example"
    assert persisted == []


def test_dry_run_does_not_change_repository_files(tmp_path, monkeypatch):
    categories_path = tmp_path / "categories.json"
    cache_path = tmp_path / "homepage_metadata.json"
    report_path = tmp_path / "classification_report.md"
    categories = {
        "version": 2,
        "generatedDate": "2026-01-01",
        "totalCasks": 1,
        "categories": {
            "utilities": {"displayName": "Utilities", "icon": "wrench"},
            "ai": {"displayName": "AI", "icon": "sparkle"},
        },
        "tokenToCategory": {"existing": {"primary": "utilities", "secondary": []}},
    }
    categories_path.write_text(json.dumps(categories), encoding="utf-8")
    cache_path.write_text("[]", encoding="utf-8")
    before_categories = categories_path.read_bytes()
    before_cache = cache_path.read_bytes()

    monkeypatch.setattr(classifier, "CATEGORIES_PATH", categories_path)
    monkeypatch.setattr(classifier, "HOMEPAGE_CACHE", cache_path)
    monkeypatch.setattr(classifier, "REPORT_PATH", report_path)
    monkeypatch.setattr(
        classifier,
        "fetch_brew_api",
        lambda: [
            {"token": "existing", "homepage": "https://example.com/existing"},
            {"token": "new", "homepage": "https://example.com/new"},
        ],
    )
    monkeypatch.setattr(
        classifier,
        "fetch_one",
        lambda token, url: {"token": token, "homepage": url, "title": token},
    )

    class StubClient:
        def classify(self, cask, homepage_meta):
            return _classification()

    monkeypatch.setattr(classifier.LLMClient, "from_env", lambda catalog: StubClient())

    assert classifier.main(["--dry-run"]) == 0
    assert categories_path.read_bytes() == before_categories
    assert cache_path.read_bytes() == before_cache
    assert not report_path.exists()


def test_github_output_is_written_when_available(tmp_path, monkeypatch):
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    classifier.write_github_output("review_required", "true")

    assert output.read_text(encoding="utf-8") == "review_required=true\n"
