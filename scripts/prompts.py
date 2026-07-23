"""Build classification prompts from the live catalog and boundary rules."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


TRAIT_CATEGORIES: frozenset[str] = frozenset({"ai"})


SCOPE_RULES: dict[str, str] = {
    "developerTools": (
        "IDEs, code editors, version control, API clients, databases, "
        "SDKs, terminal emulators, debuggers, CI/CD, containers, build tools, "
        "game engines and game-creation tools. "
        "ALSO: cloud CLIs (gcloud, aws-cli) and SSH/SFTP clients."
    ),
    "browsers": (
        "Web browsers ONLY (Chrome, Firefox, Arc, Brave, etc.). "
        "NOT: database browsers, file browsers, app wrappers (Biscuit, Wavebox), "
        "or developer testing tools (Polypane, Responsively, Blisk)."
    ),
    "communication": (
        "Chat, email, video conferencing, social media clients, messaging, team chat. "
        "NOT: AI chatbot/LLM clients (those go to productivity with secondary=ai)."
    ),
    "productivity": (
        "Note-taking, task management, calendars, writing tools, clipboard managers, "
        "text expanders, RSS readers, PDF editors, project management. "
        "ALSO: AI assistants/chatbots (LLM clients) - add secondary=ai. "
        "ALSO: digital signature / e-signing tools. "
        "NOT: full office suites (those go to officeTools)."
    ),
    "utilities": (
        "System utilities, file managers, disk tools, backup, uninstallers, "
        "converters, system monitoring, drivers, input methods. "
        "ALSO: remote desktop, download managers, torrent clients, file transfer tools, "
        "game controller/peripheral configurators. "
        "Default 'when in doubt' bucket - but only when truly system-level."
    ),
    "designGraphics": (
        "Image/photo editors, vector tools, 3D modeling, UI/UX design, "
        "screenshot annotation, color pickers, icon editors, digital art, CAD."
    ),
    "audioMusic": (
        "DAWs, music players, audio editors, synthesizers, DJ software, podcast tools, "
        "audio plugins (VST/AU), music streaming, music notation, MIDI tools."
    ),
    "videoMedia": (
        "Video players, video editors, screen recorders (e.g. OBS), streaming software, "
        "video converters, media servers, webcam tools, subtitle editors."
    ),
    "games": (
        "Games, game launchers, console emulators, and tools focused on playing games. "
        "NOT: game engines (developerTools) or controller config software (utilities)."
    ),
    "securityPrivacy": (
        "VPNs, password managers, encryption, firewalls, antivirus, privacy tools, "
        "network security, ad blockers, 2FA/authenticators. "
        "NOT: crypto wallets (financeCrypto), digital signatures (productivity)."
    ),
    "financeCrypto": (
        "Trading, banking, crypto wallets, accounting, invoicing, tax tools, "
        "budgeting, financial planning."
    ),
    "cloudStorage": (
        "Cloud storage/sync services (Dropbox, Google Drive, iCloud, OneDrive), "
        "NAS clients, WebDAV, cloud backup. "
        "NOT: remote desktop, NOT SSH, NOT download managers, NOT cloud CLIs."
    ),
    "scienceEducation": (
        "Scientific tools, calculator apps, education platforms, language learning, "
        "research tools, reference managers, astronomy, chemistry, math, GIS."
    ),
    "menuBar": (
        "Apps whose PRIMARY purpose is providing a menu-bar widget. "
        "If the app has a real window/UI and the menu bar is incidental, classify "
        "by what the app actually does."
    ),
    "officeTools": (
        "Full office suites and their components - Microsoft Office (Word, Excel, "
        "PowerPoint, OneNote, Outlook), LibreOffice, OnlyOffice, WPS Office, "
        "FreeOffice. Standalone PDF editors / single-purpose document tools stay "
        "in productivity; this category is for full suite vendors."
    ),
    "screensaverWallpaper": (
        "macOS screensavers and dynamic/wallpaper apps. Bing Wallpaper, Aerial, "
        "Fliqlo, Wallpaper Wizard, etc."
    ),
    "ai": (
        "TRAIT - never primary. Add as secondary to any cask whose core value is "
        "powered by an LLM/AI: ChatGPT/Claude/local-LLM clients, AI image/video "
        "generators, AI coding assistants, agentic tools."
    ),
    "other": (
        "A last resort for applications without a defensible fit. If you reach "
        "for this, pick the closest of utilities/productivity/scienceEducation instead."
    ),
}


EVIDENCE_PRIORITY = (
    "Your training knowledge of what the app does (highest)",
    "Homepage <title> + meta description",
    "Cask description from Homebrew (sometimes wrong/vague)",
    "Token name (often misleading)",
    "Homepage domain (contextual hint only)",
)


@dataclass(frozen=True)
class CategoryCatalog:
    """Validated category list loaded from categories.json."""

    primary_ids: frozenset[str]   # categories valid as a primary
    secondary_ids: frozenset[str] # categories valid as secondary (= all categories)
    display_names: dict[str, str]

    @classmethod
    def load(cls, categories_json_path: Path) -> "CategoryCatalog":
        data = json.loads(categories_json_path.read_text(encoding="utf-8"))
        cats = data["categories"]
        all_ids = frozenset(cats.keys())
        return cls(
            primary_ids=all_ids - TRAIT_CATEGORIES,
            secondary_ids=all_ids,
            display_names={k: v["displayName"] for k, v in cats.items()},
        )


SYSTEM_PROMPT_TEMPLATE = """\
You classify Homebrew cask applications into a fixed taxonomy used by the CaskHub macOS app.

# Output format
Return strict JSON only - no prose, no markdown - with this exact shape:
{{
  "primary": "<category-id>",
  "secondary": ["<category-id>", ...],   // 0 to 2 entries
  "confidence": <float between 0 and 1>,
  "reason": "<one short sentence justifying the choice>"
}}

# Valid primary categories
You MUST pick exactly one of these IDs as `primary`:
{primary_list}

# Valid secondary categories (traits and overlaps)
`secondary` may contain 0–2 of these IDs (must NOT duplicate `primary`):
{secondary_list}

# Scope rules (boundaries between categories)
{scope_block}

# Evidence priority (use in this order)
{evidence_list}

# Hard constraints
- Never invent category IDs. If nothing fits well, pick `other` and lower confidence.
- `primary` MUST be in the primary list above.
- `ai` is a TRAIT - never primary. Add it as secondary for AI-first apps.
- Confidence should reflect ambiguity: 0.95+ for unmistakable apps, 0.6–0.8 for "likely but homepage was thin", below 0.5 means you're guessing.
- Output JSON only. No commentary.
"""


def build_system_prompt(catalog: CategoryCatalog) -> str:
    """Construct the system prompt from the live catalog + static scope rules."""
    primary_list = "\n".join(
        f"- `{cid}` ({catalog.display_names[cid]})" for cid in sorted(catalog.primary_ids)
    )
    secondary_list = "\n".join(
        f"- `{cid}` ({catalog.display_names[cid]})" for cid in sorted(catalog.secondary_ids)
    )
    scope_block = "\n".join(
        f"- **{cid}** - {SCOPE_RULES.get(cid, '(no scope rule defined)')}"
        for cid in sorted(catalog.secondary_ids)
    )
    evidence_list = "\n".join(f"{i + 1}. {e}" for i, e in enumerate(EVIDENCE_PRIORITY))
    return SYSTEM_PROMPT_TEMPLATE.format(
        primary_list=primary_list,
        secondary_list=secondary_list,
        scope_block=scope_block,
        evidence_list=evidence_list,
    )


def build_user_prompt(cask: dict, homepage_meta: dict | None) -> str:
    """One cask → one classification request."""
    parts = [
        f"# Cask: `{cask['token']}`",
        f"Name: {cask.get('name') or '(none)'}",
        f"Description: {cask.get('desc') or '(none)'}",
        f"Homepage: {cask.get('homepage') or '(none)'}",
    ]
    if homepage_meta and "error" not in homepage_meta:
        parts.append("")
        parts.append("# Homepage metadata")
        parts.append(f"Title: {homepage_meta.get('title') or '(none)'}")
        parts.append(f"Meta description: {homepage_meta.get('meta_desc') or '(none)'}")
        if homepage_meta.get("og_desc"):
            parts.append(f"og:description: {homepage_meta['og_desc']}")
    elif homepage_meta and "error" in homepage_meta:
        parts.append("")
        parts.append(f"# Homepage fetch failed: {homepage_meta['error']}")
    parts.append("")
    parts.append("Classify this cask. JSON only.")
    return "\n".join(parts)
