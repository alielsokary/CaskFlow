#!/usr/bin/env python3
"""Classify tap casks from the collect step's output with the official classifier, keyed by full name."""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from classify_new_casks import CATEGORIES_PATH, LLM_WORKERS_BY_PROVIDER, REPO_ROOT, classify_parallel, fetch_homepages_for
from llm_client import Classification, LLMClient
from prompts import CategoryCatalog
from style_standards import write_json

TAP_CATEGORIES_PATH = REPO_ROOT / "data" / "tap_categories.json"
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


def make_client() -> LLMClient:
    return LLMClient.from_env(CategoryCatalog.load(CATEGORIES_PATH))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected", type=Path, required=True, help="tap_casks.json from collect_tap_casks.py")
    args = parser.parse_args(argv)

    collected = json.loads(args.collected.read_text(encoding="utf-8"))
    existing = load_existing(TAP_CATEGORIES_PATH)
    refs = pending_refs(collected, existing)
    print(f"tap casks: {len(collected['casks'])}, aliased: {len(collected.get('sameAs', {}))}, to classify: {len(refs)}")

    classifications = {}
    if refs:
        cache = json.loads(TAP_HOMEPAGE_CACHE.read_text(encoding="utf-8")) if TAP_HOMEPAGE_CACHE.exists() else {}
        homepages = fetch_homepages_for(refs, collected["casks"], cache, persist=False)
        write_json(TAP_HOMEPAGE_CACHE, dict(sorted(cache.items())), trailing_newline=True)
        workers = LLM_WORKERS_BY_PROVIDER.get(os.environ.get("LLM_PROVIDER", "anthropic").lower(), 5)
        classifications, failures = classify_parallel(make_client(), refs, collected["casks"], homepages, workers)
        for ref, reason in sorted(failures):
            print(f"skipped {ref}: {reason}")

    write_json(TAP_CATEGORIES_PATH, merge(existing, classifications, collected.get("sameAs", {})), trailing_newline=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
