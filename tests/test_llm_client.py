"""Tests for the LLMClient base class — focused on validation behavior."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ is sibling to tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from llm_client import (  # noqa: E402
    Classification,
    ClassificationError,
    LLMClient,
    MockClient,
)
from prompts import CategoryCatalog  # noqa: E402


CATEGORIES_PATH = Path(__file__).resolve().parent.parent / "categories.json"


@pytest.fixture(scope="module")
def catalog() -> CategoryCatalog:
    return CategoryCatalog.load(CATEGORIES_PATH)


class _StubClient(LLMClient):

    """Drop-in client that returns whatever raw string the test specifies."""

    def __init__(self, catalog: CategoryCatalog, raw: str):
        super().__init__(catalog)
        self._raw = raw

    def _generate(self, system: str, user: str) -> str:
        return self._raw


def test_catalog_excludes_traits_from_primary(catalog):
    assert "ai" not in catalog.primary_ids
    assert "ai" in catalog.secondary_ids
    assert "developerTools" in catalog.primary_ids


def test_valid_classification_parses(catalog):
    raw = json.dumps(
        {"primary": "developerTools", "secondary": ["ai"], "confidence": 0.92, "reason": "ok"}
    )
    client = _StubClient(catalog, raw)
    result = client.classify({"token": "x", "name": "X", "desc": "", "homepage": ""}, None)
    assert isinstance(result, Classification)
    assert result.primary == "developerTools"
    assert result.secondary == ["ai"]
    assert result.confidence == pytest.approx(0.92)


def test_invalid_json_raises(catalog):
    client = _StubClient(catalog, "not json{")
    with pytest.raises(ClassificationError, match="invalid JSON"):
        client.classify({"token": "x"}, None)


def test_unknown_primary_rejected(catalog):
    raw = json.dumps({"primary": "blockchain", "secondary": [], "confidence": 1.0, "reason": ""})
    client = _StubClient(catalog, raw)
    with pytest.raises(ClassificationError, match="unknown primary"):
        client.classify({"token": "x"}, None)


def test_trait_as_primary_rejected(catalog):
    raw = json.dumps({"primary": "ai", "secondary": [], "confidence": 1.0, "reason": ""})
    client = _StubClient(catalog, raw)
    with pytest.raises(ClassificationError, match="cannot be primary"):
        client.classify({"token": "x"}, None)


def test_unknown_secondary_rejected(catalog):
    raw = json.dumps(
        {"primary": "utilities", "secondary": ["nonsense"], "confidence": 0.5, "reason": ""}
    )
    client = _StubClient(catalog, raw)
    with pytest.raises(ClassificationError, match="unknown secondary"):
        client.classify({"token": "x"}, None)


def test_primary_in_secondary_rejected(catalog):
    raw = json.dumps(
        {"primary": "utilities", "secondary": ["utilities"], "confidence": 0.5, "reason": ""}
    )
    client = _StubClient(catalog, raw)
    with pytest.raises(ClassificationError, match="duplicated"):
        client.classify({"token": "x"}, None)


def test_too_many_secondary_rejected(catalog):
    raw = json.dumps(
        {
            "primary": "utilities",
            "secondary": ["ai", "developerTools", "productivity"],
            "confidence": 0.5,
            "reason": "",
        }
    )
    client = _StubClient(catalog, raw)
    with pytest.raises(ClassificationError, match="too many"):
        client.classify({"token": "x"}, None)


def test_confidence_clamped(catalog):
    raw = json.dumps({"primary": "utilities", "secondary": [], "confidence": 1.7, "reason": ""})
    client = _StubClient(catalog, raw)
    assert client.classify({"token": "x"}, None).confidence == 1.0


def test_mock_client_classifies_keywords(catalog):
    client = MockClient(catalog)
    cask = {"token": "vscode", "name": "VS Code", "desc": "code editor", "homepage": "https://code.visualstudio.com"}
    result = client.classify(cask, None)
    assert result.primary == "developerTools"
