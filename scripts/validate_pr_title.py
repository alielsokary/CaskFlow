#!/usr/bin/env python3
"""Validate a pull request title against the repository's semantic format."""
from __future__ import annotations

import argparse
import re

TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)
TITLE_PATTERN = re.compile(
    rf"^(?:{'|'.join(TYPES)})(?:\([a-z0-9][a-z0-9._/-]*\))?!?:\s+\S.*$"
)


def is_semantic_title(title: str) -> bool:
    return bool(TITLE_PATTERN.fullmatch(title.strip()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("title")
    args = parser.parse_args(argv)
    if is_semantic_title(args.title):
        return 0
    parser.error(
        "title must use '<type>: <description>' or '<type>(<scope>): <description>'; "
        f"allowed types: {', '.join(TYPES)}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
