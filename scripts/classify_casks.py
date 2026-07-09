#!/usr/bin/env python3
"""Generate categories.json by classifying Homebrew casks into 15 categories."""
# Usage:
#     python3 Scripts/classify_casks.py
#
# Output:
#     CaskHub/Resources/categories.json

import json
import urllib.request
import os
from datetime import date
from pathlib import Path

CATEGORIES = [
    "developerTools",
    "browsers",
    "communication",
    "productivity",
    "utilities",
    "designGraphics",
    "audioMusic",
    "videoMedia",
    "games",
    "securityPrivacy",
    "financeCrypto",
    "cloudStorage",
    "scienceEducation",
    "menuBar",
    "other",
]

# Classification rules are data, not code — they live in
# data/classification_rules.json. Rule order matters and JSON preserves it.
# Priority: manual overrides > token prefixes > homepage domains > keywords.
_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "classification_rules.json"
_RULES = json.loads(_RULES_PATH.read_text())
MANUAL_OVERRIDES: dict[str, str] = _RULES["manualOverrides"]
TOKEN_PREFIX_RULES: dict[str, str] = _RULES["tokenPrefixRules"]
HOMEPAGE_DOMAIN_RULES: dict[str, str] = _RULES["homepageDomainRules"]
# (category, desc_keywords, name_keywords, negative_keywords)
KEYWORD_RULES: list[tuple] = [tuple(r) for r in _RULES["keywordRules"]]


def _rule_category(token, homepage):
    """Priority 1-3: manual overrides, token prefixes, homepage domains."""
    if token in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[token]
    for prefix, category in TOKEN_PREFIX_RULES.items():
        if token.startswith(prefix):
            return category
    for domain, category in HOMEPAGE_DOMAIN_RULES.items():
        if domain in homepage:
            return category
    return None


def _keyword_category(name, desc, text):
    """Priority 4: keyword matching, negative keywords veto a rule."""
    for category, desc_keywords, name_keywords, negative_keywords in KEYWORD_RULES:
        if any(neg in text for neg in negative_keywords):
            continue
        if any(kw in desc for kw in desc_keywords):
            return category
        if any(kw in name for kw in name_keywords):
            return category
    return None


def classify_cask(cask):
    """Classify a single cask into a category."""
    token = cask.get("token", "")
    name = (cask.get("name", [None])[0] or token).lower()
    desc = (cask.get("desc") or "").lower()
    homepage = (cask.get("homepage") or "").lower()

    rule_hit = _rule_category(token, homepage)
    if rule_hit:
        return rule_hit
    return _keyword_category(name, desc, f"{name} {desc}") or "other"


def _fetch_active_casks():
    """Homebrew API casks, filtered like the app filters them."""
    print("Fetching casks from Homebrew API...")
    url = "https://formulae.brew.sh/api/cask.json"
    with urllib.request.urlopen(url) as response:
        casks = json.loads(response.read().decode())
    print(f"Total casks: {len(casks)}")
    active = [
        c for c in casks
        if not c.get("deprecated")
        and not c.get("disabled")
        and "@" not in c["token"]
        and not c["token"].startswith("font-")
    ]
    print(f"Active (non-font, non-deprecated): {len(active)}")
    return active


def _print_stats(category_counts, total):
    print("\n── Classification Results ──")
    for cat in CATEGORIES:
        count = category_counts[cat]
        pct = (count / total) * 100
        bar = "█" * int(pct / 2)
        print(f"  {cat:20s}  {count:5d}  ({pct:5.1f}%)  {bar}")


def _write_output(token_to_category):
    output = {
        "version": 1,
        "generatedDate": str(date.today()),
        "totalCasks": len(token_to_category),
        "categories": {cat: {"displayName": display_name(cat), "icon": icon_for(cat)}
                       for cat in CATEGORIES},
        "tokenToCategory": token_to_category,
    }
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "CaskHub", "Resources")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "categories.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, sort_keys=False)
    print(f"\nWritten to: {output_path}")


def _print_samples(token_to_category):
    print("\n── Samples per Category ──")
    for cat in CATEGORIES:
        samples = [t for t, c in token_to_category.items() if c == cat][:5]
        print(f"  {cat}: {', '.join(samples)}")


def main():
    active = _fetch_active_casks()

    token_to_category = {}
    category_counts = {cat: 0 for cat in CATEGORIES}
    for cask in active:
        category = classify_cask(cask)
        token_to_category[cask["token"]] = category
        category_counts[category] += 1

    _print_stats(category_counts, len(active))
    print(f"\n  Total classified: {len(token_to_category)}")
    _write_output(token_to_category)
    _print_samples(token_to_category)


def display_name(category_id):
    """Convert camelCase ID to display name."""
    names = {
        "developerTools": "Developer Tools",
        "browsers": "Browsers",
        "communication": "Communication",
        "productivity": "Productivity",
        "utilities": "Utilities",
        "designGraphics": "Design & Graphics",
        "audioMusic": "Audio & Music",
        "videoMedia": "Video & Media",
        "games": "Games",
        "securityPrivacy": "Security & Privacy",
        "financeCrypto": "Finance & Crypto",
        "cloudStorage": "Cloud & Storage",
        "scienceEducation": "Science & Education",
        "menuBar": "Menu Bar",
        "other": "Other",
    }
    return names.get(category_id, category_id)


def icon_for(category_id):
    """SF Symbol icon for each category."""
    icons = {
        "developerTools": "chevron.left.forwardslash.chevron.right",
        "browsers": "globe",
        "communication": "bubble.left.and.bubble.right",
        "productivity": "checkmark.circle",
        "utilities": "wrench.and.screwdriver",
        "designGraphics": "paintbrush",
        "audioMusic": "music.note",
        "videoMedia": "play.rectangle",
        "games": "gamecontroller",
        "securityPrivacy": "lock.shield",
        "financeCrypto": "dollarsign.circle",
        "cloudStorage": "cloud",
        "scienceEducation": "graduationcap",
        "menuBar": "menubar.rectangle",
        "other": "square.grid.2x2",
    }
    return icons.get(category_id, "questionmark")


if __name__ == "__main__":
    main()
