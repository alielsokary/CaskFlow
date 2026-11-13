#!/usr/bin/env python3
"""Extract icons for tap casks into taps/<user>/<repo>/<token>.png on the icons branch.

Reuses the official extractor. Runs no tap code: it reads the collect step's JSON.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import extract_icons
from extract_icons import MAX_ATTEMPTS, ExtractError, _git, write_icon_manifest
from icons_state import ICONS_BRANCH, REPO_ROOT
from style_standards import write_json

TAPS_DIR = "taps"
REPORT = f"{TAPS_DIR}/icon_report.json"
FLUSH_EVERY = 25


def icon_path(ref: str) -> str:
    return f"{TAPS_DIR}/{ref}.png"


def _parked(entry: dict) -> bool:
    return entry["status"] in ("no_icon", "car_only") or (
        entry["status"] == "failed" and entry.get("attempts", 0) >= MAX_ATTEMPTS
    )


def pending(collected: dict, published: set[str], report: dict[str, dict]) -> list[dict]:
    aliased = collected.get("sameAs", {})
    return [
        cask for ref, cask in sorted(collected["casks"].items())
        if ref not in aliased and ref not in published and not (ref in report and _parked(report[ref]))
    ]


def stage(wt: Path, pngs: dict[str, Path], report: dict[str, dict]) -> None:
    """Copy icons and the tap report into a checked-out icons branch; nothing outside taps/."""
    for ref, png in pngs.items():
        target = wt / icon_path(ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(png, target)
    (wt / TAPS_DIR).mkdir(exist_ok=True)
    write_json(wt / REPORT, dict(sorted(report.items())))


def _branch_state() -> tuple[set[str], dict[str, dict]]:
    if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
        raise SystemExit(f"git fetch origin {ICONS_BRANCH} failed")
    files = _git(REPO_ROOT, "ls-tree", "-r", "--name-only", "FETCH_HEAD", "--", TAPS_DIR).stdout.splitlines()
    published = {name.removeprefix(f"{TAPS_DIR}/").removesuffix(".png") for name in files if name.endswith(".png")}
    shown = _git(REPO_ROOT, "show", f"FETCH_HEAD:{REPORT}")
    return published, (json.loads(shown.stdout) if shown.returncode == 0 else {})


def publish(pngs: dict[str, Path], report: dict[str, dict]) -> None:
    wt = Path(tempfile.mkdtemp(prefix="tap-icons-wt-"))
    added = False
    try:
        extract_icons._add_icons_worktree(wt)
        added = True
        stage(wt, pngs, report)
        if _git(wt, "add", "-A", "--", TAPS_DIR).returncode != 0:
            raise ExtractError("Cannot stage tap icons")
        write_icon_manifest(wt)
        _git(wt, "add", "icons.json")
        if _git(wt, "diff", "--cached", "--quiet").returncode == 0:
            return
        changed = _git(wt, "diff", "--cached", "--name-only", "--diff-filter=AM", "--", f"{TAPS_DIR}/*.png", "icons.json")
        extract_icons._commit_and_push(wt, pngs)
        for filename in changed.stdout.splitlines():
            extract_icons.purge_file(filename)
    finally:
        if added:
            _git(REPO_ROOT, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)


def _record(report: dict, ref: str, status: str, detail: str) -> None:
    attempts = report.get(ref, {}).get("attempts", 0) + (1 if status == "failed" else 0)
    report[ref] = {"status": status, "reason": detail, **({"attempts": attempts} if status == "failed" else {})}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="Cap this run (0 = all pending)")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "icons_out")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)

    collected = json.loads(args.collected.read_text(encoding="utf-8"))
    published, report = _branch_state()
    batch = pending(collected, published, report)
    if args.limit:
        batch = batch[: args.limit]
    print(f"tap icons to extract: {len(batch)}")

    ready: dict[str, Path] = {}
    for cask in batch:
        ref = cask["full_token"]
        folder = args.output_dir / TAPS_DIR / Path(ref).parent
        folder.mkdir(parents=True, exist_ok=True)
        status, detail = extract_icons._extract_status(cask, folder)
        png = folder / f"{cask['token']}.png"
        if status == "ok" and png.exists():
            ready[ref] = png
            report.pop(ref, None)
        else:
            _record(report, ref, status, detail)
        if args.publish and len(ready) >= FLUSH_EVERY:
            publish(ready, report)
            ready = {}
    if args.publish:
        publish(ready, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
