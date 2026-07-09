#!/usr/bin/env python3
"""Apply verified corrections from category_corrections.json to categories.json."""
# Only applies HIGH-confidence corrections by default.
#
# Usage:
#     python3 apply_corrections.py              # Apply high-confidence only
#     python3 apply_corrections.py --all        # Apply all corrections
#     python3 apply_corrections.py --dry-run    # Preview without saving
import json
import os
import sys
import shutil
from datetime import date, datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CATEGORIES_PATH = os.path.join(PROJECT_DIR, "CaskHub", "Resources", "categories.json")
CORRECTIONS_PATH = os.path.join(PROJECT_DIR, "category_corrections.json")


def main():
    dry_run = "--dry-run" in sys.argv
    apply_all = "--all" in sys.argv

    with open(CATEGORIES_PATH) as f:
        cat_data = json.load(f)
    with open(CORRECTIONS_PATH) as f:
        corrections = json.load(f)

    if not apply_all:
        corrections = [c for c in corrections if c.get("confidence") == "high"]

    print(f"Corrections to apply: {len(corrections)}")
    if dry_run:
        print("DRY RUN - no changes will be saved\n")

    applied = 0
    skipped = 0
    for corr in corrections:
        token = corr["token"]
        new_cat = corr["shouldBe"]
        tc = cat_data["tokenToCategory"]

        if token not in tc:
            print(f"  SKIP {token}: not found in categories.json")
            skipped += 1
            continue

        mapping = tc[token]
        old_cat = mapping["primary"] if isinstance(mapping, dict) else mapping

        if old_cat == new_cat:
            skipped += 1
            continue

        if dry_run:
            print(f"  {token}: {old_cat} -> {new_cat}")
        else:
            if isinstance(mapping, dict):
                mapping["primary"] = new_cat
            else:
                tc[token] = {"primary": new_cat, "secondary": []}

        applied += 1

    print(f"\nApplied: {applied}, Skipped: {skipped}")

    if not dry_run and applied > 0:
        # Backup first
        backup_path = CATEGORIES_PATH + f".backup_{datetime.now().isoformat(timespec='seconds').replace(':', '-')}"
        shutil.copy2(CATEGORIES_PATH, backup_path)
        print(f"Backup saved to {backup_path}")

        # Update date
        cat_data["generatedDate"] = date.today().isoformat()

        with open(CATEGORIES_PATH, "w") as f:
            json.dump(cat_data, f, indent=2, ensure_ascii=False)
        print("Saved updated categories.json")


if __name__ == "__main__":
    main()
