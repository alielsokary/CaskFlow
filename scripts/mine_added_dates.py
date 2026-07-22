#!/usr/bin/env python3
"""Mine each cask's earliest addition date from Homebrew's git history."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "added_dates.json"
TAP_URL = "https://github.com/Homebrew/homebrew-cask"

DATE_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clone_tap(dest: str) -> Path:
    """Blob-less partial clone: full history and trees (enough for --name-status), no blobs."""
    path = Path(dest) / "homebrew-cask"
    print(f"Cloning {TAP_URL} (blob-less) ...", flush=True)
    subprocess.run(
        ["git", "clone", "--filter=blob:none", "--no-checkout", TAP_URL, str(path)],
        check=True,
    )
    return path


def parse_log(log: str) -> dict[str, str]:
    """Newest-first log of adds; always overwriting leaves the oldest add per token."""
    added: dict[str, str] = {}
    current_date = ""
    for line in log.splitlines():
        if DATE_LINE.match(line):
            current_date = line
        elif line.startswith("A\t"):
            path = line[2:]
            if path.startswith("Casks/") and path.endswith(".rb"):
                added[Path(path).stem] = current_date
    return added


def mine(repo: Path) -> dict[str, str]:
    log = subprocess.run(
        [
            "git", "-C", str(repo), "log",
            "--diff-filter=A", "--no-renames", "--name-status",
            # Committer date = when it landed in the tap (author date can
            # predate the merge by days on slow PRs).
            "--date=short", "--format=%cd",
            "--", "Casks/",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return parse_log(log)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Path to an existing homebrew-cask clone")
    args = parser.parse_args()

    if args.repo:
        added = mine(Path(args.repo))
    else:
        with tempfile.TemporaryDirectory() as tmp:
            added = mine(clone_tap(tmp))

    if len(added) < 5000:
        print(f"Sanity check failed: only {len(added)} tokens mined", file=sys.stderr)
        return 1

    payload = {
        "version": 1,
        "generatedDate": date.today().isoformat(),
        "totalCasks": len(added),
        "tokenAddedDates": dict(sorted(added.items())),
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} ({len(added)} tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
