#!/usr/bin/env python3
"""Validate a hand-picked cask and write it to data/house_pick.json for CaskHub's Browse hero."""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
from pathlib import Path
from typing import Callable

from style_standards import normalize, write_json

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ROOT / "categories.json"
PICK = ROOT / "data" / "house_pick.json"
BREW_HOST = "formulae.brew.sh"
TOKEN = re.compile(r"\A[a-z0-9][a-z0-9@._+-]*\Z")
NOTE_LIMIT = 120


def fetch_cask(token: str) -> dict | None:
    """Homebrew's cask JSON, or None when the token does not exist."""
    connection = http.client.HTTPSConnection(BREW_HOST, timeout=30)
    try:
        connection.request("GET", f"/api/cask/{token}.json")
        response = connection.getresponse()
        if response.status == 404:
            return None
        if response.status != 200:
            raise RuntimeError(f"Homebrew API returned HTTP {response.status} for {token}")
        return json.load(response)
    finally:
        connection.close()


def build_pick(token: str, note: str | None, known: set[str],
               fetch: Callable[[str], dict | None]) -> dict:
    """The pick to publish; raises ValueError naming the first rule it breaks."""
    token = token.strip().lower()
    _check_token(token, known, fetch)
    return {"token": token, "note": _clean_note(note)}


def _check_token(token: str, known: set[str], fetch: Callable[[str], dict | None]) -> None:
    if not TOKEN.match(token):
        raise ValueError(f"{token!r} is not a cask token")
    if token.startswith("font-"):
        raise ValueError(f"{token} is a font; fonts are not shown as the house pick")
    if token not in known:
        raise ValueError(f"{token} is not in categories.json, so CaskHub cannot show it in Browse")
    cask = fetch(token)
    if cask is None:
        raise ValueError(f"{token} does not exist in homebrew/cask")
    if cask.get("deprecated") or cask.get("disabled"):
        raise ValueError(f"{token} is deprecated or disabled in homebrew/cask")


def _clean_note(note: str | None) -> str | None:
    note = normalize(" ".join((note or "").split())) or None
    if note and len(note) > NOTE_LIMIT:
        raise ValueError(f"note is {len(note)} characters; the limit is {NOTE_LIMIT}")
    return note


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token", required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args(argv)
    known = set(json.loads(CATEGORIES.read_text(encoding="utf-8"))["tokenToCategory"])
    try:
        pick = build_pick(args.token, args.note, known, fetch_cask)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    write_json(PICK, pick, trailing_newline=True)
    print(f"house pick: {pick['token']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
