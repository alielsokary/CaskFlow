"""Shared shell/git helpers and icon-report state on the icons branch."""
# Leaf module: extract_icons.py and curate_icons.py build on these; this file
# imports nothing back from them.
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_FILE = "icon_report.json"  # lives on the icons branch, beside the PNGs
ICONS_BRANCH = "icons"


class ExtractError(Exception):
    """Recoverable per-cask failure — recorded in the report, never fatal."""


def run(cmd: list[str], input_text: str | None = None, **kw) -> subprocess.CompletedProcess:
    if input_text is None:
        kw["stdin"] = subprocess.DEVNULL
    return subprocess.run(cmd, capture_output=True, text=True, input=input_text, **kw)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(cwd), *args])


def load_report() -> dict[str, dict]:
    """Read icon_report.json from the icons branch — its only home."""
    if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
        raise SystemExit(f"git fetch origin {ICONS_BRANCH} failed — branch missing?")
    show = _git(REPO_ROOT, "show", f"FETCH_HEAD:{REPORT_FILE}")
    if show.returncode != 0:
        return {}  # first run after migration
    return json.loads(show.stdout)


def report_json(report: dict[str, dict]) -> str:
    return json.dumps(dict(sorted(report.items())), indent=2, ensure_ascii=False) + "\n"


def record(report: dict, token: str, status: str, reason: str) -> None:
    prev = report.get(token, {})
    report[token] = {
        "status": status,
        "reason": reason,
        "attempts": prev.get("attempts", 0) + (1 if status == "failed" else 0),
        "updated": str(date.today()),
    }


def published_tokens() -> set[str]:
    """Tokens already on the icons branch — the done-list."""
    # One tree listing, no pagination.
    if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
        raise SystemExit(f"git fetch origin {ICONS_BRANCH} failed — branch missing?")
    ls = _git(REPO_ROOT, "ls-tree", "-r", "--name-only", "FETCH_HEAD")
    if ls.returncode != 0:
        raise SystemExit(f"git ls-tree failed: {ls.stderr.strip()[:200]}")
    return {Path(n).stem for n in ls.stdout.split() if n.endswith(".png")}
