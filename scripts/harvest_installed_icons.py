"""Install casks on a disposable runner and harvest the icon of the app they add.

Covers casks whose download holds no inspectable app: installer scripts and
packages that create the app while installing. Publication validates every
runner result, because the runner executed vendor code.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from extract_icons import _icon_status, _pick_candidate, _record_ok, installerish, publish_batch
from icons_state import load_report, published_tokens, record
from observe_package_install import Commands, changes, require_disposable_runner, save_evidence, snapshot

TOKEN = re.compile(r"[a-z0-9][a-z0-9.+@_-]*")
MAX_TOKENS = 50
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 2_000_000
STATUSES = ("ok", "no_icon", "car_only", "failed")


def tokens_from(text: str) -> list[str]:
    tokens = text.split()
    if (not tokens or len(tokens) > MAX_TOKENS or len(set(tokens)) != len(tokens)
            or not all(TOKEN.fullmatch(token) for token in tokens)):
        raise ValueError(f"Expected 1-{MAX_TOKENS} distinct cask tokens")
    return tokens


def matrix(text: str) -> dict:
    return {"include": [{"token": token} for token in tokens_from(text)]}


def pick_app(apps: list[Path], token: str) -> tuple[Path, str] | None:
    candidates = [app for app in apps if not installerish(app.name)]
    return _pick_candidate(candidates, token) if candidates else None


def harvest(token: str, output: Path) -> None:
    require_disposable_runner("arm64")
    output.mkdir(parents=True)
    roots = [Path("/Applications"), Path.home() / "Applications"]
    before = snapshot(roots, Path("/var/db/receipts"))
    install = Commands(output).run("install", ["brew", "install", "--cask", token], 1800, check=False)
    after = snapshot(roots, Path("/var/db/receipts"))
    added = [Path(app["path"]) for app in changes(before["applications"], after["applications"])["added"]]
    hit = pick_app(added, token)
    if install["timedOut"] or install["returncode"]:
        status, detail = "failed", "brew install failed"
    elif hit is None:
        status, detail = "no_icon", "install added only installer/updater apps" if added else "install added no app"
    else:
        status, detail = _icon_status(hit[0], hit[1], output / "icon.png")
    save_evidence(output / "harvest.json", {"token": token, "status": status, "detail": detail,
                                            "app": str(hit[0]) if hit else None,
                                            "added": [str(app) for app in added]})


def _valid_png(path: Path) -> bool:
    return (path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_PNG_BYTES
            and path.read_bytes()[:len(PNG_MAGIC)] == PNG_MAGIC)


def publish(directory: Path, text: str) -> None:
    """Fold runner results into the icons branch; the planned token list is the only trust anchor."""
    report, done = load_report(), published_tokens()
    pngs, dirty = {}, set()
    for token in tokens_from(text):
        evidence = directory / f"harvest-{token}"
        try:
            result = json.loads((evidence / "harvest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # the job died before recording a result
        status, detail = result.get("status"), str(result.get("detail", ""))[:200]
        if token in done or result.get("token") != token or status not in STATUSES:
            continue
        if status == "ok":
            if not _valid_png(evidence / "icon.png"):
                continue
            pngs[token] = evidence / "icon.png"
            _record_ok(token, detail, report)
        else:
            record(report, token, status, f"install harvest: {detail}")
        dirty.add(token)
    publish_batch(pngs, report, dirty)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("matrix").add_argument("--tokens", required=True)
    run = commands.add_parser("harvest")
    run.add_argument("--token", required=True)
    run.add_argument("--output", required=True, type=Path)
    publishing = commands.add_parser("publish")
    publishing.add_argument("--tokens", required=True)
    publishing.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "matrix":
        print(json.dumps(matrix(args.tokens)))
    elif args.command == "harvest":
        harvest(tokens_from(args.token)[0], args.output)
    else:
        publish(args.directory, args.tokens)


if __name__ == "__main__":
    main()
