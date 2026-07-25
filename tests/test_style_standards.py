"""Repository-wide text style standard checks."""
# Slop characters are built with chr() on purpose: literal ones would make this
# file fail its own guard.
from __future__ import annotations

import json
import subprocess
import unicodedata
from pathlib import Path

import style_standards


REPO_ROOT = Path(__file__).resolve().parent.parent

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
ELLIPSIS = chr(0x2026)
LEFT_QUOTE = chr(0x201C)
RIGHT_QUOTE = chr(0x201D)
APOSTROPHE = chr(0x2019)
NBSP = chr(0x00A0)
ZERO_WIDTH_SPACE = chr(0x200B)
SOFT_HYPHEN = chr(0x00AD)
BYTE_ORDER_MARK = chr(0xFEFF)


def test_normalize_replaces_slop_characters():
    raw = (
        f"Claude Code {EM_DASH} a GUI app{ELLIPSIS} "
        f"{LEFT_QUOTE}quoted{RIGHT_QUOTE}, it{APOSTROPHE}s 0{EN_DASH}2"
    )
    assert style_standards.normalize(raw) == 'Claude Code - a GUI app... "quoted", it\'s 0-2'


def test_normalize_strips_invisible_characters():
    raw = f"a{ZERO_WIDTH_SPACE}b{SOFT_HYPHEN}c{BYTE_ORDER_MARK}d{NBSP}e"
    assert style_standards.normalize(raw) == "abcd e"


def test_normalize_preserves_non_english_content():
    """Scraped homepages include CJK and emoji: the standard must not mangle them."""
    for text in (chr(0x65E5) + chr(0x672C) + chr(0x8A9E), chr(0x4E2D) + chr(0x6587), chr(0x1F680)):
        assert style_standards.normalize(text) == text


def test_violations_names_the_offending_characters():
    assert style_standards.violations("plain ascii") == []
    assert style_standards.violations(f"a {EM_DASH} b {ELLIPSIS}") == [EM_DASH, ELLIPSIS]


def test_write_text_normalizes_before_writing(tmp_path):
    target = tmp_path / "out.json"
    style_standards.write_text(target, f'{{"title": "Claude Code {EM_DASH} a GUI app"}}')
    assert target.read_text(encoding="utf-8") == '{"title": "Claude Code - a GUI app"}'


def test_write_json_escapes_normalized_quotes(tmp_path):
    """Curly quotes become straight ones, so they must be escaped by the serializer."""
    # Normalizing already-serialized JSON would inject a bare quote into a
    # string value and corrupt the file.
    target = tmp_path / "out.json"
    style_standards.write_json(target, [{"title": f"{LEFT_QUOTE}Smart{RIGHT_QUOTE} app"}])
    assert json.loads(target.read_text(encoding="utf-8")) == [{"title": '"Smart" app'}]


def test_write_json_normalizes_nested_values(tmp_path):
    target = tmp_path / "out.json"
    style_standards.write_json(target, {"a": {"b": [f"x{EM_DASH}y"]}})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": {"b": ["x-y"]}}


def test_write_json_preserves_non_english_content(tmp_path):
    target = tmp_path / "out.json"
    japanese = chr(0x65E5) + chr(0x672C) + chr(0x8A9E)
    style_standards.write_json(target, [{"title": japanese}])
    assert json.loads(target.read_text(encoding="utf-8")) == [{"title": japanese}]


def test_normalize_data_passes_through_non_strings():
    """categories.json carries ints and bools alongside its text."""
    payload = {"totalCasks": 3819, "ok": True, "missing": None, "ratio": 0.75}
    assert style_standards.normalize_data(payload) == payload


def test_write_json_can_append_trailing_newline(tmp_path):
    target = tmp_path / "out.json"
    style_standards.write_json(target, {"a": 1}, trailing_newline=True)
    assert target.read_text(encoding="utf-8").endswith("}\n")


def test_tracked_files_meet_style_standards():
    tracked = subprocess.run(
        ["/usr/bin/git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    offenders: list[str] = []

    for relative_bytes in tracked:
        if not relative_bytes:
            continue
        relative = relative_bytes.decode("utf-8")
        path = REPO_ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        found = style_standards.violations(text)
        if found:
            names = ", ".join(unicodedata.name(c, repr(c)) for c in found)
            offenders.append(f"{relative} ({names})")

    assert offenders == [], "Style standard violations in:\n  " + "\n  ".join(offenders)
