"""Tests for the mine_added_dates log parser — earliest-add-wins semantics."""
from __future__ import annotations

from mine_added_dates import parse_log


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
