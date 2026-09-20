"""Report application-identity coverage without downloading or installing casks."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app_identities import declared_artifact_apps, needs_refresh, package_selection_reason
from classify_new_casks import is_main_cask
from extract_icons import eligibility
from style_standards import write_json

NON_APPLICATION_ARTIFACTS = {
    "binary", "command_wrapper", "generated_script", "generate_completions_from_executable",
    "bash_completion", "zsh_completion", "fish_completion", "manpage", "font", "dictionary",
    "screen_saver", "keyboard_layout", "colorpicker", "prefpane", "input_method", "internet_plugin",
    "qlplugin", "mdimporter", "service", "audio_unit_plugin", "vst_plugin", "vst3_plugin", "stage_only"
}
METADATA_STANZAS = {"target", "uninstall", "zap", "preflight", "postflight", "preflight_steps", "postflight_steps",
                    "uninstall_preflight_steps", "uninstall_postflight_steps"}


def _unsupported_reason(cask: dict, kinds: set[str]) -> tuple[str, str]:
    if "installer" in kinds:
        modes = {key for stanza in cask.get("artifacts") or [] for entry in stanza.get("installer", [])
                 if isinstance(entry, dict) for key in entry}
        return "unsupported", "custom installer needs payload inspection: " + ", ".join(sorted(modes))
    if "app_image" in kinds:
        return "not_applicable", "Linux AppImage is outside macOS application identity coverage"
    if kinds and kinds <= NON_APPLICATION_ARTIFACTS:
        return "not_applicable", "non-application artifacts need their own detection: " + ", ".join(sorted(kinds))
    return "unsupported", "no verified application install path: " + ", ".join(sorted(kinds))


def classify(cask: dict, raw: dict | None, published: list, report: dict) -> tuple[str, str]:
    if published:
        return "mapped", "published application identity"
    if raw and raw.get("apps"):
        return "extracted_unpublished", "verified raw identity is absent from the consumer projection"
    if reason := package_selection_reason(cask):
        return "unsupported", reason
    kinds = {key for stanza in cask.get("artifacts") or [] for key in stanza} - METADATA_STANZAS
    if eligibility(cask) is not None:
        return _unsupported_reason(cask, kinds)
    if report.get("reason", "").startswith("unsupported container:"):
        return "unsupported", report["reason"]
    if report.get("status") == "failed":
        return "failed", report.get("reason", "extraction failed")
    if raw and not needs_refresh(cask, raw):
        return "empty", "inspected without an accepted application identity"
    return "needs_inspection", "newly supported or stale extraction"


def audit(casks: list[dict], identities: dict, categories: dict, report: dict) -> dict:
    rows = []
    for cask in sorted(filter(is_main_cask, casks), key=lambda c: c["token"]):
        token = cask["token"]
        raw = identities.get("casks", {}).get(token)
        published = categories.get("appIdentities", {}).get(token, [])
        status, reason = classify(cask, raw, published, report.get(token, {}))
        rows.append({"token": token, "status": status, "reason": reason,
                     "needsReinspection": eligibility(cask) is None and needs_refresh(cask, raw),
                     "declaredGenericApps": [name for _, name in declared_artifact_apps(cask)],
                     "diagnostics": (raw or {}).get("diagnostics", []),
                     "rawIdentities": (raw or {}).get("apps", []), "publishedIdentities": published})
    return {"scope": "macOS application bundles, not all installed software",
            "catalogCasks": len(rows), "counts": dict(Counter(row["status"] for row in rows)), "casks": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["casks", "identities", "categories", "report", "output"]:
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    result = audit(*(json.loads(getattr(args, name).read_text(encoding="utf-8"))
                     for name in ["casks", "identities", "categories", "report"]))
    write_json(args.output, result, trailing_newline=True)
    print(json.dumps({"catalogCasks": result["catalogCasks"], "counts": result["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
