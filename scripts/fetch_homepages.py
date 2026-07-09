#!/usr/bin/env python3
"""Fetch homepage metadata (title, meta description, og:description) for casks."""
# Two ways to use it:
# 1. As a library — import fetch_one(token, url) and call per cask. The daily
#    pipeline (classify_new_casks.py) does this against the delta set.
# 2. As a CLI — `python scripts/fetch_homepages.py` re-scrapes everything in
#    data/filtered_casks.json. Used for full audits, not the daily pipeline.
import contextlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
import ssl

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
CATEGORIES_PATH = os.path.join(REPO_ROOT, "categories.json")
CASKS_PATH = os.path.join(REPO_ROOT, "data", "filtered_casks.json")
OUTPUT_PATH = os.path.join(REPO_ROOT, "data", "homepage_metadata.json")

# Resume support: skip already-fetched tokens
WORKERS = 30
TIMEOUT = 10
MAX_BODY = 200_000  # 200KB max per page


class MetaExtractor(HTMLParser):
    """Extract title, meta description, and og:description from HTML."""

    def __init__(self):
        """Initialize empty parser state."""
        super().__init__()
        self.title = ""
        self.meta_desc = ""
        self.og_desc = ""
        self._in_title = False
        self._title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = {k.lower(): v for k, v in attrs if k}
        if tag == "title":
            self._in_title = True
            self._title_parts = []
        elif tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            content = attrs_dict.get("content", "")
            if name == "description" and content:
                self.meta_desc = content[:500]
            elif prop == "og:description" and content:
                self.og_desc = content[:500]

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = "".join(self._title_parts).strip()[:300]


def fetch_one(token, url):
    """Fetch a single homepage and extract metadata."""
    if not url or not url.startswith("http"):
        return {"token": token, "homepage": url, "error": "invalid_url"}

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"refusing non-HTTP homepage URL: {url}")
        req = Request(url, headers=headers)
        with urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            body = resp.read(MAX_BODY).decode("utf-8", errors="replace")

        extractor = MetaExtractor()
        with contextlib.suppress(Exception):  # scraped HTML can be arbitrarily broken
            extractor.feed(body)

        return {
            "token": token,
            "homepage": url,
            "title": extractor.title,
            "meta_desc": extractor.meta_desc,
            "og_desc": extractor.og_desc,
        }
    except HTTPError as e:
        return {"token": token, "homepage": url, "error": f"HTTP {e.code}"}
    except URLError as e:
        return {"token": token, "homepage": url, "error": str(e.reason)[:200]}
    except Exception as e:
        return {"token": token, "homepage": url, "error": str(e)[:200]}


def _load_existing() -> dict:
    """Resume support: previously fetched results, keyed by token."""
    existing = {}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH) as f:
            for item in json.load(f):
                existing[item["token"]] = item
        print(f"Found {len(existing)} existing results (will skip)")
    return existing


def _build_work(casks: list[dict], existing: dict) -> list[tuple[str, str]]:
    """(token, homepage) pairs still needing a fetch — failures are retried."""
    work = []
    for c in casks:
        token = c["token"]
        if token in existing and "error" not in existing[token]:
            continue  # Skip already-fetched successes
        work.append((token, c.get("homepage", "")))
    return work


def _report_progress(done: int, total: int, errors: int, start_time: float, results: dict) -> None:
    """Print a rate line every 100 casks and checkpoint results every 500."""
    if done % 100 != 0 and done != total:
        return
    elapsed = time.time() - start_time
    rate = done / elapsed if elapsed > 0 else 0
    print(f"  [{done}/{total}] {rate:.0f}/sec, {errors} errors")
    if done % 500 == 0:
        _save(results)


def main():
    with open(CASKS_PATH) as f:
        casks = json.load(f)
    print(f"Loaded {len(casks)} casks from {CASKS_PATH}")

    existing = _load_existing()
    work = _build_work(casks, existing)
    print(f"Fetching {len(work)} homepages with {WORKERS} workers...")
    results = dict(existing)  # Start with existing
    done = 0
    errors = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_one, t, u): t for t, u in work}
        for future in as_completed(futures):
            result = future.result()
            results[result["token"]] = result
            done += 1
            if "error" in result:
                errors += 1
            _report_progress(done, len(work), errors, start_time, results)

    _save(results)
    elapsed = time.time() - start_time
    success = sum(1 for r in results.values() if "error" not in r)
    print(f"\nDone in {elapsed:.0f}s: {success} success, {len(results) - success} errors")
    print(f"Saved to {OUTPUT_PATH}")


def _save(results):
    out = sorted(results.values(), key=lambda x: x["token"])
    with open(OUTPUT_PATH, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
