"""Tests for old_tokens rename migration in classify_new_casks."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from classify_new_casks import migrate_renames  # noqa: E402


def _existing(*tokens: str) -> dict:
    return {"tokenToCategory": {t: {"primary": "utilities", "secondary": []} for t in tokens}}


def test_rename_migrates_classification():
    existing = _existing("windsurf")
    api = [{"token": "devin-desktop", "old_tokens": ["windsurf"]}]

    renames = migrate_renames(existing, api)

    assert renames == [("windsurf", "devin-desktop")]
    assert "windsurf" not in existing["tokenToCategory"]
    assert existing["tokenToCategory"]["devin-desktop"] == {"primary": "utilities", "secondary": []}


def test_rename_onto_already_classified_token_drops_old():
    existing = {
        "tokenToCategory": {
            "old-name": {"primary": "utilities", "secondary": []},
            "new-name": {"primary": "developerTools", "secondary": ["ai"]},
        }
    }
    api = [{"token": "new-name", "old_tokens": ["old-name"]}]

    renames = migrate_renames(existing, api)

    assert renames == [("old-name", "new-name")]
    assert "old-name" not in existing["tokenToCategory"]
    # The existing classification of the target wins.
    assert existing["tokenToCategory"]["new-name"]["primary"] == "developerTools"


def test_true_removal_is_left_for_prune():
    existing = _existing("gone-forever", "still-here")
    api = [{"token": "still-here"}]

    assert migrate_renames(existing, api) == []
    # Untouched — compute_diff owns pruning true removals.
    assert "gone-forever" in existing["tokenToCategory"]


def test_still_present_token_is_never_treated_as_rename():
    # A cask can list old_tokens while the "old" token also still exists.
    existing = _existing("alias-source")
    api = [
        {"token": "alias-source"},
        {"token": "alias-target", "old_tokens": ["alias-source"]},
    ]

    assert migrate_renames(existing, api) == []
    assert "alias-source" in existing["tokenToCategory"]
