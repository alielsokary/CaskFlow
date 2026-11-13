#!/usr/bin/env python3
"""PR check for taps.json: schema, names, and that new taps are reachable repos with casks."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import tap_registry

_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=_GIT_ENV, timeout=120, **kwargs)


def changed_entries(base: list[dict], head: list[dict]) -> list[dict]:
    before = {entry["name"]: entry for entry in base}
    return [entry for entry in head if before.get(entry["name"]) != entry]


def check_tap(entry: dict, run=_run, workdir: Path | None = None) -> str | None:
    remote = tap_registry.remote_of(entry)
    if run(["git", "ls-remote", "--exit-code", remote, "HEAD"]).returncode != 0:
        return f"{entry['name']}: {remote} is not reachable"
    with tempfile.TemporaryDirectory(dir=workdir) as clone:
        cloned = run(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout", remote, clone])
        if cloned.returncode != 0:
            return f"{entry['name']}: {remote} could not be cloned"
        listing = run(["git", "ls-tree", "HEAD", "Casks"], cwd=clone)
    if listing.returncode != 0 or "Casks" not in listing.stdout:
        return f"{entry['name']}: {remote} has no Casks/ folder"
    return None


def _base_entries(base_ref: str) -> list[dict]:
    shown = _run(["git", "show", f"{base_ref}:taps.json"], cwd=tap_registry.REPO_ROOT)
    if shown.returncode != 0:
        return []
    return json.loads(shown.stdout).get("taps", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/develop")
    args = parser.parse_args(argv)
    try:
        head = tap_registry.load()
    except (tap_registry.RegistryError, ValueError) as error:
        print(f"taps.json: {error}", file=sys.stderr)
        return 1
    errors = [error for entry in changed_entries(_base_entries(args.base_ref), head) if (error := check_tap(entry))]
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
