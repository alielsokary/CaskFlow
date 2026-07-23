"""Repository-wide text style checks."""
from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_tracked_files_contain_no_em_dashes():
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
        except UnicodeDecodeError:
            continue
        if "\N{EM DASH}" in text:
            offenders.append(relative)

    assert offenders == [], f"Unicode em dashes found in: {', '.join(offenders)}"
