#!/usr/bin/env python3
"""Extract icons for tap casks into taps/<user>/<repo>/<token>.png on the icons branch."""
# Reuses the official extractor. Runs no tap code: it reads the collect step's JSON.
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
from tap_registry import REF, tap_of
from collect_tap_casks import load_official, trusted_view
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


MAX_PNG_BYTES = 1024 * 1024
MAX_ICON_SIDE = 256
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_problem(data: bytes) -> str | None:
    """None for a well-formed 8-bit RGB/RGBA PNG no larger than our icons, else why not."""
    if len(data) > MAX_PNG_BYTES:
        return "too large"
    if not data.startswith(_PNG_SIGNATURE):
        return "not a png"
    parsed = _chunks(data)
    if isinstance(parsed, str):
        return parsed
    chunks, end = parsed
    if end != len(data):
        return "data after IEND"
    header = _ihdr(chunks)
    if header is None:
        return "bad IHDR"
    return _format_problem(header) or _pixels_problem(header, b"".join(body for kind, body in chunks if kind == b"IDAT"))


def _chunks(data: bytes) -> tuple[list[tuple[bytes, bytes]], int] | str:
    """The (type, body) chunks through IEND and where IEND ends, or why the file is malformed."""
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(_PNG_SIGNATURE)
    while not chunks or chunks[-1][0] != b"IEND":
        if offset + 12 > len(data):
            return "truncated"
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        end = offset + 12 + length
        if end > len(data):
            return "truncated"
        body = data[offset + 8:end - 4]
        if struct.unpack(">I", data[end - 4:end])[0] != zlib.crc32(kind + body):
            return "bad chunk checksum"
        chunks.append((kind, body))
        offset = end
    return chunks, offset


def _ihdr(chunks: list[tuple[bytes, bytes]]) -> tuple | None:
    kind, body = chunks[0]
    if kind != b"IHDR" or len(body) != 13 or [k for k, _ in chunks].count(b"IHDR") != 1:
        return None
    return struct.unpack(">IIBBBBB", body)


def _format_problem(header: tuple) -> str | None:
    width, height, depth, color, compression, filtering, interlace = header
    if not (1 <= width <= MAX_ICON_SIDE and 1 <= height <= MAX_ICON_SIDE):
        return "bad dimensions"
    if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
        return "unsupported format"
    return None


def _pixels_problem(header: tuple, idat: bytes) -> str | None:
    width, height, _, color = header[:4]
    row = 1 + width * (3 if color == 2 else 4)
    inflater = zlib.decompressobj()
    try:
        # Bounded, so a small IDAT can't inflate to gigabytes before the size check.
        pixels = inflater.decompress(idat, height * row + 1)
    except zlib.error:
        return "bad image data"
    if len(pixels) != height * row or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        return "bad image data"
    if any(pixels[i] > 4 for i in range(0, len(pixels), row)):
        return "bad row filter"
    return None


def _tap_folder(root: Path, tap: str) -> Path | None:
    folder = (root / tap).resolve()
    return folder if tap_registry.validate_name(tap) is None and folder.is_relative_to(root) and folder != root else None


def stage(wt: Path, pngs: dict[str, Path], report: dict[str, dict] | None, taps: list[str], run: dict | None = None) -> None:
    """Copy icons into a checked-out icons branch and replace only `taps`' report entries; nothing outside taps/."""
    # `run` (from classify) names each listed tap's refs that keep icons and the taps removed from the registry;
    # other icons and report entries of those taps are deleted. `report` None (no extraction output) keeps the
    # branch report as it is.
    root = (wt / TAPS_DIR).resolve()
    _copy_icons(wt, root, pngs)
    current, pruned = (run or {}).get("current", {}), set((run or {}).get("prunedTaps", []))
    _drop_stale_icons(root, current, pruned, pngs)

    def kept(ref: str) -> bool:
        return tap_of(ref) not in pruned and (tap_of(ref) not in current or ref in current[tap_of(ref)] or ref in pngs)

    branch = json.loads((wt / REPORT).read_text(encoding="utf-8")) if (wt / REPORT).exists() else {}
    merged = branch if report is None else {ref: entry for ref, entry in branch.items() if tap_of(ref) not in taps} | report
    write_json(wt / REPORT, dict(sorted((ref, entry) for ref, entry in merged.items() if kept(ref))))


def _copy_icons(wt: Path, root: Path, pngs: dict[str, Path]) -> None:
    for ref, png in pngs.items():
        target = (wt / icon_path(ref)).resolve()
        if not REF.fullmatch(ref) or not target.is_relative_to(root):
            raise ValueError(f"refusing to write tap icon outside {TAPS_DIR}/: {ref!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(png, target)
    root.mkdir(exist_ok=True)


def _drop_stale_icons(root: Path, current: dict[str, list[str]], pruned: set[str], pngs: dict[str, Path]) -> None:
    for tap, refs in current.items():
        folder = _tap_folder(root, tap)
        for png in folder.glob("*.png") if folder else []:
            if f"{tap}/{png.stem}" not in refs and f"{tap}/{png.stem}" not in pngs:
                png.unlink()
    for tap in pruned:
        if (folder := _tap_folder(root, tap)) and folder.exists():
            shutil.rmtree(folder)


def _branch_state() -> tuple[set[str], dict[str, dict]]:
    if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
        raise SystemExit(f"git fetch origin {ICONS_BRANCH} failed")
    files = _git(REPO_ROOT, "ls-tree", "-r", "--name-only", "FETCH_HEAD", "--", TAPS_DIR).stdout.splitlines()
    published = {name.removeprefix(f"{TAPS_DIR}/").removesuffix(".png") for name in files if name.endswith(".png")}
    shown = _git(REPO_ROOT, "show", f"FETCH_HEAD:{REPORT}")
    return published, (json.loads(shown.stdout) if shown.returncode == 0 else {})


def publish(pngs: dict[str, Path], report: dict[str, dict] | None, taps: list[str], run: dict | None = None) -> None:
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


def _json_object(path: Path | None) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
    return raw if isinstance(raw, dict) else {}


def _tap_name(value: object) -> bool:
    return isinstance(value, str) and tap_registry.validate_name(value) is None


def _refs_of(tap: str, refs: list) -> list[str]:
    return [ref for ref in refs if isinstance(ref, str) and REF.fullmatch(ref) and tap_of(ref) == tap]


def load_run(path: Path | None) -> dict:
    """The run file from classify, keeping only well-formed names."""
    raw = _json_object(path)
    current = raw.get("current") if isinstance(raw.get("current"), dict) else {}
    pruned = raw.get("prunedTaps") if isinstance(raw.get("prunedTaps"), list) else []
    return {
        "current": {tap: _refs_of(tap, refs) for tap, refs in current.items() if _tap_name(tap) and isinstance(refs, list)},
        "prunedTaps": [tap for tap in pruned if _tap_name(tap)],
    }


def publish_from(root: Path, taps: list[str], run_file: Path | None, files_path: Path | None = None) -> None:
    """Publish what an extraction job left in `root`, and classify's removals, for this run's taps only."""
    report_file = root / REPORT
    run = load_run(run_file)
    if not report_file.exists():
        if run["current"] or run["prunedTaps"]:
            publish({}, None, taps, run)
        return
    report = {ref: entry for ref, entry in _json_object(report_file).items() if tap_of(ref) in taps}
    pngs = _valid_pngs(root / TAPS_DIR, taps, report)
    publish(pngs, report, taps, run)
    if files_path and files_path.exists():
        _unsettle(files_path, report)


def _valid_pngs(folder: Path, taps: list[str], report: dict[str, dict]) -> dict[str, Path]:
    """The run's icons; the extraction job opened untrusted installers, so malformed PNGs are refused."""
    pngs = {}
    for png in sorted(folder.glob("*/*/*.png")):
        ref = str(png.relative_to(folder)).removesuffix(".png")
        problem = "too large" if png.stat().st_size > MAX_PNG_BYTES else png_problem(png.read_bytes())
        if tap_of(ref) in taps and problem:
            _record(report, ref, "failed", f"invalid png: {problem}")
        elif tap_of(ref) in taps:
            pngs[ref] = png
    return pngs


def _unsettle(files_path: Path, report: dict[str, dict]) -> None:
    # A cask whose icon is worth retrying stays unsettled, so the next sweep collects its tap again.
    retry = {ref for ref, entry in report.items() if entry.get("status") == "failed" and entry.get("attempts", 0) < MAX_ATTEMPTS}
    files = _json_object(files_path)
    write_json(files_path, {ref: oid for ref, oid in files.items() if ref not in retry}, trailing_newline=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected", type=Path)
    parser.add_argument("--taps", nargs="+", help="Taps this run was asked to process")
    parser.add_argument("--limit", type=int, default=0, help="Cap this run (0 = all pending)")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "icons_out")
    parser.add_argument("--publish-from", type=Path, help="Publish a previous extraction's output and exit")
    parser.add_argument("--run-file", type=Path, help="classify's data/tap_run.json (removals) when publishing")
    parser.add_argument("--files", type=Path, help="data/tap_files.json; icons worth retrying are unsettled")
    args = parser.parse_args(argv)
    if not args.taps:
        parser.error("--taps is required")
    if invalid := [error for name in args.taps if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))
    if args.publish_from:
        publish_from(args.publish_from, args.taps, args.run_file, args.files)
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

    _extract(batch, args.output_dir, report)
    return 0


def _extract(batch: list[dict], output_dir: Path, report: dict[str, dict]) -> None:
    (output_dir / TAPS_DIR).mkdir(parents=True, exist_ok=True)
    # Written up front and after every cask so a timed-out job still hands its progress to publish.
    write_json(output_dir / REPORT, dict(sorted(report.items())))
    for cask in batch:
        ref = cask["full_token"]
        folder = output_dir / TAPS_DIR / Path(ref).parent
        folder.mkdir(parents=True, exist_ok=True)
        safe = download_view(cask)
        status, detail = ("failed", "download url is not a public https url") if safe is None \
            else extract_icons._extract_status(safe, folder)
        if status == "ok" and (folder / f"{cask['token']}.png").exists():
            report.pop(ref, None)
        else:
            _record(report, ref, status, detail)
        write_json(output_dir / REPORT, dict(sorted(report.items())))


if __name__ == "__main__":
    raise SystemExit(main())
