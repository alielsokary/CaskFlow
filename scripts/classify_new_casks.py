#!/usr/bin/env python3
"""Daily classification orchestrator."""
# Computes the diff between the live Homebrew cask API and the existing
# categories.json, classifies new casks via an LLM, prunes deprecated/removed
# casks, and writes the updated categories.json plus a human-readable report.
#
# Idempotent — if there are no diffs, exits 0 with no file changes.
# Failure-isolated — per-cask LLM errors are logged and skipped, never fatal.
#
# Run:
#     LLM_PROVIDER=mock python scripts/classify_new_casks.py
#     LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... python scripts/classify_new_casks.py
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.request import urlopen

# Allow importing sibling modules when run as `python scripts/classify_new_casks.py`
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from fetch_homepages import fetch_one  # noqa: E402  (path setup above)
from llm_client import Classification, ClassificationError, LLMClient  # noqa: E402
from prompts import CategoryCatalog  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent
CATEGORIES_PATH = REPO_ROOT / "categories.json"
HOMEPAGE_CACHE = REPO_ROOT / "data" / "homepage_metadata.json"
REPORT_PATH = REPO_ROOT / "data" / "classification_report.md"

BREW_API = "https://formulae.brew.sh/api/cask.json"
HOMEPAGE_WORKERS = 30
LLM_WORKERS_BY_PROVIDER = {
    "anthropic": 5,
    "openai": 8,
    "groq": 10,
    "cloudflare": 10,
    "mock": 16,
}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def fetch_brew_api() -> list[dict]:
    print(f"Fetching {BREW_API} …")
    with urlopen(BREW_API, timeout=30) as resp:
        return json.loads(resp.read())


def load_existing_categories() -> dict:
    return json.loads(CATEGORIES_PATH.read_text())


def load_homepage_cache() -> dict[str, dict]:
    if not HOMEPAGE_CACHE.exists():
        return {}
    return {entry["token"]: entry for entry in json.loads(HOMEPAGE_CACHE.read_text())}


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def is_main_cask(c: dict) -> bool:
    """Mirror the filter in CaskCatalogViewModel.swift:137-142."""
    # The app only ever shows main casks — not deprecated/disabled, not fonts,
    # not version-pinned variants (anything containing `@`). Classifying anything
    # outside that set would inflate categories.json with rows the app filters
    # out anyway.
    token = c["token"]
    return not (
        c.get("deprecated")
        or c.get("disabled")
        or "@" in token
        or token.startswith("font-")
    )


def migrate_renames(existing: dict, api_casks: list[dict]) -> list[tuple[str, str]]:
    """Carry the classification across a Homebrew token rename instead of re-classifying."""
    # Homebrew renames keep the old token in the new cask's `old_tokens` field;
    # migrating avoids pruning + paying an LLM call to re-classify the same app.
    #
    # Mutates existing["tokenToCategory"] in place. Returns [(old, new), ...].
    # Renames onto an already-classified token just drop the old entry; renames
    # onto a non-main cask (deprecated etc.) are migrated here and then pruned by
    # compute_diff's no_longer_main pass.
    mapping = existing["tokenToCategory"]
    api_tokens = {c["token"] for c in api_casks}
    old_to_new = {
        old: c["token"] for c in api_casks for old in (c.get("old_tokens") or [])
    }

    renames: list[tuple[str, str]] = []
    for old in sorted(set(mapping) - api_tokens):
        new = old_to_new.get(old)
        if new is None:
            continue  # true removal — compute_diff prunes it
        if new not in mapping:
            mapping[new] = mapping[old]
        del mapping[old]
        renames.append((old, new))
    return renames


def compute_diff(api_casks: list[dict], existing: dict) -> tuple[set[str], set[str], set[str]]:
    main_tokens = {c["token"] for c in api_casks if is_main_cask(c)}
    existing_tokens = set(existing["tokenToCategory"])

    # Tokens that should be pruned even if still present in the API:
    # explicitly deprecated/disabled, OR tokens we already classified that
    # the app would now filter out (e.g. someone deprecated them upstream).
    api_by_token = {c["token"]: c for c in api_casks}
    no_longer_main = {
        t for t in existing_tokens
        if t in api_by_token and not is_main_cask(api_by_token[t])
    }

    new_tokens = main_tokens - existing_tokens
    removed_tokens = existing_tokens - {c["token"] for c in api_casks}
    prune_tokens = removed_tokens | no_longer_main
    return new_tokens, removed_tokens, prune_tokens


# ---------------------------------------------------------------------------
# Homepage scrape (delta only, persisted to cache)
# ---------------------------------------------------------------------------

def _cached_subset(tokens: set[str], cache: dict[str, dict]) -> dict[str, dict]:
    return {t: cache[t] for t in tokens if t in cache}


def _persist_cache(cache: dict[str, dict]) -> None:
    HOMEPAGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    HOMEPAGE_CACHE.write_text(
        json.dumps(sorted(cache.values(), key=lambda x: x["token"]), indent=2, ensure_ascii=False)
    )


def fetch_homepages_for(
    tokens: set[str], api_casks_by_token: dict[str, dict], cache: dict[str, dict]
) -> dict[str, dict]:
    """Fetch homepages for `tokens` not in cache. Updates cache file in place."""
    work = [
        (t, api_casks_by_token[t].get("homepage", ""))
        for t in tokens
        if t not in cache or "error" in cache[t]
    ]
    if not work:
        return _cached_subset(tokens, cache)

    print(f"Fetching {len(work)} homepages with {HOMEPAGE_WORKERS} workers …")
    with ThreadPoolExecutor(max_workers=HOMEPAGE_WORKERS) as pool:
        futures = {pool.submit(fetch_one, t, u): t for t, u in work}
        for fut in as_completed(futures):
            r = fut.result()
            cache[r["token"]] = r

    _persist_cache(cache)
    return _cached_subset(tokens, cache)


# ---------------------------------------------------------------------------
# LLM classification (parallel, failure-isolated)
# ---------------------------------------------------------------------------

def classify_parallel(
    client: LLMClient,
    tokens: set[str],
    api_casks_by_token: dict[str, dict],
    homepage_meta: dict[str, dict],
    workers: int,
) -> tuple[dict[str, Classification], list[tuple[str, str]]]:
    classifications: dict[str, Classification] = {}
    failures: list[tuple[str, str]] = []
    if not tokens:
        return classifications, failures

    print(f"Classifying {len(tokens)} casks with {workers} workers …")
    start = time.time()

    def _one(token: str) -> tuple[str, Classification | str]:
        cask = api_casks_by_token[token]
        meta = homepage_meta.get(token)
        try:
            return token, client.classify(cask, meta)
        except ClassificationError as e:
            return token, f"VALIDATE: {e}"
        except Exception as e:  # network, provider 5xx, etc.
            return token, f"PROVIDER: {type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, t) for t in sorted(tokens)]
        done = 0
        for fut in as_completed(futures):
            token, result = fut.result()
            done += 1
            if isinstance(result, Classification):
                classifications[token] = result
            else:
                failures.append((token, result))
            if done % 25 == 0 or done == len(tokens):
                elapsed = time.time() - start
                rate = done / elapsed if elapsed else 0
                print(f"  [{done}/{len(tokens)}] {rate:.1f}/sec, {len(failures)} skipped")

    return classifications, failures


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def build_updated_categories(
    existing: dict, prune_tokens: set[str], classifications: dict[str, Classification]
) -> dict:
    new_mapping = {
        t: v for t, v in existing["tokenToCategory"].items() if t not in prune_tokens
    }
    for token, c in classifications.items():
        new_mapping[token] = {"primary": c.primary, "secondary": c.secondary}

    return {
        **existing,
        "generatedDate": str(date.today()),
        "totalCasks": len(new_mapping),
        "tokenToCategory": dict(sorted(new_mapping.items())),
    }


def write_categories(updated: dict) -> None:
    CATEGORIES_PATH.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")


def _section(title: str, rows: list[str]) -> list[str]:
    """A `## title` block with blank lines around its content; [] when empty."""
    if not rows:
        return []
    return [f"## {title}", "", *rows, ""]


def _classification_rows(classifications: dict[str, Classification]) -> list[str]:
    rows = ["| token | primary | secondary | confidence | reason |", "|---|---|---|---|---|"]
    for token in sorted(classifications):
        c = classifications[token]
        sec = ", ".join(c.secondary) or "—"
        rows.append(f"| `{token}` | {c.primary} | {sec} | {c.confidence:.2f} | {c.reason} |")
    return rows


def write_report(
    new_tokens: set[str],
    removed_tokens: set[str],
    prune_tokens: set[str],
    classifications: dict[str, Classification],
    failures: list[tuple[str, str]],
    renames: list[tuple[str, str]],
) -> None:
    deprecated_only = prune_tokens - removed_tokens
    lines: list[str] = [
        "# Daily classification update",
        "",
        f"Generated {date.today().isoformat()}",
        "",
        "## Summary",
        "",
        f"- {len(classifications)} new casks classified",
        f"- {len(failures)} new casks **skipped** (LLM/validation failures, will retry tomorrow)",
        f"- {len(renames)} casks renamed in Homebrew (classification migrated, no LLM call)",
        f"- {len(removed_tokens)} casks removed from Homebrew (pruned)",
        f"- {len(deprecated_only)} casks deprecated/disabled in Homebrew (pruned)",
        "",
    ]

    lines += _section("Renamed (classification migrated)",
                      [f"- `{old}` → `{new}`" for old, new in sorted(renames)])
    lines += _section("New classifications",
                      _classification_rows(classifications) if classifications else [])
    lines += _section("Skipped (will retry next run)",
                      [f"- `{token}` — {err}" for token, err in sorted(failures)])
    lines += _section("Removed from Homebrew (pruned)", [f"- `{t}`" for t in sorted(removed_tokens)])
    lines += _section("Deprecated/disabled (pruned)", [f"- `{t}`" for t in sorted(deprecated_only)])

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report → {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute diffs and run LLM classification, but do not write categories.json.",
    )
    args = parser.parse_args(argv)

    existing = load_existing_categories()
    catalog = CategoryCatalog.load(CATEGORIES_PATH)
    api_casks = fetch_brew_api()
    api_casks_by_token = {c["token"]: c for c in api_casks}

    renames = migrate_renames(existing, api_casks)
    new_tokens, removed_tokens, prune_tokens = compute_diff(api_casks, existing)
    print(
        f"diff: +{len(new_tokens)} new, ~{len(renames)} renamed, "
        f"-{len(removed_tokens)} removed, "
        f"-{len(prune_tokens - removed_tokens)} deprecated"
    )

    if not new_tokens and not prune_tokens and not renames:
        print("No changes. Exiting clean.")
        return 0

    cache = load_homepage_cache()
    homepage_meta = (
        fetch_homepages_for(new_tokens, api_casks_by_token, cache) if new_tokens else {}
    )

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    workers = LLM_WORKERS_BY_PROVIDER.get(provider, 5)
    client = LLMClient.from_env(catalog)

    classifications, failures = classify_parallel(
        client, new_tokens, api_casks_by_token, homepage_meta, workers
    )

    updated = build_updated_categories(existing, prune_tokens, classifications)
    write_report(new_tokens, removed_tokens, prune_tokens, classifications, failures, renames)

    if args.dry_run:
        print(
            f"DRY RUN — would write {CATEGORIES_PATH.name}: {updated['totalCasks']} casks "
            f"(+{len(classifications)}, -{len(prune_tokens)}, skipped {len(failures)})"
        )
        return 0

    write_categories(updated)
    print(
        f"Wrote {CATEGORIES_PATH.name}: {updated['totalCasks']} casks "
        f"(+{len(classifications)}, -{len(prune_tokens)}, skipped {len(failures)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
