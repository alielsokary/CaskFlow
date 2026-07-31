"""Regression tests for data-publication and classification workflow contracts."""
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _workflow(name: str) -> str:
    return (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_added_dates_publish_independently_of_category_changes():
    release = _workflow("release.yml")

    assert "schedule:" in release
    assert 'cron: "47 14 * * *"' in release
    assert "python3 scripts/mine_added_dates.py" in release
    assert "overwrite_files: true" in release


def test_auto_merge_checks_null_as_a_boolean():
    classification = _workflow("classify-new-casks.yml")

    assert "--jq '.autoMergeRequest != null'" in classification
    assert "--jq '.autoMergeRequest')\" = \"null\"" not in classification


def test_pending_review_disarms_previously_enabled_auto_merge():
    classification = _workflow("classify-new-casks.yml")

    assert "review_required == 'true'" in classification
    assert "gh pr merge --disable-auto" in classification


def test_icon_publication_cuts_a_release_for_the_fresh_manifest():
    icons = _workflow("extract-icons.yml")

    assert "actions: write" in icons
    assert "gh workflow run release.yml" in icons


def test_pr_hygiene_can_assign_pull_requests():
    hygiene = _workflow("pr-hygiene.yml")

    assert "pull-requests: write" in hygiene
    assert "issues: write" not in hygiene
