from __future__ import annotations

import json
from pathlib import Path

import pytest

from apply_corrections import apply_correction, apply_corrections, validate_categories


def _data() -> dict:
    return {
        "generatedDate": "2026-01-01",
        "totalCasks": 1,
        "categories": {
            "utilities": {},
            "developerTools": {},
            "productivity": {},
            "ai": {},
        },
        "tokenToCategory": {
            "example": {"primary": "utilities", "secondary": ["developerTools"]}
        },
    }


def test_primary_change_removes_duplicate_secondary():
    data = _data()
    changed = apply_correction(
        {"token": "example", "was": "utilities", "shouldBe": "developerTools"},
        data["tokenToCategory"],
        set(data["categories"]),
    )

    assert changed
    assert data["tokenToCategory"]["example"] == {
        "primary": "developerTools",
        "secondary": [],
    }


def test_secondary_action_is_deduplicated():
    data = _data()
    correction = {
        "token": "example",
        "action": "addSecondary",
        "secondary": ["ai", "ai"],
    }

    assert apply_correction(correction, data["tokenToCategory"], set(data["categories"]))
    assert data["tokenToCategory"]["example"]["secondary"] == ["developerTools", "ai"]


def test_stale_expected_primary_is_rejected():
    data = _data()
    with pytest.raises(ValueError, match="expected current primary"):
        apply_correction(
            {"token": "example", "was": "productivity", "shouldBe": "developerTools"},
            data["tokenToCategory"],
            set(data["categories"]),
        )


def test_trait_cannot_be_primary():
    data = _data()
    with pytest.raises(ValueError, match="secondary-only trait"):
        apply_correction(
            {"token": "example", "was": "utilities", "shouldBe": "ai"},
            data["tokenToCategory"],
            set(data["categories"]),
        )


def test_default_selection_only_applies_high_confidence():
    data = _data()
    corrections = [
        {
            "token": "example",
            "was": "utilities",
            "shouldBe": "developerTools",
            "confidence": "medium",
        }
    ]

    assert apply_corrections(data, corrections, apply_all=False) == []
    assert data["tokenToCategory"]["example"]["primary"] == "utilities"


def test_dataset_validation_rejects_primary_in_secondary():
    data = _data()
    data["tokenToCategory"]["example"]["secondary"] = ["utilities"]
    with pytest.raises(ValueError, match="invalid secondary"):
        validate_categories(data)


def test_repository_categories_satisfy_schema_invariants():
    path = Path(__file__).resolve().parent.parent / "categories.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    validate_categories(data)
    assert data["totalCasks"] == len(data["tokenToCategory"])
