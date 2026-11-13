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
# Titles render as plain text, so the maintainer approves exactly the name parsed here.
_ISSUE_TITLE = re.compile(r"[ ]*add tap:[ ]*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)[ ]*", re.IGNORECASE | re.ASCII)


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


def parse_issue_title(title: str) -> str | None:
    """The tap named by an 'Add tap: user/repo' issue title, or None."""
    match = _ISSUE_TITLE.fullmatch(title or "")
    if not match:
        return None
    name = match.group(1).lower()
    return None if validate_name(name) else name


def with_tap(entries: list[dict], name: str) -> list[dict]:
    if any(entry["name"] == name for entry in entries):
        return entries
    return sorted([*entries, {"name": name}], key=lambda entry: entry["name"])


def added_between(base: list[dict], head: list[dict], exclude: list[str] = ()) -> list[str]:
    """Names `head` registers that `base` does not, minus `exclude`."""
    before = {entry["name"] for entry in base}
    return sorted(entry["name"] for entry in head if entry["name"] not in before and entry["name"] not in exclude)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from style_standards import write_json

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--add", nargs="+", help="Register taps (sorted insert; no-op if present)")
    mode.add_argument("--added-between", nargs=2, type=Path, metavar=("BASE", "HEAD"),
                      help="Print the names HEAD's taps.json registers that BASE's does not")
    parser.add_argument("--only-readable", type=Path, help="Register only taps this run file lists as readable")
    parser.add_argument("--except", dest="exclude", nargs="*", default=[], help="Names --added-between leaves out")
    args = parser.parse_args(argv)
    if args.added_between:
        base, head = (json.loads(path.read_text(encoding="utf-8")).get("taps", []) for path in args.added_between)
        names = [name for name in added_between(base, head, args.exclude) if validate_name(name) is None]
        print("\n".join(names))
        return 0
    if errors := [error for name in args.add if (error := validate_name(name))]:
        raise SystemExit("; ".join(errors))
    names = args.add
    if args.only_readable:
        readable = set(json.loads(args.only_readable.read_text(encoding="utf-8")).get("readable", []))
        if skipped := [name for name in names if name not in readable]:
            print(f"not registering taps without readable casks: {' '.join(skipped)}")
        names = [name for name in names if name in readable]
    entries = load()
    for name in names:
        entries = with_tap(entries, name)
    write_json(REGISTRY_PATH, {"version": VERSION, "taps": entries}, trailing_newline=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
