#!/usr/bin/env python3
"""Mine the date each cask was added to Homebrew/homebrew-cask."""
# Homebrew's API has no "date added" field, so the tap's git history is the
# source of truth: the date a cask entered the tap is the author date of the
# commit that created its file. Emits added_dates.json (token -> YYYY-MM-DD),
# published as a release asset alongside categories.json for CaskHub.
#
# The earliest add per token wins — the 2023 letter-sharding move
# (Casks/foo.rb -> Casks/f/foo.rb) re-added every file, so the latest add is
# often wrong. `--no-renames` keeps those moves visible as plain adds.
#
# Run:
#     python scripts/mine_added_dates.py                  # clones a fresh copy
#     python scripts/mine_added_dates.py --repo ~/tap     # reuses a local clone
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
    # The oldest add absorbs the sharding move's re-adds.
    added: dict[str, str] = {}
    current_date = ""
    for line in log.splitlines():
        if DATE_LINE.match(line):
            current_date = line
        elif line.startswith("A\t"):
            path = line[2:]
            if path.startswith("Casks/") and path.endswith(".rb"):
                # ponytail: renamed casks (old_tokens) get their rename date,
                # not the original add — rare, and only inflates "recent" a bit.
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
