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


@pytest.mark.parametrize("secondary", [None, []])
def test_secondary_action_requires_categories(secondary):
    data = _data()
    correction = {
        "token": "example",
        "action": "addSecondary",
        "secondary": secondary,
    }

    with pytest.raises(ValueError, match="requires a non-empty secondary list"):
        apply_correction(correction, data["tokenToCategory"], set(data["categories"]))


def test_secondary_action_rejects_unknown_category():
    data = _data()
    correction = {
        "token": "example",
        "action": "addSecondary",
        "secondary": ["unknown"],
    }

    with pytest.raises(ValueError, match="unknown secondary categories"):
        apply_correction(correction, data["tokenToCategory"], set(data["categories"]))


def test_secondary_action_enforces_item_limit():
    data = _data()
    correction = {
        "token": "example",
        "action": "addSecondary",
        "secondary": ["ai", "productivity"],
    }

    with pytest.raises(ValueError, match="2-item limit"):
        apply_correction(correction, data["tokenToCategory"], set(data["categories"]))


def test_secondary_action_reports_no_change():
    data = _data()
    correction = {
        "token": "example",
        "action": "addSecondary",
        "secondary": ["developerTools"],
    }

    assert not apply_correction(
        correction,
        data["tokenToCategory"],
        set(data["categories"]),
    )


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


def test_unknown_primary_is_rejected():
    data = _data()
    with pytest.raises(ValueError, match="unknown primary category"):
        apply_correction(
            {"token": "example", "was": "utilities", "shouldBe": "unknown"},
            data["tokenToCategory"],
            set(data["categories"]),
        )


def test_reapplying_primary_cleans_secondary_duplicates():
    data = _data()
    data["tokenToCategory"]["example"]["secondary"] = ["developerTools", "utilities"]

    assert apply_correction(
        {"token": "example", "shouldBe": "utilities"},
        data["tokenToCategory"],
        set(data["categories"]),
    )
    assert data["tokenToCategory"]["example"]["secondary"] == ["developerTools"]


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
