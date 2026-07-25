#!/usr/bin/env python3
"""Normalize typographic slop out of authored prose and generated data."""
# Only slop is listed. Real content (CJK, emoji, accented Latin) and meaningful
# notation (copyright, section, >=, arrows) are deliberately absent so they
# survive untouched. Characters are built with chr() so this file stays pure
# ASCII and passes its own guard.
from __future__ import annotations

import json
from pathlib import Path

REPLACEMENTS = {
    chr(0x2014): "-",    # EM DASH
    chr(0x2013): "-",    # EN DASH
    chr(0x2015): "-",    # HORIZONTAL BAR
    chr(0x2212): "-",    # MINUS SIGN
    chr(0x201C): '"',    # LEFT DOUBLE QUOTATION MARK
    chr(0x201D): '"',    # RIGHT DOUBLE QUOTATION MARK
    chr(0x2018): "'",    # LEFT SINGLE QUOTATION MARK
    chr(0x2019): "'",    # RIGHT SINGLE QUOTATION MARK
    chr(0x2026): "...",  # HORIZONTAL ELLIPSIS
    chr(0x00A0): " ",    # NO-BREAK SPACE
    chr(0x202F): " ",    # NARROW NO-BREAK SPACE
    chr(0x200B): "",     # ZERO WIDTH SPACE
    chr(0x2060): "",     # WORD JOINER
    chr(0xFEFF): "",     # ZERO WIDTH NO-BREAK SPACE (BOM)
    chr(0x00AD): "",     # SOFT HYPHEN
}


def normalize(text: str) -> str:
    """Replace every slop character with its ASCII equivalent."""
    for slop, ascii_form in REPLACEMENTS.items():
        text = text.replace(slop, ascii_form)
    return text


def violations(text: str) -> list[str]:
    """Slop characters present in text, ordered by code point for stable reports."""
    return sorted(slop for slop in REPLACEMENTS if slop in text)


def normalize_data(value):
    """Normalize every string inside a JSON-shaped structure, in place of the whole."""
    if isinstance(value, str):
        return normalize(value)
    if isinstance(value, dict):
        return {normalize_data(k): normalize_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_data(item) for item in value]
    return value


def write_text(path: Path | str, text: str) -> None:
    """Write plain text with slop normalized away."""
    # For JSON use write_json: normalizing serialized JSON would turn a curly
    # quote into a bare one and corrupt the string it sits in.
    Path(path).write_text(normalize(text), encoding="utf-8")


def write_json(path: Path | str, data, *, trailing_newline: bool = False) -> None:
    """Write JSON with slop normalized before serialization, so quotes stay escaped."""
    # Every generated JSON file routes through here, so scraped vendor typography
    # cannot reach a tracked file no matter which script produced it.
    text = json.dumps(normalize_data(data), indent=2, ensure_ascii=False)
    if trailing_newline:
        text += "\n"
    Path(path).write_text(text, encoding="utf-8")
