"""Tests for the mine_added_dates log parser - earliest-add-wins semantics."""
from __future__ import annotations

import os
import subprocess

from mine_added_dates import current_cask_tokens, mine, parse_log, validate_current_cask_coverage


LOG = "\n".join([
    # Newest first, like git log. The 2023 sharding move re-added iterm2.
    "2023-06-01",
    "",
    "A\tCasks/i/iterm2.rb",
    "A\tCasks/f/firefox.rb",
    "2020-03-15",
    "",
    "A\tCasks/newapp.rb",
    "M\tCasks/other.rb",
    "2012-12-19",
    "",
    "A\tCasks/iterm2.rb",
    "A\tCasks/README.md",
])


def test_earliest_add_wins_across_sharding_move():
    assert parse_log(LOG)["iterm2"] == "2012-12-19"


def test_single_add_keeps_its_date():
    result = parse_log(LOG)
    assert result["newapp"] == "2020-03-15"
    assert result["firefox"] == "2023-06-01"


def test_non_ruby_and_modified_files_are_ignored():
    result = parse_log(LOG)
    assert "README" not in result
    assert "other" not in result


def test_empty_log():
    assert parse_log("") == {}


def test_mines_cask_that_has_no_category(tmp_path):
    """Recently Added must come from tap history, not category membership."""
    repo = tmp_path / "homebrew-cask"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )

    token = "brand-new-unclassified"
    cask = repo / "Casks" / "b" / f"{token}.rb"
    cask.parent.mkdir(parents=True)
    cask.write_text('cask "brand-new-unclassified" do\nend\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(cask)], check=True)
    env = os.environ | {
        "GIT_AUTHOR_DATE": "2026-07-23T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-07-23T12:00:00Z",
    }
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "Add unclassified cask"],
        check=True,
        env=env,
    )

    added = mine(repo)
    category_tokens: set[str] = set()
    assert token not in category_tokens
    assert current_cask_tokens(repo) == {token}
    assert added[token] == "2026-07-23"
    assert validate_current_cask_coverage(repo, added) == set()


def test_mines_default_branch_landing_date_for_delayed_merge(tmp_path):
    repo = tmp_path / "homebrew-cask"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )

    readme = repo / "README.md"
    readme.write_text("tap\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(readme)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "Initialize tap"],
        check=True,
    )

    subprocess.run(["git", "-C", str(repo), "switch", "-q", "-c", "new-cask"], check=True)
    cask = repo / "Casks" / "o" / "openwhispr.rb"
    cask.parent.mkdir(parents=True)
    cask.write_text('cask "openwhispr" do\nend\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(cask)], check=True)
    feature_env = os.environ | {
        "GIT_AUTHOR_DATE": "2026-07-16T10:00:00Z",
        "GIT_COMMITTER_DATE": "2026-07-16T10:00:00Z",
    }
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "Add OpenWhispr"],
        check=True,
        env=feature_env,
    )

    subprocess.run(["git", "-C", str(repo), "switch", "-q", "master"], check=True)
    readme.write_text("tap history\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(readme)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "Advance default branch"],
        check=True,
    )
    merge_env = os.environ | {
        "GIT_AUTHOR_DATE": "2026-07-23T09:27:47Z",
        "GIT_COMMITTER_DATE": "2026-07-23T09:27:47Z",
    }
    subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-ff", "new-cask", "-m", "Merge cask"],
        check=True,
        env=merge_env,
    )

    assert mine(repo)["openwhispr"] == "2026-07-23"


def test_coverage_check_catches_an_active_cask_without_a_date(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mine_added_dates.current_cask_tokens",
        lambda _repo: {"classified", "brand-new-unclassified"},
    )

    assert validate_current_cask_coverage(
        tmp_path,
        {"classified": "2026-07-22"},
    ) == {"brand-new-unclassified"}
