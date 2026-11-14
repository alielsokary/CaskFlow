#!/usr/bin/env python3
"""PR check for taps.json: schema, names, and that new taps are reachable repos with casks."""
from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 - fixed git commands, argument lists, and no shell
import sys
import tempfile
from pathlib import Path

import tap_registry
from tap_registry import OBJECT_ID, REF

_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=_GIT_ENV, timeout=120, **kwargs)  # nosec B603 - argument list; no shell


def changed_entries(base: list[dict], head: list[dict]) -> list[dict]:
    before = {entry["name"]: entry for entry in base}
    return [entry for entry in head if before.get(entry["name"]) != entry]


def list_casks(entry: dict, run=_run, workdir: Path | None = None) -> dict[str, str] | None:
    """The tap's cask refs and their file's git object id (blob-less clone, no tap code run); None if unknown."""
    try:
        with tempfile.TemporaryDirectory(dir=workdir) as clone:
            cloned = run(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout",
                          tap_registry.remote_of(entry), clone])
            if cloned.returncode != 0:
                return None
            listing = run(["git", "ls-tree", "-r", "HEAD", "Casks"], cwd=clone)
    except (subprocess.SubprocessError, OSError):
        return None
    if listing.returncode != 0:
        return None
    files = {}
    for line in listing.stdout.splitlines():
        meta, _, path = line.partition("\t")
        kind, oid = (meta.split() + ["", "", ""])[1:3]
        ref = f"{entry['name']}/{Path(path).stem}"
        if kind == "blob" and path.endswith(".rb") and REF.fullmatch(ref) and OBJECT_ID.fullmatch(oid):
            files[ref] = oid
    # An empty listing more likely means a restructured repo than a tap that removed every cask.
    return dict(sorted(files.items())) or None


def check_tap(entry: dict, run=_run, workdir: Path | None = None) -> str | None:
    if list_casks(entry, run, workdir):
        return None
    return f"{entry['name']}: {tap_registry.remote_of(entry)} is not reachable or has no casks"


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
