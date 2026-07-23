import pytest

from validate_pr_title import is_semantic_title


@pytest.mark.parametrize(
    "title",
    [
        "chore: Daily cask classification update",
        "fix(classifier): Handle malformed provider output",
        "feat!: Change the categories schema",
        "docs(contributing): Clarify local setup",
    ],
)
def test_semantic_titles_are_accepted(title):
    assert is_semantic_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "Daily cask classification update",
        "feature: Add categories",
        "fix missing colon",
        "fix: ",
        "Fix: Capitalized type",
    ],
)
def test_nonsemantic_titles_are_rejected(title):
    assert not is_semantic_title(title)
