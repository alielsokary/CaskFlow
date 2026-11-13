#!/usr/bin/env python3
"""Registered third-party taps: names, validation, and remotes. See taps.json."""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "taps.json"
VERSION = 1

_PART = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_ISSUE_LINE = re.compile(r"^\s*tap:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


class RegistryError(Exception):
    pass


def validate_name(name: str) -> str | None:
    """None when `name` is a lowercase third-party `user/repo`, else the reason."""
    parts = name.split("/")
    if len(parts) != 2 or not all(_PART.fullmatch(part) for part in parts):
        return f"{name!r} is not a lowercase user/repo tap name"
    if parts[0] == "homebrew":
        return f"{name!r} is an official Homebrew tap"
    return None


def default_remote(name: str) -> str:
    user, repo = name.split("/")
    return f"https://github.com/{user}/homebrew-{repo}"


def remote_of(entry: dict) -> str:
    return entry.get("remote") or default_remote(entry["name"])


def _check_entry(entry: object) -> dict:
    if not isinstance(entry, dict) or set(entry) - {"name", "remote"} or "name" not in entry:
        raise RegistryError(f"entry {entry!r} must have only 'name' and optional 'remote'")
    if error := validate_name(entry["name"]):
        raise RegistryError(error)
    remote = entry.get("remote")
    if remote is not None and not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", remote):
        raise RegistryError(f"{entry['name']}: remote must be an https://github.com/<owner>/<repo> URL")
    return entry


def load(path: Path = REGISTRY_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("taps"), list):
        raise RegistryError(f"{path.name} must be {{\"version\": {VERSION}, \"taps\": [...]}}")
    entries = [_check_entry(entry) for entry in data["taps"]]
    names = [entry["name"] for entry in entries]
    if len(set(names)) != len(names):
        raise RegistryError("duplicate tap names")
    if names != sorted(names):
        raise RegistryError("taps must be sorted by name")
    return entries


def parse_issue_tap(body: str) -> str | None:
    """The validated tap from an issue's single, visible `Tap: user/repo` line."""
    body = body or ""
    lines = _ISSUE_LINE.findall(body)
    # A maintainer approves what GitHub renders, so a line hidden in a comment must not win.
    if len(lines) != 1 or len(_ISSUE_LINE.findall(_HTML_COMMENT.sub("", body))) != 1:
        return None
    name = lines[0].lower()
    return None if validate_name(name) else name


def with_tap(entries: list[dict], name: str) -> list[dict]:
    if any(entry["name"] == name for entry in entries):
        return entries
    return sorted([*entries, {"name": name}], key=lambda entry: entry["name"])


def main(argv: list[str] | None = None) -> int:
    import argparse

    from style_standards import write_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--add", required=True, help="Register a tap (sorted insert; no-op if present)")
    args = parser.parse_args(argv)
    if error := validate_name(args.add):
        raise SystemExit(error)
    write_json(REGISTRY_PATH, {"version": VERSION, "taps": with_tap(load(), args.add)}, trailing_newline=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
