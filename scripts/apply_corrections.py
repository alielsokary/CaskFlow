#!/usr/bin/env python3
"""Apply reviewed category corrections to categories.json."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATEGORIES = REPO_ROOT / "categories.json"
DEFAULT_CORRECTIONS = REPO_ROOT / "data" / "category_corrections.json"
TRAIT_CATEGORIES = {"ai"}
MAX_SECONDARY = 2


def _mapping_for(token: str, mapping: dict[str, dict]) -> dict:
    if token not in mapping:
        raise ValueError(f"{token}: not found in categories.json")
    value = mapping[token]
    if not isinstance(value, dict):
        raise ValueError(f"{token}: expected a version 2 category mapping")
    value.setdefault("secondary", [])
    return value


def apply_correction(
    correction: dict,
    mapping: dict[str, dict],
    category_ids: set[str],
) -> bool:
    token = correction["token"]
    value = _mapping_for(token, mapping)

    if correction.get("action") == "addSecondary":
        additions = correction.get("secondary")
        if not isinstance(additions, list) or not additions:
            raise ValueError(f"{token}: addSecondary requires a non-empty secondary list")
        unknown = set(additions) - category_ids
        if unknown:
            raise ValueError(f"{token}: unknown secondary categories: {sorted(unknown)}")
        secondary = list(dict.fromkeys([*value["secondary"], *additions]))
        secondary = [category for category in secondary if category != value["primary"]]
        if len(secondary) > MAX_SECONDARY:
            raise ValueError(f"{token}: secondary categories exceed the {MAX_SECONDARY}-item limit")
        if secondary == value["secondary"]:
            return False
        value["secondary"] = secondary
        return True

    new_primary = correction.get("shouldBe")
    if new_primary not in category_ids:
        raise ValueError(f"{token}: unknown primary category: {new_primary!r}")
    if new_primary in TRAIT_CATEGORIES:
        raise ValueError(f"{token}: {new_primary} is a secondary-only trait")

    current_primary = value["primary"]
    if current_primary == new_primary:
        cleaned = list(dict.fromkeys(
            category for category in value["secondary"] if category != new_primary
        ))
        changed = cleaned != value["secondary"]
        value["secondary"] = cleaned
        return changed

    expected = correction.get("was")
    if expected and current_primary != expected:
        raise ValueError(
            f"{token}: expected current primary {expected!r}, found {current_primary!r}"
        )
    value["primary"] = new_primary
    value["secondary"] = list(dict.fromkeys(
        category for category in value["secondary"] if category != new_primary
    ))
    return True


def validate_categories(data: dict) -> None:
    category_ids = set(data["categories"])
    for token, value in data["tokenToCategory"].items():
        primary = value["primary"]
        secondary = value.get("secondary", [])
        if primary not in category_ids or primary in TRAIT_CATEGORIES:
            raise ValueError(f"{token}: invalid primary category {primary!r}")
        if len(secondary) > MAX_SECONDARY or len(secondary) != len(set(secondary)):
            raise ValueError(f"{token}: invalid secondary categories {secondary!r}")
        if primary in secondary or set(secondary) - category_ids:
            raise ValueError(f"{token}: invalid secondary categories {secondary!r}")


def apply_corrections(data: dict, corrections: list[dict], *, apply_all: bool) -> list[str]:
    selected = corrections if apply_all else [
        correction for correction in corrections if correction.get("confidence") == "high"
    ]
    category_ids = set(data["categories"])
    changed = [
        correction["token"]
        for correction in selected
        if apply_correction(correction, data["tokenToCategory"], category_ids)
    ]
    validate_categories(data)
    data["totalCasks"] = len(data["tokenToCategory"])
    if changed:
        data["generatedDate"] = date.today().isoformat()
    return changed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Include non-high-confidence corrections")
    parser.add_argument("--dry-run", action="store_true", help="Validate and preview without writing")
    parser.add_argument("--categories", type=Path, default=DEFAULT_CATEGORIES)
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data = json.loads(args.categories.read_text(encoding="utf-8"))
    corrections = json.loads(args.corrections.read_text(encoding="utf-8"))
    changed = apply_corrections(data, corrections, apply_all=args.all)

    label = "Would update" if args.dry_run else "Updated"
    print(f"{label} {len(changed)} casks: {', '.join(changed) if changed else 'none'}")
    if changed and not args.dry_run:
        args.categories.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
