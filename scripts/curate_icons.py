#!/usr/bin/env python3
"""Curated icons for casks with no extractable app icon (CLI tools, SDKs).

The extraction pipeline can only ship what a .app bundle contains; CLI-only
casks (claude-code, ngrok, gcloud-cli, …) have no bundle and no icon at the
source. This publishes official vendor assets instead — GitHub org avatars
(vendor-controlled) or brand PNGs — mapped per token in
data/curated_icons.json. Every icon is eye-verified before --publish.

Published tokens get status "curated" in icon_report.json: provenance for
audits, and it keeps them out of the extractor's candidate selection.

Run:
    python scripts/curate_icons.py                       # download only
    python scripts/curate_icons.py --publish             # push to icons branch
    python scripts/curate_icons.py --tokens ngrok codex  # subset
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from extract_icons import (
    DEFAULT_UA,
    ICON_SIZE,
    REPO_ROOT,
    load_report,
    publish_batch,
    record,
    run,
)

MAPPING_FILE = REPO_ROOT / "data" / "curated_icons.json"


def fetch_icon(url: str, dest_png: Path) -> str | None:
    """Download and normalize to a 256px PNG. Returns an error or None."""
    with tempfile.TemporaryDirectory(prefix="curated-") as td:
        raw = Path(td) / "raw"
        proc = run(["curl", "-fsSL", "--retry", "2", "-A", DEFAULT_UA,
                    "-o", str(raw), url])
        if proc.returncode != 0 or not raw.exists():
            return f"download failed: {proc.stderr.strip()[:120]}"
        proc = run(["sips", "-s", "format", "png",
                    "--resampleHeightWidthMax", ICON_SIZE,
                    str(raw), "--out", str(dest_png)])
        if proc.returncode != 0 or not dest_png.exists():
            return f"sips failed: {proc.stderr.strip()[:120]}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", nargs="*", help="Curate only these tokens")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--output-dir", type=Path,
                        default=REPO_ROOT / "icons_out" / "curated")
    args = parser.parse_args(argv)

    mapping: dict[str, str] = json.loads(MAPPING_FILE.read_text())
    if args.tokens:
        missing = [t for t in args.tokens if t not in mapping]
        if missing:
            raise SystemExit(f"not in {MAPPING_FILE.name}: {missing}")
        mapping = {t: mapping[t] for t in args.tokens}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pngs: dict[str, Path] = {}
    for token, url in sorted(mapping.items()):
        dest = args.output_dir / f"{token}.png"
        err = fetch_icon(url, dest)
        print(f"  {token}: {err or 'ok'}")
        if err is None:
            pngs[token] = dest

    if args.publish and pngs:
        report = load_report()
        for token in pngs:
            record(report, token, "curated", f"official vendor asset: {mapping[token]}")
        publish_batch(pngs, report, dirty=set(pngs))
    elif not args.publish:
        print(f"(local run — review {args.output_dir}, then --publish)")
    return 0 if len(pngs) == len(mapping) else 1


if __name__ == "__main__":
    sys.exit(main())
