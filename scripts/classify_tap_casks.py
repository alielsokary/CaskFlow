#!/usr/bin/env python3
"""Classify tap casks from the collect step's output with the official classifier, keyed by full name."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

import tap_registry
from tap_registry import tap_of
from classify_new_casks import CATEGORIES_PATH, HOMEPAGE_WORKERS, LLM_WORKERS_BY_PROVIDER, REPO_ROOT, classify_parallel
from collect_tap_casks import load_official, trusted_view
from fetch_homepages import fetch_one
from safe_url import is_public_url, public_opener
from llm_client import Classification, LLMClient
from prompts import CategoryCatalog
from style_standards import write_json

TAP_CATEGORIES_PATH = REPO_ROOT / "data" / "tap_categories.json"
TAP_RUN_PATH = REPO_ROOT / "data" / "tap_run.json"
TAP_FILES_PATH = REPO_ROOT / "data" / "tap_files.json"
# Separate from the official homepage cache so the tap pipeline never edits official data.
TAP_HOMEPAGE_CACHE = REPO_ROOT / "data" / "tap_homepage_metadata.json"
VERSION = 1


def load_existing(path: Path) -> dict:
    if not path.exists():
        return {"version": VERSION, "generatedDate": "", "casks": {}, "sameAs": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def pending_refs(collected: dict, existing: dict) -> set[str]:
    return {
        ref for ref in collected["casks"]
        if ref not in collected.get("sameAs", {}) and ref not in existing.get("casks", {})
    }


def merge(existing: dict, classifications: dict[str, Classification], same_as: dict[str, str]) -> dict:
    casks = dict(existing.get("casks", {}))
    for ref, result in classifications.items():
        casks[ref] = {"primary": result.primary, "secondary": result.secondary}
    aliases = {**existing.get("sameAs", {}), **same_as}
    return {
        "version": VERSION,
        "generatedDate": str(date.today()),
        "casks": dict(sorted(casks.items())),
        "sameAs": dict(sorted(aliases.items())),
    }


def _gone(ref: str, listed: dict[str, object], removed_taps: list[str]) -> bool:
    """The cask's tap left taps.json, or its file left the tap's repository listing."""
    tap = tap_of(ref)
    return tap in removed_taps or (tap in listed and ref not in listed[tap])


def _listed(view: dict) -> dict[str, object]:
    # An empty listing prunes nothing: more likely a restructured repo than a tap with no casks.
    return {tap: refs for tap, refs in view.get("listed", {}).items() if refs}


def prune(existing: dict, view: dict, removed_taps: list[str]) -> tuple[dict, list[str]]:
    """Drop what is gone: casks that left their tap, stale aliases, and taps removed from taps.json."""
    # Whether a cask is gone comes only from `listed` (a git file listing made without running tap code),
    # never from collected data, which tap code could have shaped.
    casks = dict(existing.get("casks", {}))
    aliases = dict(existing.get("sameAs", {}))
    listed, present, now_aliased = _listed(view), set(view["casks"]), view.get("sameAs", {})
    removed: set[str] = set()
    for ref in set(casks) | set(aliases):
        if _gone(ref, listed, removed_taps):
            casks.pop(ref, None)
            aliases.pop(ref, None)
            removed.add(ref)
        elif ref in present and ref not in now_aliased:
            aliases.pop(ref, None)
        elif ref in present and casks.pop(ref, None) is not None:
            removed.add(ref)
    aliases.update(now_aliased)
    return {**existing, "casks": casks, "sameAs": aliases}, sorted(removed)


def tap_files(existing: dict[str, str], merged: dict, view: dict, removed_taps: list[str]) -> dict[str, str]:
    """Cask file ids already settled (categorized or aliased); the weekly sweep skips taps whose files all match."""
    listed = _listed(view)
    settled = set(merged.get("casks", {})) | set(merged.get("sameAs", {}))
    files = {ref: oid for ref, oid in existing.items() if not _gone(ref, listed, removed_taps)}
    files.update({ref: oid for ref, oid in view.get("processed", {}).items() if ref in settled})
    return dict(sorted(files.items()))


def run_file(view: dict, removed_taps: list[str]) -> dict:
    """What publish needs from this run: taps that yielded casks, and per listed tap the refs that keep icons."""
    return {
        "readable": sorted({tap_of(ref) for ref in view["casks"]}),
        "current": {tap: sorted(refs) for tap, refs in sorted(view.get("listed", {}).items())},
        "prunedTaps": sorted(removed_taps),
    }


def fetch_tap_homepages(refs: set[str], casks: dict[str, dict], cache: dict[str, dict]) -> dict[str, dict]:
    """Fetch homepages tap authors control: public hosts only, redirects included."""
    opener = public_opener()
    work = [(ref, casks[ref].get("homepage", "")) for ref in sorted(refs) if ref not in cache or "error" in cache[ref]]

    def one(item: tuple[str, str]) -> dict:
        ref, url = item
        if not is_public_url(url):
            return {"token": ref, "homepage": url, "error": "non_public_url"}
        return fetch_one(ref, url, open_url=opener.open)

    with ThreadPoolExecutor(max_workers=HOMEPAGE_WORKERS) as pool:
        for result in pool.map(one, work):
            cache[result["token"]] = result
    return {ref: cache[ref] for ref in refs if ref in cache}


def make_client() -> LLMClient:
    return LLMClient.from_env(CategoryCatalog.load(CATEGORIES_PATH))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected", type=Path, required=True, help="tap_casks.json from collect_tap_casks.py")
    parser.add_argument("--taps", nargs="*", required=True, help="Taps this run was asked to process")
    parser.add_argument("--prune", nargs="*", default=[], help="Taps removed from taps.json")
    args = parser.parse_args(argv)
    if invalid := [error for name in args.taps + args.prune if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))

    # The collect job ran tap code, so its file is only trusted through this view.
    collected = trusted_view(json.loads(args.collected.read_text(encoding="utf-8")), args.taps, load_official())
    existing, removed = prune(load_existing(TAP_CATEGORIES_PATH), collected, args.prune)
    print(f"removed: {len(removed)}")
    refs = pending_refs(collected, existing)
    print(f"tap casks: {len(collected['casks'])}, aliased: {len(collected.get('sameAs', {}))}, to classify: {len(refs)}")

    classifications = {}
    if refs:
        cache = json.loads(TAP_HOMEPAGE_CACHE.read_text(encoding="utf-8")) if TAP_HOMEPAGE_CACHE.exists() else {}
        homepages = fetch_tap_homepages(refs, collected["casks"], cache)
        write_json(TAP_HOMEPAGE_CACHE, dict(sorted(cache.items())), trailing_newline=True)
        workers = LLM_WORKERS_BY_PROVIDER.get(os.environ.get("LLM_PROVIDER", "anthropic").lower(), 5)
        classifications, failures = classify_parallel(make_client(), refs, collected["casks"], homepages, workers)
        for ref, reason in sorted(failures):
            print(f"skipped {ref}: {reason}")

    merged = merge(existing, classifications, collected.get("sameAs", {}))
    write_json(TAP_CATEGORIES_PATH, merged, trailing_newline=True)
    files = json.loads(TAP_FILES_PATH.read_text(encoding="utf-8")) if TAP_FILES_PATH.exists() else {}
    write_json(TAP_FILES_PATH, tap_files(files, merged, collected, args.prune), trailing_newline=True)
    write_json(TAP_RUN_PATH, run_file(collected, args.prune), trailing_newline=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
