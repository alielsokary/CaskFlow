#!/usr/bin/env python3
"""Extract icons for tap casks into taps/<user>/<repo>/<token>.png on the icons branch.

Reuses the official extractor. Runs no tap code: it reads the collect step's JSON.
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

import extract_icons
import tap_registry
from collect_tap_casks import _REF, load_official, trusted_view
from safe_url import is_public_url
from extract_icons import MAX_ATTEMPTS, ExtractError, _git, write_icon_manifest
from icons_state import ICONS_BRANCH, REPO_ROOT
from style_standards import write_json

TAPS_DIR = "taps"
REPORT = f"{TAPS_DIR}/icon_report.json"


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


def download_view(cask: dict) -> dict | None:
    """A tap cask as the downloader may see it: https, public host, no tap-chosen headers or cookies."""
    if not is_public_url(cask.get("url") or "", schemes=("https",)):
        return None
    specs = {key: value for key, value in (cask.get("url_specs") or {}).items() if key in ("user_agent", "referer")}
    return {**cask, "url_specs": specs, "https_only": True}


def _tap(ref: str) -> str:
    return ref.rsplit("/", 1)[0]


MAX_PNG_BYTES = 1024 * 1024
MAX_ICON_SIDE = 256
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_problem(data: bytes) -> str | None:
    """None for a well-formed 8-bit RGB/RGBA PNG no larger than our icons, else why not."""
    if len(data) > MAX_PNG_BYTES:
        return "too large"
    if not data.startswith(_PNG_SIGNATURE):
        return "not a png"
    offset, header, idat = len(_PNG_SIGNATURE), None, bytearray()
    while True:
        if offset + 12 > len(data):
            return "truncated"
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        end = offset + 12 + length
        if end > len(data):
            return "truncated"
        body = data[offset + 8:offset + 8 + length]
        if struct.unpack(">I", data[end - 4:end])[0] != zlib.crc32(kind + body):
            return "bad chunk checksum"
        if header is None and kind != b"IHDR":
            return "first chunk is not IHDR"
        if kind == b"IHDR":
            if length != 13 or header is not None:
                return "bad IHDR"
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        offset = end
        if kind == b"IEND":
            break
    width, height, depth, color, compression, filtering, interlace = header
    if not (1 <= width <= MAX_ICON_SIDE and 1 <= height <= MAX_ICON_SIDE):
        return "bad dimensions"
    if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
        return "unsupported format"
    if offset != len(data):
        return "data after IEND"
    try:
        pixels = zlib.decompress(bytes(idat))
    except zlib.error:
        return "bad image data"
    if len(pixels) != height * (1 + width * (3 if color == 2 else 4)):
        return "bad image data"
    return None


def _tap_folder(root: Path, tap: str) -> Path | None:
    folder = (root / tap).resolve()
    return folder if tap_registry.validate_name(tap) is None and folder.is_relative_to(root) and folder != root else None


def stage(wt: Path, pngs: dict[str, Path], report: dict[str, dict], taps: list[str], run: dict | None = None) -> None:
    """Copy icons into a checked-out icons branch and replace only `taps`' report entries; nothing outside taps/.

    `run` (from classify) names each fully read tap's refs that keep icons and the taps removed from the registry;
    other icons and report entries of those taps are deleted.
    """
    root = (wt / TAPS_DIR).resolve()
    for ref, png in pngs.items():
        target = (wt / icon_path(ref)).resolve()
        if not _REF.fullmatch(ref) or not target.is_relative_to(root):
            raise ValueError(f"refusing to write tap icon outside {TAPS_DIR}/: {ref!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(png, target)
    (wt / TAPS_DIR).mkdir(exist_ok=True)
    current = (run or {}).get("current", {})
    pruned = set((run or {}).get("prunedTaps", []))
    for tap, refs in current.items():
        if folder := _tap_folder(root, tap):
            for png in folder.glob("*.png"):
                if f"{tap}/{png.stem}" not in refs:
                    png.unlink()
    for tap in pruned:
        if (folder := _tap_folder(root, tap)) and folder.exists():
            shutil.rmtree(folder)

    def kept(ref: str) -> bool:
        return _tap(ref) not in pruned and (_tap(ref) not in current or ref in current[_tap(ref)])

    branch = json.loads((wt / REPORT).read_text(encoding="utf-8")) if (wt / REPORT).exists() else {}
    merged = {ref: entry for ref, entry in branch.items() if _tap(ref) not in taps} | report
    write_json(wt / REPORT, dict(sorted((ref, entry) for ref, entry in merged.items() if kept(ref))))


def _branch_state() -> tuple[set[str], dict[str, dict]]:
    if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
        raise SystemExit(f"git fetch origin {ICONS_BRANCH} failed")
    files = _git(REPO_ROOT, "ls-tree", "-r", "--name-only", "FETCH_HEAD", "--", TAPS_DIR).stdout.splitlines()
    published = {name.removeprefix(f"{TAPS_DIR}/").removesuffix(".png") for name in files if name.endswith(".png")}
    shown = _git(REPO_ROOT, "show", f"FETCH_HEAD:{REPORT}")
    return published, (json.loads(shown.stdout) if shown.returncode == 0 else {})


def publish(pngs: dict[str, Path], report: dict[str, dict], taps: list[str], run: dict | None = None) -> None:
    wt = Path(tempfile.mkdtemp(prefix="tap-icons-wt-"))
    added = False
    try:
        extract_icons._add_icons_worktree(wt)
        added = True
        stage(wt, pngs, report, taps, run)
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


def load_run(path: Path | None) -> dict:
    """classify's run file, keeping only well-formed names."""
    raw = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
    current = raw.get("current") if isinstance(raw.get("current"), dict) else {}
    pruned = raw.get("prunedTaps") if isinstance(raw.get("prunedTaps"), list) else []
    return {
        "current": {tap: [ref for ref in refs if isinstance(ref, str) and _REF.fullmatch(ref) and _tap(ref) == tap]
                    for tap, refs in current.items()
                    if isinstance(tap, str) and tap_registry.validate_name(tap) is None and isinstance(refs, list)},
        "prunedTaps": [tap for tap in pruned if isinstance(tap, str) and tap_registry.validate_name(tap) is None],
    }


def publish_from(root: Path, taps: list[str], run_file: Path | None) -> None:
    """Publish what an extraction job left in `root`, and classify's removals, for this run's taps only."""
    report_file = root / REPORT
    run = load_run(run_file)
    if not report_file.exists() and not run["current"] and not run["prunedTaps"]:
        print("no tap icon extraction output or removals; nothing to publish")
        return
    folder = root / TAPS_DIR
    pngs = {ref: png for png in sorted(folder.glob("*/*/*.png"))
            if _tap(ref := str(png.relative_to(folder)).removesuffix(".png")) in taps}
    report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else {}
    report = {ref: entry for ref, entry in report.items() if _tap(ref) in taps}
    # The extraction job opened untrusted installers; only well-formed PNGs reach the icons branch.
    for ref, png in list(pngs.items()):
        if problem := png_problem(png.read_bytes()):
            del pngs[ref]
            _record(report, ref, "failed", f"invalid png: {problem}")
    publish(pngs, report, taps, run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected", type=Path)
    parser.add_argument("--taps", nargs="+", help="Taps this run was asked to process")
    parser.add_argument("--limit", type=int, default=0, help="Cap this run (0 = all pending)")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "icons_out")
    parser.add_argument("--publish-from", type=Path, help="Publish a previous extraction's output and exit")
    parser.add_argument("--run-file", type=Path, help="classify's data/tap_run.json (removals) when publishing")
    args = parser.parse_args(argv)
    if not args.taps:
        parser.error("--taps is required")
    if invalid := [error for name in args.taps if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))
    if args.publish_from:
        publish_from(args.publish_from, args.taps, args.run_file)
        return 0
    if not args.collected:
        parser.error("--collected is required to extract")

    # The collect job ran tap code, so its file is only trusted through this view.
    collected = trusted_view(json.loads(args.collected.read_text(encoding="utf-8")), args.taps, load_official())
    published, report = _branch_state()
    batch = pending(collected, published, report)
    if args.limit:
        batch = batch[: args.limit]
    print(f"tap icons to extract: {len(batch)}")

    folder_root = args.output_dir / TAPS_DIR
    for cask in batch:
        ref = cask["full_token"]
        folder = folder_root / Path(ref).parent
        folder.mkdir(parents=True, exist_ok=True)
        safe = download_view(cask)
        status, detail = ("failed", "download url is not a public https url") if safe is None \
            else extract_icons._extract_status(safe, folder)
        if status == "ok" and (folder / f"{cask['token']}.png").exists():
            report.pop(ref, None)
        else:
            _record(report, ref, status, detail)
        # Written after every cask so a timed-out job still hands its progress to publish.
        folder_root.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / REPORT, dict(sorted(report.items())))
    folder_root.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / REPORT, dict(sorted(report.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
