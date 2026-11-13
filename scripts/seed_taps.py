#!/usr/bin/env python3
"""Seed taps.json with the most-installed third-party taps from Homebrew's cask analytics."""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Callable

import tap_registry
from style_standards import write_json

ANALYTICS_URL = "https://formulae.brew.sh/api/analytics/cask-install/365d.json"
GITHUB = "https://github.com/"


def select(analytics: dict, threshold: int = 100) -> list[str]:
    """Third-party taps whose casks sum to `threshold` installs or more, most installed first."""
    installs: Counter[str] = Counter()
    for item in analytics.get("items", []):
        parts = item["cask"].lower().split("/")
        if len(parts) == 3 and parts[0] != "homebrew":
            installs[f"{parts[0]}/{parts[1]}"] += int(str(item["count"]).replace(",", ""))
    ranked = sorted(installs.items(), key=lambda pair: (-pair[1], pair[0]))
    return [tap for tap, count in ranked if count >= threshold]


def resolve(name: str, head: Callable[[str], str | None]) -> dict | None:
    """The registry entry for where the tap's repository lives now; None if gone or official."""
    final = head(tap_registry.default_remote(name))
    if final is None:
        return None
    owner, repo = final.removeprefix(GITHUB).split("/")[:2]
    if repo.lower().startswith("homebrew-"):
        entry = {"name": f"{owner}/{repo[len('homebrew-'):]}".lower()}
    else:
        entry = {"name": f"{owner}/{repo}".lower(), "remote": f"{GITHUB}{owner}/{repo}"}
    return None if tap_registry.validate_name(entry["name"]) else entry


def seed(names: list[str], head: Callable[[str], str | None], limit: int) -> list[dict]:
    """Resolve taps in rank order until `limit` distinct entries; a tap that moved onto one already kept is dropped."""
    entries: dict[str, dict] = {}
    for name in names:
        if len(entries) >= limit:
            break
        entry = resolve(name, head)
        if entry and entry["name"] not in entries:
            entries[entry["name"]] = entry
    return list(entries.values())


def github_head(url: str) -> str | None:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "caskflow-seed"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.geturl()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analytics", type=Path, help=f"Saved analytics JSON (default: fetch {ANALYTICS_URL})")
    parser.add_argument("--out", type=Path, default=tap_registry.REGISTRY_PATH)
    parser.add_argument("--threshold", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--batch", type=int, required=True, help="Write the first N batches")
    args = parser.parse_args(argv)

    if args.analytics:
        analytics = json.loads(args.analytics.read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(ANALYTICS_URL, timeout=60) as response:
            analytics = json.load(response)
    new = seed(select(analytics, args.threshold), github_head, args.batch * args.batch_size)
    merged = {entry["name"]: entry for entry in [*new, *tap_registry.load(args.out)]}
    taps = sorted(merged.values(), key=lambda entry: entry["name"])
    write_json(args.out, {"version": tap_registry.VERSION, "taps": taps}, trailing_newline=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
