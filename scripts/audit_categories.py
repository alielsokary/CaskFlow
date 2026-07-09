#!/usr/bin/env python3
"""Verify every cask's category using homepage metadata and cask descriptions."""
# Outputs a corrections JSON and a full audit report.
#
# Usage:
#     python3 audit_categories.py
#
# Requires:
#     ../homepage_metadata.json  (from fetch_homepages.py)
#     ../CaskHub/Resources/categories.json
#     ../filtered_casks.json
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CATEGORIES_PATH = os.path.join(PROJECT_DIR, "CaskHub", "Resources", "categories.json")
CASKS_PATH = os.path.join(PROJECT_DIR, "filtered_casks.json")
METADATA_PATH = os.path.join(PROJECT_DIR, "homepage_metadata.json")
CORRECTIONS_PATH = os.path.join(PROJECT_DIR, "category_corrections.json")
REPORT_PATH = os.path.join(PROJECT_DIR, "audit_report.md")

# ============================================================
# CATEGORY DEFINITIONS - used for keyword matching
# ============================================================
CATEGORY_SIGNALS = {
    "developerTools": {
        "strong": [
            "ide", "code editor", "developer tool", "devtool", "sdk", "api client",
            "terminal emulator", "debugger", "ci/cd", "version control", "git client",
            "database management", "sql client", "container", "docker", "kubernetes",
            "compiler", "linter", "formatter", "package manager", "build tool",
            "profiler", "decompiler", "disassembler", "hex editor", "diff tool",
            "merge tool", "ssh client", "sftp", "rest client", "graphql",
            "json viewer", "xml editor", "regex", "devops", "deployment",
            "testing framework", "test runner", "code review", "source control",
        ],
        "domains": [
            "jetbrains.com", "github.com", "gitlab.com", "bitbucket.org",
            "docker.com", "kubernetes.io", "sublimetext.com", "atom.io",
        ],
    },
    "browsers": {
        "strong": [
            "web browser", "internet browser", "chromium-based browser",
            "firefox-based", "webkit browser",
        ],
        "negative": [
            "database browser", "file browser", "image browser", "photo browser",
            "api browser", "app browser", "workspace", "web app",
        ],
        "domains": [
            "mozilla.org", "brave.com", "vivaldi.com", "arc.net",
        ],
    },
    "communication": {
        "strong": [
            "messaging", "chat app", "email client", "video call", "video conferencing",
            "voice call", "voip", "instant messenger", "social media client",
            "team communication", "collaboration chat", "irc client", "xmpp",
            "matrix client", "slack", "discord", "teams",
        ],
        "negative": [
            "ai chat", "chatbot", "llm", "gpt", "ai assistant", "ai companion",
        ],
    },
    "productivity": {
        "strong": [
            "note taking", "notes app", "task manager", "to-do", "todo list",
            "calendar app", "office suite", "word processor", "spreadsheet",
            "presentation", "writing tool", "markdown editor", "text editor",
            "clipboard manager", "text expander", "snippet manager",
            "project management", "time tracker", "pomodoro", "pdf editor",
            "document", "wiki", "knowledge base", "bookmarks manager",
            "rss reader", "feed reader", "ai assistant", "ai chat",
        ],
        "domains": [
            "notion.so", "obsidian.md", "todoist.com", "trello.com",
            "asana.com", "monday.com", "evernote.com",
        ],
    },
    "utilities": {
        "strong": [
            "system utility", "file manager", "disk utility", "disk cleaner",
            "uninstaller", "archive", "compression", "backup tool",
            "system monitor", "driver", "printer driver", "input method",
            "clipboard", "window manager", "screen capture", "screenshot",
            "file transfer", "remote desktop", "vnc", "rdp",
            "download manager", "torrent client", "bittorrent",
            "converter", "batch rename", "automator", "calculator",
            "usb", "bluetooth", "wifi", "network utility",
        ],
    },
    "designGraphics": {
        "strong": [
            "image editor", "photo editor", "vector editor", "illustration",
            "3d modeling", "3d rendering", "cad", "ui design", "ux design",
            "graphic design", "icon editor", "pixel art", "digital art",
            "color picker", "mockup", "wireframe", "prototype", "animation",
            "svg editor", "font editor", "typography",
        ],
        "domains": [
            "figma.com", "sketch.com", "adobe.com", "canva.com",
            "blender.org", "inkscape.org", "gimp.org",
        ],
    },
    "audioMusic": {
        "strong": [
            "daw", "digital audio workstation", "music player", "audio editor",
            "synthesizer", "synth", "dj software", "podcast", "audio plugin",
            "vst", "au plugin", "music streaming", "music production",
            "midi", "sequencer", "sampler", "equalizer", "eq",
            "audio recorder", "sound editor", "music notation",
            "karaoke", "lyrics", "audio converter", "music library",
        ],
        "negative": [
            "video editor", "screen recorder",
        ],
    },
    "videoMedia": {
        "strong": [
            "video player", "video editor", "screen recorder", "streaming software",
            "obs", "video converter", "media server", "plex", "webcam",
            "subtitle", "video downloader", "dvd", "blu-ray",
            "live stream", "broadcast", "iptv", "media player",
            "video transcoder", "handbrake",
        ],
    },
    "games": {
        "strong": [
            "game", "gaming", "game launcher", "game engine", "emulator",
            "steam", "gog", "epic games", "game mod", "rom",
            "retro gaming", "arcade", "puzzle game", "fps", "rpg",
            "mmorpg", "strategy game", "chess", "game trainer",
        ],
        "negative": [
            "game controller config", "game capture",
        ],
    },
    "securityPrivacy": {
        "strong": [
            "vpn", "password manager", "encryption", "firewall",
            "antivirus", "malware", "privacy", "ad blocker",
            "network security", "vulnerability", "penetration testing",
            "2fa", "two-factor", "authenticator", "keychain",
            "secure delete", "file vault", "tor browser",
        ],
        "negative": [
            "budgeting", "finance", "wallet", "crypto wallet",
            "digital signature", "e-signature",
        ],
    },
    "financeCrypto": {
        "strong": [
            "trading", "stock", "forex", "crypto", "bitcoin", "ethereum",
            "wallet", "banking", "accounting", "invoicing", "tax",
            "budgeting", "financial", "portfolio", "investment",
            "bookkeeping", "expense tracker", "payroll",
        ],
    },
    "cloudStorage": {
        "strong": [
            "cloud storage", "cloud sync", "file sync", "cloud drive",
            "dropbox", "google drive", "onedrive", "icloud", "s3",
            "nas client", "webdav", "ftp client", "cloud backup",
            "object storage",
        ],
        "negative": [
            "remote desktop", "ssh", "vnc", "rdp", "printer",
            "download manager", "torrent", "driver",
        ],
    },
    "scienceEducation": {
        "strong": [
            "scientific", "calculator", "education", "learning platform",
            "language learning", "flashcard", "research", "reference",
            "astronomy", "chemistry", "physics", "biology", "math",
            "statistics", "data science", "jupyter", "lab",
            "gis", "mapping", "genome", "bioinformatics",
        ],
    },
    "menuBar": {
        "strong": [
            "menu bar", "menubar", "status bar", "system tray",
        ],
    },
}


def load_data():
    with open(CATEGORIES_PATH) as f:
        cat_data = json.load(f)
    with open(CASKS_PATH) as f:
        casks = json.load(f)

    metadata = {}
    if os.path.exists(METADATA_PATH):
        with open(METADATA_PATH) as f:
            for item in json.load(f):
                metadata[item["token"]] = item

    return cat_data, casks, metadata


def build_text(cask, meta):
    """Build a searchable text blob from all available info."""
    parts = [
        cask.get("desc") or "",
        cask.get("token", "").replace("-", " "),
        meta.get("title") or "",
        meta.get("meta_desc") or "",
        meta.get("og_desc") or "",
    ]
    return " ".join(parts).lower()


def score_category(text, homepage, cat_id):
    """Score how well text matches a category. Higher = better match."""
    signals = CATEGORY_SIGNALS.get(cat_id, {})
    score = 0

    # Strong keyword matches
    for kw in signals.get("strong", []):
        if kw in text:
            score += 10

    # Negative signals (things that suggest NOT this category)
    for kw in signals.get("negative", []):
        if kw in text:
            score -= 15

    # Domain matches
    if homepage:
        for domain in signals.get("domains", []):
            if domain in homepage.lower():
                score += 20

    return score


def audit_all(cat_data, casks, metadata):
    """Audit every cask and return suspected miscategorizations."""
    tc = cat_data["tokenToCategory"]
    cask_map = {c["token"]: c for c in casks}
    confident = []
    uncertain = []

    for token, mapping in tc.items():
        current_cat = mapping["primary"] if isinstance(mapping, dict) else mapping
        cask = cask_map.get(token, {"token": token, "desc": ""})
        meta = metadata.get(token, {})

        text = build_text(cask, meta)
        homepage = cask.get("homepage", "")

        # Score all categories
        scores = {}
        for cat_id in CATEGORY_SIGNALS:
            scores[cat_id] = score_category(text, homepage, cat_id)

        # Find best match
        best_cat = max(scores, key=scores.get)
        best_score = scores[best_cat]
        current_score = scores.get(current_cat, 0)

        # Flag if another category scores significantly higher
        if best_cat != current_cat and best_score > current_score + 10:
            has_meta = bool(meta.get("title") or meta.get("meta_desc"))
            entry = {
                "token": token,
                "was": current_cat,
                "shouldBe": best_cat,
                "currentScore": current_score,
                "bestScore": best_score,
                "desc": (cask.get("desc") or "")[:100],
                "homepage": homepage,
                "hpTitle": (meta.get("title") or "")[:100],
                "hpMeta": (meta.get("meta_desc") or meta.get("og_desc") or "")[:150],
                "hasMeta": has_meta,
            }
            if best_score >= 20 and has_meta:
                entry["confidence"] = "high"
                confident.append(entry)
            else:
                entry["confidence"] = "medium"
                uncertain.append(entry)

    return confident, uncertain


def generate_report(confident, uncertain, cat_data):
    """Generate a markdown audit report."""
    tc = cat_data["tokenToCategory"]
    categories = cat_data["categories"]

    # Current distribution
    dist = {}
    for mapping in tc.values():
        cat = mapping["primary"] if isinstance(mapping, dict) else mapping
        dist[cat] = dist.get(cat, 0) + 1

    lines = ["# CaskHub Category Audit Report", ""]
    lines.append(f"**Total casks**: {len(tc)}")
    lines.append(f"**High-confidence corrections**: {len(confident)}")
    lines.append(f"**Medium-confidence flags**: {len(uncertain)}")
    lines.append("")

    # Distribution table
    lines.append("## Current Distribution")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|----------|-------|")
    for cat_id in sorted(dist, key=dist.get, reverse=True):
        name = categories.get(cat_id, {}).get("displayName", cat_id)
        lines.append(f"| {name} | {dist[cat_id]} |")
    lines.append("")

    # High confidence corrections
    lines.append("## High-Confidence Corrections")
    lines.append("")
    if confident:
        # Group by source category
        by_source = {}
        for c in confident:
            by_source.setdefault(c["was"], []).append(c)

        for src_cat in sorted(by_source):
            src_name = categories.get(src_cat, {}).get("displayName", src_cat)
            items = by_source[src_cat]
            lines.append(f"### From {src_name} ({len(items)} corrections)")
            lines.append("")
            for item in sorted(items, key=lambda x: x["token"]):
                dest_name = categories.get(item["shouldBe"], {}).get("displayName", item["shouldBe"])
                lines.append(f"- **{item['token']}** → {dest_name}")
                lines.append(f"  - Desc: {item['desc']}")
                if item.get("hpTitle"):
                    lines.append(f"  - Homepage title: {item['hpTitle']}")
                if item.get("hpMeta"):
                    lines.append(f"  - Homepage meta: {item['hpMeta']}")
                lines.append("")
    else:
        lines.append("None found.")
        lines.append("")

    # Medium confidence
    lines.append("## Medium-Confidence Flags (Review Needed)")
    lines.append("")
    if uncertain:
        for item in sorted(uncertain, key=lambda x: x["bestScore"] - x["currentScore"], reverse=True)[:100]:
            src_name = categories.get(item["was"], {}).get("displayName", item["was"])
            dest_name = categories.get(item["shouldBe"], {}).get("displayName", item["shouldBe"])
            lines.append(f"- **{item['token']}**: {src_name} → {dest_name}? (score diff: +{item['bestScore'] - item['currentScore']})")
            lines.append(f"  - Desc: {item['desc']}")
            lines.append("")
    else:
        lines.append("None found.")
        lines.append("")

    return "\n".join(lines)


def main():
    print("Loading data...")
    cat_data, casks, metadata = load_data()
    meta_count = sum(1 for m in metadata.values() if m.get("title") or m.get("meta_desc"))
    print(f"  Categories: {len(cat_data['categories'])}")
    print(f"  Casks: {len(casks)}")
    print(f"  Homepage metadata: {len(metadata)} ({meta_count} with content)")

    print("\nAuditing categories...")
    confident, uncertain = audit_all(cat_data, casks, metadata)
    print(f"  High-confidence corrections: {len(confident)}")
    print(f"  Medium-confidence flags: {len(uncertain)}")

    # Save corrections
    all_corrections = confident + uncertain
    with open(CORRECTIONS_PATH, "w") as f:
        json.dump(all_corrections, f, indent=2, ensure_ascii=False)
    print(f"\nSaved corrections to {CORRECTIONS_PATH}")

    # Generate report
    report = generate_report(confident, uncertain, cat_data)
    with open(REPORT_PATH, "w") as f:
        f.write(report)
    print(f"Saved report to {REPORT_PATH}")


if __name__ == "__main__":
    main()
