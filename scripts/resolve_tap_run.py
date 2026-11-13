#!/usr/bin/env python3
"""Decide which taps a tap-pipeline run covers, and which new tap (if any) it registers."""
from __future__ import annotations

import json
import os
import subprocess

import tap_registry
from validate_taps import changed_entries


def resolve(event: str, *, base: list[dict], head: list[dict], dispatch_tap: str = "", issue_title: str = "") -> tuple[list[str], str | None]:
    registered = [entry["name"] for entry in head]
    if event == "push":
        return [entry["name"] for entry in changed_entries(base, head)], None
    if event == "schedule":
        return registered, None
    if event == "workflow_dispatch":
        name = dispatch_tap.strip().lower()
        if error := tap_registry.validate_name(name):
            raise SystemExit(error)
    elif event == "issues":
        name = tap_registry.parse_issue_title(issue_title)
        if name is None:
            raise SystemExit("the issue title is not 'Add tap: user/repo'")
    else:
        raise SystemExit(f"unsupported event {event!r}")
    return [name], None if name in registered else name


def _base_entries(before: str) -> list[dict]:
    if not before or set(before) == {"0"}:
        return []
    shown = subprocess.run(["git", "show", f"{before}:taps.json"], capture_output=True, text=True, cwd=tap_registry.REPO_ROOT)
    return json.loads(shown.stdout).get("taps", []) if shown.returncode == 0 else []


def main() -> int:
    taps, register = resolve(
        os.environ["EVENT_NAME"],
        base=_base_entries(os.environ.get("BEFORE_SHA", "")),
        head=tap_registry.load(),
        dispatch_tap=os.environ.get("DISPATCH_TAP", ""),
        issue_title=os.environ.get("ISSUE_TITLE", ""),
    )
    print(f"taps: {taps}, register: {register}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"taps={' '.join(taps)}\nregister={register or ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
