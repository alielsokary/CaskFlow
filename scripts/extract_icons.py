#!/usr/bin/env python3
"""
Icon extraction pipeline — see docs/superpowers/specs/2026-07-05-icon-extraction-design.md.

Per cask: download the vendor artifact, expand it WITHOUT executing anything,
locate the .app bundle, convert its .icns to a 256px PNG, and (optionally)
publish it as a per-cask GitHub pre-release `cask-<token>` with the single
asset `AppIcon.png`. Pre-release is load-bearing: it keeps `releases/latest`
pointing at the data-* releases that CaskHub's CategoryService consumes.

State:
- "Done" = the cask-* releases themselves (queried via `gh api`).
- data/icon_report.json records no_icon / car_only / failed tokens with
  reasons. Failed tokens get MAX_ATTEMPTS tries, then are parked.

Run:
    python scripts/extract_icons.py --tokens obsidian rectangle   # local, no publish
    python scripts/extract_icons.py --publish --limit 50          # CI / backfill batch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "data" / "icon_report.json"
BREW_API = "https://formulae.brew.sh/api/cask.json"
ANALYTICS_API = "https://formulae.brew.sh/api/analytics/cask-install/30d.json"
GITHUB_REPO = "alielsokary/CaskKit"
ICON_SIZE = "256"
MAX_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = 600  # seconds; some vendor artifacts are multi-GB
PUBLISH_THROTTLE = 2.0  # seconds between release creations — stays under
                        # GitHub secondary rate limits on large backfill runs

TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz")


class ExtractError(Exception):
    """Recoverable per-cask failure — recorded in the report, never fatal."""


# ---------------------------------------------------------------------------
# Eligibility (pure — unit tested)
# ---------------------------------------------------------------------------

def app_names_from_artifacts(cask: dict) -> list[str]:
    """`.app` names declared by the cask's `app`/`suite` artifact stanzas."""
    names: list[str] = []
    for artifact in cask.get("artifacts") or []:
        for key in ("app", "suite"):
            for entry in artifact.get(key, []) if isinstance(artifact, dict) else []:
                if isinstance(entry, str):
                    names.append(entry)
                elif isinstance(entry, dict) and isinstance(entry.get("target"), str):
                    names.append(entry["target"])
    return names


def has_pkg_artifact(cask: dict) -> bool:
    return any(
        isinstance(a, dict) and "pkg" in a for a in cask.get("artifacts") or []
    )


def eligibility(cask: dict) -> str | None:
    """None if extractable, else a `no_icon` reason string.

    pkg-artifact casks (Zoom, Teams, …) count as extractable: the installer
    payload contains the .app, which pkgutil --expand-full exposes without
    running any scripts."""
    if cask.get("deprecated") or cask.get("disabled"):
        return "deprecated/disabled"
    if not cask.get("url"):
        return "no download url"
    if not app_names_from_artifacts(cask) and not has_pkg_artifact(cask):
        return "no app/suite/pkg artifact"
    return None


def container_type(cask: dict, filename: str) -> str | None:
    """dmg | zip | pkg | tar — from the download filename or `container` hint."""
    name = filename.lower()
    nested = (cask.get("container") or {}).get("type")
    if nested in ("dmg", "zip", "pkg"):
        return nested
    if name.endswith(".dmg"):
        return "dmg"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".pkg", ".mpkg")):
        return "pkg"
    if name.endswith(TAR_SUFFIXES):
        return "tar"
    if name.endswith(".app"):  # rare: bare .app download
        return "zip" if name.endswith(".zip") else None
    return None


def resolve_icns_name(info: dict) -> str | None:
    """CFBundleIconFile with `.icns` appended when the extension is omitted."""
    name = info.get("CFBundleIconFile")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    return name if name.endswith(".icns") else f"{name}.icns"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def load_report() -> dict[str, dict]:
    if REPORT_PATH.exists():
        return json.loads(REPORT_PATH.read_text())
    return {}


def save_report(report: dict[str, dict]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(dict(sorted(report.items())), indent=2, ensure_ascii=False) + "\n"
    )


def record(report: dict, token: str, status: str, reason: str) -> None:
    prev = report.get(token, {})
    report[token] = {
        "status": status,
        "reason": reason,
        "attempts": prev.get("attempts", 0) + (1 if status == "failed" else 0),
        "updated": str(date.today()),
    }
    save_report(report)  # write-through: crash-safe across a long batch


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw
    )


def download(cask: dict, dest_dir: Path) -> Path:
    url = cask["url"]
    filename = Path(urlparse(url).path).name or "artifact"
    dest = dest_dir / filename
    cmd = ["curl", "-fSL", "--retry", "2", "--max-time", str(DOWNLOAD_TIMEOUT), "-o", str(dest)]
    specs = cask.get("url_specs") or {}
    if specs.get("user_agent"):
        cmd += ["-A", str(specs["user_agent"])]
    if specs.get("referer"):
        cmd += ["-e", str(specs["referer"])]
    for header in specs.get("header") or []:
        cmd += ["-H", str(header)]
    if isinstance(specs.get("cookies"), dict):
        cmd += ["-b", "; ".join(f"{k}={v}" for k, v in specs["cookies"].items())]
    proc = run(cmd + [url])
    if proc.returncode != 0 or not dest.exists():
        raise ExtractError(f"download failed: {proc.stderr.strip().splitlines()[-1] if proc.stderr else 'curl error'}")

    expected = cask.get("sha256")
    if expected and expected != "no_check":
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        if digest != expected:
            raise ExtractError(f"sha256 mismatch (got {digest[:12]}…)")
    return dest


def expand(artifact: Path, kind: str, workdir: Path, mounts: list[Path]) -> Path:
    """Expand without executing anything. Returns the directory to search."""
    workdir.mkdir(parents=True, exist_ok=True)  # nested calls pass workdir/"nested"
    out = workdir / "expanded"
    out.mkdir(exist_ok=True)
    if kind == "dmg":
        mnt = workdir / "mnt"
        proc = run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify",
                    "-mountpoint", str(mnt), str(artifact)])
        if proc.returncode != 0:
            raise ExtractError(f"hdiutil attach failed: {proc.stderr.strip()[:200]}")
        mounts.append(mnt)
        return mnt
    if kind == "zip":
        proc = run(["ditto", "-xk", str(artifact), str(out)])
        if proc.returncode != 0:
            raise ExtractError(f"ditto failed: {proc.stderr.strip()[:200]}")
        return out
    if kind == "tar":
        proc = run(["tar", "-xf", str(artifact), "-C", str(out)])
        if proc.returncode != 0:
            raise ExtractError(f"tar failed: {proc.stderr.strip()[:200]}")
        return out
    if kind == "pkg":
        pkg_out = workdir / "pkg"
        proc = run(["pkgutil", "--expand-full", str(artifact), str(pkg_out)])
        if proc.returncode != 0:
            raise ExtractError(f"pkgutil failed: {proc.stderr.strip()[:200]}")
        return pkg_out
    raise ExtractError(f"unknown container type for {artifact.name}")


def find_app(root: Path, wanted: list[str]) -> Path | None:
    """Locate the .app bundle. Prefers names from the cask's artifacts; never
    follows symlinks (DMGs ship an /Applications symlink)."""
    wanted_lower = {w.lower() for w in wanted}
    found: list[Path] = []
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 6:
            continue
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if entry.name.endswith(".app"):
                if entry.name.lower() in wanted_lower:
                    return entry
                found.append(entry)
            else:
                stack.append((entry, depth + 1))
    # No exact match: a single discovered app is unambiguous; otherwise shallowest.
    if found:
        return sorted(found, key=lambda p: len(p.parts))[0]
    return None


def find_nested_archive(root: Path) -> tuple[Path, str] | None:
    """One level of dmg-in-zip style nesting (guided search, spec §Extraction 3)."""
    for pattern, kind in (("*.dmg", "dmg"), ("*.pkg", "pkg"), ("*.zip", "zip")):
        # is_file: an expanded component package is a *directory* named *.pkg
        hits = [p for p in root.rglob(pattern) if p.is_file() and not p.is_symlink()]
        if hits:
            return hits[0], kind
    return None


def icns_to_png(app: Path, dest_png: Path) -> str | None:
    """Convert the app's icon to PNG. Returns a car_only/no-icon reason or None."""
    plist_path = app / "Contents" / "Info.plist"
    info: dict = {}
    if plist_path.exists():
        try:
            info = plistlib.loads(plist_path.read_bytes())
        except Exception:
            info = {}

    resources = app / "Contents" / "Resources"
    icns_name = resolve_icns_name(info)
    icns = resources / icns_name if icns_name else None

    if icns is None or not icns.exists():
        if info.get("CFBundleIconName") and not (icns and icns.exists()):
            candidates = sorted(resources.glob("*.icns")) if resources.exists() else []
            if not candidates:
                return "car_only"
            icns = max(candidates, key=lambda p: p.stat().st_size)
        else:
            candidates = sorted(resources.glob("*.icns")) if resources.exists() else []
            if not candidates:
                return "no .icns in Resources"
            icns = max(candidates, key=lambda p: p.stat().st_size)

    proc = run(["sips", "-s", "format", "png", "--resampleHeightWidthMax", ICON_SIZE,
                str(icns), "--out", str(dest_png)])
    if proc.returncode != 0 or not dest_png.exists():
        raise ExtractError(f"sips failed: {proc.stderr.strip()[:200]}")
    return None


# ---------------------------------------------------------------------------
# Per-cask pipeline
# ---------------------------------------------------------------------------

def extract_one(cask: dict, output_dir: Path) -> tuple[str, str]:
    """Returns (status, detail) where status ∈ published-ready | no_icon | car_only.
    Raises ExtractError for `failed`."""
    token = cask["token"]
    reason = eligibility(cask)
    if reason:
        return "no_icon", reason

    workdir = Path(tempfile.mkdtemp(prefix=f"icon-{token}-"))
    mounts: list[Path] = []
    try:
        artifact = download(cask, workdir)
        kind = container_type(cask, artifact.name)
        if kind is None:
            return "no_icon", f"unsupported container: {artifact.name}"

        root = expand(artifact, kind, workdir, mounts)
        wanted = [n if n.endswith(".app") else f"{n}.app" for n in app_names_from_artifacts(cask)]
        app = find_app(root, wanted)

        if app is None:
            nested = find_nested_archive(root)
            if nested:
                inner, inner_kind = nested
                root = expand(inner, inner_kind, workdir / "nested", mounts)
                app = find_app(root, wanted)
        if app is None:
            # Deterministic outcome (e.g. suite/pkg of CLI binaries) — park it
            # rather than burning retries. --tokens bypasses parked entries.
            return "no_icon", "no .app found in expanded artifact"

        dest_png = output_dir / f"{token}.png"
        icon_reason = icns_to_png(app, dest_png)
        if icon_reason == "car_only":
            return "car_only", "asset-catalog-only app (no .icns)"
        if icon_reason:
            return "no_icon", icon_reason
        return "ok", str(dest_png)
    finally:
        for mnt in mounts:
            run(["hdiutil", "detach", str(mnt), "-force"])
        shutil.rmtree(workdir, ignore_errors=True)


def publish(token: str, png: Path) -> None:
    staged = png.parent / "AppIcon.png"
    shutil.copyfile(png, staged)
    tag = f"cask-{token}"
    create = run(["gh", "release", "create", tag, str(staged), "--repo", GITHUB_REPO,
                  "--prerelease", "--title", tag, "--notes", f"App icon for `{token}`."])
    if create.returncode != 0:
        if "already exists" in create.stderr:
            upload = run(["gh", "release", "upload", tag, str(staged), "--repo", GITHUB_REPO, "--clobber"])
            if upload.returncode != 0:
                raise ExtractError(f"gh release upload failed: {upload.stderr.strip()[:200]}")
        else:
            raise ExtractError(f"gh release create failed: {create.stderr.strip()[:200]}")
    staged.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def published_tokens() -> set[str]:
    proc = run(["gh", "api", "--paginate", f"repos/{GITHUB_REPO}/releases",
                "--jq", ".[].tag_name"])
    if proc.returncode != 0:
        raise SystemExit(f"gh api releases failed: {proc.stderr.strip()[:200]}")
    return {t[len("cask-"):] for t in proc.stdout.split() if t.startswith("cask-")}


def load_install_counts() -> dict[str, int]:
    """30-day install counts — backfill priority so the most-seen icons land
    first. The bulk cask.json has analytics=null; counts live in this endpoint.
    Best-effort: an empty map just means unordered selection."""
    try:
        with urlopen(ANALYTICS_API, timeout=60) as resp:
            data = json.loads(resp.read())
        return {
            row["cask"]: int(str(row["count"]).replace(",", ""))
            for row in data.get("items", [])
        }
    except Exception as e:
        print(f"warning: analytics fetch failed ({e}); selection unordered")
        return {}


def select_candidates(api_casks: list[dict], report: dict, limit: int) -> list[dict]:
    done = published_tokens()
    parked = {
        t for t, e in report.items()
        if e["status"] in ("no_icon", "car_only")
        or (e["status"] == "failed" and e.get("attempts", 0) >= MAX_ATTEMPTS)
    }
    candidates = [
        c for c in api_casks
        if c["token"] not in done and c["token"] not in parked and eligibility(c) is None
    ]
    counts = load_install_counts()
    candidates.sort(key=lambda c: counts.get(c["token"], 0), reverse=True)
    return candidates[:limit]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", nargs="*", help="Extract exactly these casks")
    parser.add_argument("--limit", type=int, default=50, help="Batch cap (default 50)")
    parser.add_argument("--publish", action="store_true",
                        help="Publish cask-<token> pre-releases (requires gh auth)")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "icons_out")
    parser.add_argument("--casks-json", type=Path,
                        help="Path to a pre-downloaded cask.json (skips the 30MB fetch)")
    args = parser.parse_args(argv)

    if args.casks_json:
        api_casks = json.loads(args.casks_json.read_text())
    else:
        print(f"Fetching {BREW_API} …")
        with urlopen(BREW_API, timeout=60) as resp:
            api_casks = json.loads(resp.read())
    by_token = {c["token"]: c for c in api_casks}

    report = load_report()
    if args.tokens:
        missing = [t for t in args.tokens if t not in by_token]
        if missing:
            raise SystemExit(f"unknown tokens: {missing}")
        batch = [by_token[t] for t in args.tokens]
    else:
        batch = select_candidates(api_casks, report, args.limit)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Extracting {len(batch)} casks → {args.output_dir}"
          + (" (publishing)" if args.publish else " (local only)"))

    ok = 0
    outcomes: list[tuple[str, str, str]] = []
    start = time.time()
    for i, cask in enumerate(batch, 1):
        token = cask["token"]
        try:
            status, detail = extract_one(cask, args.output_dir)
            if status == "ok":
                if args.publish:
                    publish(token, args.output_dir / f"{token}.png")
                    time.sleep(PUBLISH_THROTTLE)
                report.pop(token, None)  # clear any prior failure
                save_report(report)
                ok += 1
            else:
                record(report, token, status, detail)
        except ExtractError as e:
            status, detail = "failed", str(e)
            record(report, token, status, detail)
        except Exception as e:  # never let one cask kill the batch
            status, detail = "failed", f"{type(e).__name__}: {e}"
            record(report, token, status, detail)
        outcomes.append((token, status, detail))
        print(f"  [{i}/{len(batch)}] {token}: {status}"
              + (f" — {detail}" if status != "ok" else ""))

    elapsed = time.time() - start
    print(f"\n{ok}/{len(batch)} icons extracted in {elapsed:.0f}s; "
          f"{sum(1 for _, s, _ in outcomes if s == 'failed')} failed, "
          f"{sum(1 for _, s, _ in outcomes if s in ('no_icon', 'car_only'))} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
