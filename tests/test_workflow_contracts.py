"""Regression tests for data-publication and classification workflow contracts."""
from pathlib import Path
import os
import subprocess
import textwrap

import pytest


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


def test_back_merge_targets_develop_with_a_merge_commit():
    sync = _workflow("sync-develop.yml")

    assert "--base develop" in sync
    assert "--head master" in sync
    assert "gh pr merge --auto --merge" in sync
    assert "--squash" not in sync
    # PAT author: bot-authored PRs stall on manual workflow approval
    assert "secrets.CLASSIFY_BOT_TOKEN" in sync


def test_pr_hygiene_can_assign_pull_requests():
    hygiene = _workflow("pr-hygiene.yml")

    assert "pull-requests: write" in hygiene
    assert "issues: write" not in hygiene


def test_identity_release_merges_seed_and_publishes_manifest():
    release = _workflow("release.yml")
    assert "scripts/app_identities.py --extracted /tmp/app_identities.json --seed app_identities.json" in release
    assert "git fetch --depth=1 origin icons" in release
    assert "            app_identities.json" in release
    assert "data/app_identity_variants.json" in release
    assert "--identity-backfill" in _workflow("extract-icons.yml")


@pytest.mark.parametrize("cron,backfill,tokens,retry,expected", [
    ("43 15 * * *", "", "", "", "--limit 300"),
    ("7 3 1 * *", "", "", "", "--retry-parked"),
    ("", "true", "", "", "--identity-backfill --limit 300"),
    ("", "true", "vlc asana", "", "--tokens vlc asana"),
    ("", "true", "vlc", "true", "--retry-parked"),
])
def test_icon_workflow_routes_scheduled_and_manual_extraction(cron, backfill, tokens, retry, expected):
    workflow = _workflow("extract-icons.yml")
    assert "23 22 * * *" not in workflow  # bulk identity backfills are manually requested
    script = textwrap.dedent(workflow.split("        run: |\n", 1)[1].split("\n      # Keep", 1)[0])
    result = subprocess.run(
        ["bash", "-c", 'python3() { printf "%s\\n" "$*"; };\n' + script],
        env={**os.environ, "CRON": cron, "IDENTITY_BACKFILL": backfill,
             "TOKENS": tokens, "RETRY_PARKED": retry, "LIMIT": "300", "WORKERS": "1" if cron else "4"},
        capture_output=True, text=True, check=True,
    )
    workers = "1" if cron else "4"
    assert result.stdout.strip() == f"scripts/extract_icons.py --publish --workers {workers} {expected}"
    if cron:
        assert f'cron: "{cron}"' in workflow
