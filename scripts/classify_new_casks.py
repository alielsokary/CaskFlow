#!/usr/bin/env python3
"""Classify new Homebrew casks and migrate classifications across token renames."""
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
import style_standards  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent
CATEGORIES_PATH = REPO_ROOT / "categories.json"
HOMEPAGE_CACHE = REPO_ROOT / "data" / "homepage_metadata.json"
REPORT_PATH = REPO_ROOT / "data" / "classification_report.md"
MANUAL_REVIEW_CONFIDENCE = 0.75

BREW_API = "https://formulae.brew.sh/api/cask.json"
HOMEPAGE_WORKERS = 30
LLM_WORKERS_BY_PROVIDER = {
    "anthropic": 5,
    "openai": 8,
    "groq": 10,
    "cloudflare": 10,
    "mock": 16,
}


def fetch_brew_api() -> list[dict]:
    print(f"Fetching {BREW_API} ...")
    with urlopen(BREW_API, timeout=30) as resp:
        return json.loads(resp.read())


def load_existing_categories() -> dict:
    return json.loads(CATEGORIES_PATH.read_text(encoding="utf-8"))


def load_homepage_cache() -> dict[str, dict]:
    if not HOMEPAGE_CACHE.exists():
        return {}
    return {entry["token"]: entry for entry in json.loads(HOMEPAGE_CACHE.read_text(encoding="utf-8"))}


def is_main_cask(c: dict) -> bool:
    """Casks the app lists, searches, or shows as installed."""
    # Deprecated and disabled casks stay in: the app finds them in search and
    # shows installed copies. Fonts and `@` variants are not classified yet.
    token = c["token"]
    return not ("@" in token or token.startswith("font-"))


def migrate_renames(existing: dict, api_casks: list[dict]) -> list[tuple[str, str]]:
    """Carry the classification across a Homebrew token rename instead of re-classifying."""
    # Homebrew exposes the prior token in old_tokens. The target's existing
    # classification wins when both tokens are already mapped.
    mapping = existing["tokenToCategory"]
    api_tokens = {c["token"] for c in api_casks}
    old_to_new = {
        old: c["token"] for c in api_casks for old in (c.get("old_tokens") or [])
    }

    renames: list[tuple[str, str]] = []
    for old in sorted(set(mapping) - api_tokens):
        new = old_to_new.get(old)
        if new is None:
            continue  # true removal: kept so installed copies keep their category
        if new not in mapping:
            mapping[new] = mapping[old]
        del mapping[old]
        renames.append((old, new))
    return renames


def compute_diff(api_casks: list[dict], existing: dict) -> set[str]:
    main_tokens = {c["token"] for c in api_casks if is_main_cask(c)}
    return main_tokens - set(existing["tokenToCategory"])


def _cached_subset(tokens: set[str], cache: dict[str, dict]) -> dict[str, dict]:
    return {t: cache[t] for t in tokens if t in cache}


def _persist_cache(cache: dict[str, dict]) -> None:
    HOMEPAGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    style_standards.write_json(
        HOMEPAGE_CACHE, sorted(cache.values(), key=lambda x: x["token"])
    )


def fetch_homepages_for(
    tokens: set[str],
    api_casks_by_token: dict[str, dict],
    cache: dict[str, dict],
    *,
    persist: bool = True,
) -> dict[str, dict]:
    """Fetch uncached homepages and optionally persist the updated cache."""
    work = [
        (t, api_casks_by_token[t].get("homepage", ""))
        for t in tokens
        if t not in cache or "error" in cache[t]
    ]
    if not work:
        return _cached_subset(tokens, cache)

    print(f"Fetching {len(work)} homepages with {HOMEPAGE_WORKERS} workers ...")
    with ThreadPoolExecutor(max_workers=HOMEPAGE_WORKERS) as pool:
        futures = {pool.submit(fetch_one, t, u): t for t, u in work}
        for fut in as_completed(futures):
            r = fut.result()
            cache[r["token"]] = r

    if persist:
        _persist_cache(cache)
    return _cached_subset(tokens, cache)


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

    print(f"Classifying {len(tokens)} casks with {workers} workers ...")
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


def build_updated_categories(
    existing: dict, classifications: dict[str, Classification]
) -> dict:
    new_mapping = dict(existing["tokenToCategory"])
    for token, c in classifications.items():
        new_mapping[token] = {"primary": c.primary, "secondary": c.secondary}

    return {
        **existing,
        "generatedDate": str(date.today()),
        "totalCasks": len(new_mapping),
        "tokenToCategory": dict(sorted(new_mapping.items())),
    }


def write_categories(updated: dict) -> None:
    style_standards.write_json(CATEGORIES_PATH, updated, trailing_newline=True)


def _section(title: str, rows: list[str]) -> list[str]:
    """A `## title` block with blank lines around its content; [] when empty."""
    if not rows:
        return []
    return [f"## {title}", "", *rows, ""]


def _classification_rows(classifications: dict[str, Classification]) -> list[str]:
    rows = ["| token | primary | secondary | confidence | reason |", "|---|---|---|---|---|"]
    for token in sorted(classifications):
        c = classifications[token]
        sec = ", ".join(c.secondary) or "-"
        reason = " ".join(c.reason.splitlines()).replace("|", "\\|")
        rows.append(f"| `{token}` | {c.primary} | {sec} | {c.confidence:.2f} | {reason} |")
    return rows


def build_report(
    classifications: dict[str, Classification],
    failures: list[tuple[str, str]],
    renames: list[tuple[str, str]],
) -> str:
    review_required = {
        token: result
        for token, result in classifications.items()
        if result.confidence < MANUAL_REVIEW_CONFIDENCE
    }
    lines: list[str] = [
        "# Daily classification update",
        "",
        f"Generated {date.today().isoformat()}",
        "",
        "## Summary",
        "",
        f"- {len(classifications)} new casks classified",
        f"- {len(review_required)} classifications require manual review "
        f"(confidence below {MANUAL_REVIEW_CONFIDENCE:.2f})",
        f"- {len(failures)} new casks **skipped** (LLM/validation failures, will retry tomorrow)",
        f"- {len(renames)} casks renamed in Homebrew (classification migrated, no LLM call)",
        "",
    ]

    lines += _section("Renamed (classification migrated)",
                      [f"- `{old}` → `{new}`" for old, new in sorted(renames)])
    lines += _section("New classifications",
                      _classification_rows(classifications) if classifications else [])
    lines += _section(
        "Manual review required",
        _classification_rows(review_required) if review_required else [],
    )
    lines += _section("Skipped (will retry next run)",
                      [f"- `{token}` - {err}" for token, err in sorted(failures)])

    return "\n".join(lines)


def write_report(report: str) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    style_standards.write_text(REPORT_PATH, report)
    print(f"Report → {REPORT_PATH}")


def requires_manual_review(classifications: dict[str, Classification]) -> bool:
    return any(c.confidence < MANUAL_REVIEW_CONFIDENCE for c in classifications.values())


def write_github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the update without changing repository files.",
    )
    args = parser.parse_args(argv)

    existing = load_existing_categories()
    catalog = CategoryCatalog.load(CATEGORIES_PATH)
    api_casks = fetch_brew_api()
    api_casks_by_token = {c["token"]: c for c in api_casks}

    renames = migrate_renames(existing, api_casks)
    new_tokens = compute_diff(api_casks, existing)
    print(f"diff: +{len(new_tokens)} new, ~{len(renames)} renamed")

    if not new_tokens and not renames:
        print("No changes. Exiting clean.")
        return 0

    cache = load_homepage_cache()
    homepage_meta = (
        fetch_homepages_for(
            new_tokens,
            api_casks_by_token,
            cache,
            persist=not args.dry_run,
        ) if new_tokens else {}
    )

    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    workers = LLM_WORKERS_BY_PROVIDER.get(provider, 5)
    client = LLMClient.from_env(catalog)

    classifications, failures = classify_parallel(
        client, new_tokens, api_casks_by_token, homepage_meta, workers
    )

    updated = build_updated_categories(existing, classifications)
    report = build_report(classifications, failures, renames)
    review_required = requires_manual_review(classifications)
    write_github_output("review_required", str(review_required).lower())

    if args.dry_run:
        print(report)
        print(
            f"DRY RUN - would write {CATEGORIES_PATH.name}: {updated['totalCasks']} casks "
            f"(+{len(classifications)}, skipped {len(failures)})"
        )
        return 0

    write_categories(updated)
    write_report(report)
    print(
        f"Wrote {CATEGORIES_PATH.name}: {updated['totalCasks']} casks "
        f"(+{len(classifications)}, skipped {len(failures)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
