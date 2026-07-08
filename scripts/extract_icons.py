#!/usr/bin/env python3
"""
Icon extraction pipeline — see docs/superpowers/specs/2026-07-05-icon-extraction-design.md.

Per cask: download the vendor artifact, expand it WITHOUT executing anything,
locate the .app bundle, convert its .icns to a 256px PNG, and (optionally)
publish it as `<token>.png` on the orphan `icons` branch. CaskHub consumes
icons via jsDelivr's edge CDN (with raw.githubusercontent.com as fallback):
    https://cdn.jsdelivr.net/gh/alielsokary/CaskKit@icons/<token>.png

State — single writer, single home:
- "Done" = the .png files on the icons branch (one `git ls-tree`).
- icon_report.json lives ON THE ICONS BRANCH, committed in the same pushes
  as the icons. Master carries no copy and no rolling PR exists — two prior
  report-clobber races came from report state living in multiple places.
  Manual audits edit the file on the icons branch directly.
- Report entries: no_icon / car_only (parked), failed (MAX_ATTEMPTS tries,
  then parked), review (published but heuristically selected — audit queue).

Run:
    python scripts/extract_icons.py --tokens obsidian rectangle   # local, no publish
    python scripts/extract_icons.py --publish --limit 50          # CI / backfill batch
"""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
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
REPORT_FILE = "icon_report.json"  # lives on the icons branch, beside the PNGs
BREW_API = "https://formulae.brew.sh/api/cask.json"
ANALYTICS_API = "https://formulae.brew.sh/api/analytics/cask-install/30d.json"
ICONS_BRANCH = "icons"
ICON_SIZE = "256"
MAX_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = 600  # seconds; some vendor artifacts are multi-GB
FLUSH_EVERY = 25  # icons per publish commit — bounds loss if a long CI run dies

TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz")

# Some vendors 404 curl's default UA on GET (Warp) — mimic what Homebrew
# sends, since every cask URL is served to Homebrew by definition.
DEFAULT_UA = "Homebrew/4.5.0 (Macintosh; arm64 Mac OS X 15) curl/8.7.1"


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


def sniff_container(artifact: Path) -> str | None:
    """Magic-byte fallback when the URL has no useful extension — modern
    vendors serve …/stable (VS Code), …/osx_arm64 (Postman), …/download
    (Raycast). Runs on the already-downloaded file, so it's authoritative."""
    try:
        size = artifact.stat().st_size
        with artifact.open("rb") as f:
            head = f.read(512)
            f.seek(max(0, size - 512))
            tail = f.read(512)
    except OSError:
        return None
    # Trailer first: UDIF images carry their block-compression's magic at
    # byte 0 (Warp: zlib, Comet: xz, HandBrake: bzip2) — head bytes lie.
    if tail.startswith(b"koly"):  # UDIF trailer: the last 512 bytes of a DMG
        return "dmg"
    if head.startswith(b"PK"):
        return "zip"
    if head.startswith(b"xar!"):
        return "pkg"
    if head.startswith((b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00")):
        return "tar"  # tar -xf auto-detects the compression
    if head[257:262] == b"ustar":
        return "tar"
    return None


_INSTALLERISH = re.compile(r"\b(install(er)?|uninstall(er)?|updater?|setup)\b", re.IGNORECASE)


def installerish(app_name: str) -> bool:
    """True for installer/updater stub apps — never the icon we want.
    Batch 1 shipped the Microsoft installer icon for Word/Excel/PowerPoint/
    Outlook because the pkg payload's stub app was the shallowest .app."""
    return bool(_INSTALLERISH.search(app_name.removesuffix(".app")))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def token_matches_app(token: str, app_name: str) -> bool:
    """`microsoft-word` ↔ `Microsoft Word.app`. Containment needs ≥4 chars to
    avoid one-letter apps (R.app) matching everything."""
    nt, na = _norm(token), _norm(app_name.removesuffix(".app"))
    if not nt or not na:
        return False
    if nt == na:
        return True
    return (len(na) >= 4 and na in nt) or (len(nt) >= 4 and nt in na)


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
    """Read icon_report.json from the icons branch — its only home."""
    if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
        raise SystemExit(f"git fetch origin {ICONS_BRANCH} failed — branch missing?")
    show = _git(REPO_ROOT, "show", f"FETCH_HEAD:{REPORT_FILE}")
    if show.returncode != 0:
        return {}  # first run after migration
    return json.loads(show.stdout)


def report_json(report: dict[str, dict]) -> str:
    return json.dumps(dict(sorted(report.items())), indent=2, ensure_ascii=False) + "\n"


def record(report: dict, token: str, status: str, reason: str) -> None:
    prev = report.get(token, {})
    report[token] = {
        "status": status,
        "reason": reason,
        "attempts": prev.get("attempts", 0) + (1 if status == "failed" else 0),
        "updated": str(date.today()),
    }


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], input_text: str | None = None, **kw) -> subprocess.CompletedProcess:
    if input_text is None:
        kw["stdin"] = subprocess.DEVNULL
    return subprocess.run(cmd, capture_output=True, text=True, input=input_text, **kw)


def download(cask: dict, dest_dir: Path) -> Path:
    url = cask["url"]
    filename = Path(urlparse(url).path).name or "artifact"
    dest = dest_dir / filename
    cmd = ["curl", "-fSL", "--retry", "2", "--max-time", str(DOWNLOAD_TIMEOUT), "-o", str(dest)]
    specs = cask.get("url_specs") or {}
    cmd += ["-A", str(specs.get("user_agent") or DEFAULT_UA)]
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
        # input "Y": auto-accept EULA prompts (24 of batch 3+4's 34 failures
        # were "attach canceled" license DMGs). Read-only mount, nothing runs.
        proc = run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify",
                    "-mountpoint", str(mnt), str(artifact)], input_text="Y\n")
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
        if proc.returncode == 0:
            return out
        # Not a tarball — a bare gzip/bzip2/xz-wrapped payload (comet ships
        # an xz-compressed DMG). Decompress, sniff the inner file, re-expand.
        with artifact.open("rb") as f:
            head = f.read(8)
        tool = next((t for magic, t in ((b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"),
                                        (b"\xfd7zXZ\x00", "xz")) if head.startswith(magic)), None)
        if tool is None:
            raise ExtractError(f"tar failed: {proc.stderr.strip()[:200]}")
        inner = workdir / f"{artifact.name}.inner"
        with inner.open("wb") as fh:
            dec = subprocess.run([tool, "-dc", str(artifact)], stdout=fh,
                                 stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        if dec.returncode != 0:
            raise ExtractError(f"{tool} -dc failed: {dec.stderr.decode()[:200]}")
        inner_kind = sniff_container(inner)
        if inner_kind in (None, "tar"):  # tar guard: no infinite recursion
            raise ExtractError(f"unrecognized payload inside {tool} stream")
        return expand(inner, inner_kind, workdir / "inner", mounts)
    if kind == "pkg":
        pkg_out = workdir / "pkg"
        proc = run(["pkgutil", "--expand-full", str(artifact), str(pkg_out)])
        if proc.returncode != 0:
            raise ExtractError(f"pkgutil failed: {proc.stderr.strip()[:200]}")
        return pkg_out
    raise ExtractError(f"unknown container type for {artifact.name}")


def find_app(root: Path, wanted: list[str], token: str = "") -> tuple[Path, str] | None:
    """Locate the .app bundle; never follows symlinks (DMGs ship an
    /Applications symlink).

    Returns (app, selection) where selection is the audit signal:
    - "exact":          name matches the cask's artifact stanzas — near-certain
    - "token":          name matches the cask token — near-certain
    - "single":         only one non-installer .app — unambiguous
    - "shallowest":     multiple candidates, none matching — human review
    - "installer_only": every .app is an installer/updater stub — caller
                        parks the cask instead of shipping a wrong icon
    """
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
                    return entry, "exact"
                found.append(entry)
            else:
                stack.append((entry, depth + 1))
    if not found:
        return None

    candidates = [a for a in found if not installerish(a.name)]
    if not candidates:
        return found[0], "installer_only"

    by_token = [a for a in candidates if token_matches_app(token, a.name)]
    if by_token:
        # Exact normalized equality beats containment; then prefer shorter
        # names ("Docker.app" over "Docker Helper.app").
        nt = _norm(token)
        by_token.sort(key=lambda a: (_norm(a.name.removesuffix(".app")) != nt, len(a.name)))
        return by_token[0], "token"

    if len(candidates) == 1:
        return candidates[0], "single"
    return sorted(candidates, key=lambda p: len(p.parts))[0], "shallowest"


def find_nested_archive(root: Path) -> tuple[Path, str] | None:
    """One level of dmg-in-zip style nesting (guided search, spec §Extraction 3)."""
    for pattern, kind in (("*.dmg", "dmg"), ("*.pkg", "pkg"), ("*.zip", "zip")):
        # is_file: an expanded component package is a *directory* named *.pkg
        hits = [p for p in root.rglob(pattern) if p.is_file() and not p.is_symlink()]
        if hits:
            return hits[0], kind
    return None


# argv: <appPath|--generic> <destPng> <size>. --generic renders the system
# generic app icon — the reference image for the wrong-icon guard below.
_CAR_ICON_SWIFT = """\
import AppKit
import UniformTypeIdentifiers
let args = CommandLine.arguments
let size = Int(args[3]) ?? 256
let icon = args[1] == "--generic"
    ? NSWorkspace.shared.icon(for: UTType.applicationBundle)
    : NSWorkspace.shared.icon(forFile: args[1])
guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
icon.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
try png.write(to: URL(fileURLWithPath: args[2]))
"""


def car_icon_to_png(app: Path, dest_png: Path) -> str | None:
    """Asset-catalog-only app (icon in Assets.car, no loose .icns): render via
    Icon Services — no public CLI decodes Assets.car. Reasons start with
    "car" so extract_one parks them under the car_only status.

    Wrong-icon guard: a render byte-identical to the generic app icon means
    the catalog had no real icon; park instead of shipping it."""
    with tempfile.TemporaryDirectory(prefix="car-icon-") as td:
        script = Path(td) / "icon.swift"
        script.write_text(_CAR_ICON_SWIFT)
        generic = Path(td) / "generic.png"
        for target, out in ((str(app), dest_png), ("--generic", generic)):
            proc = run(["swift", str(script), target, str(out), ICON_SIZE])
            if proc.returncode != 0 or not out.exists():
                return f"car render failed: {proc.stderr.strip()[:120]}"
        if dest_png.read_bytes() == generic.read_bytes():
            dest_png.unlink()
            return "car catalog has no app icon (generic render)"
    return None


def icns_to_png(app: Path, dest_png: Path) -> str | None:
    """Convert the app's icon to PNG. Returns a no-icon/car reason or None."""
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
        candidates = sorted(resources.glob("*.icns")) if resources.exists() else []
        if candidates:
            icns = max(candidates, key=lambda p: p.stat().st_size)
        elif (resources / "Assets.car").exists():
            # Keyed off the actual file, not CFBundleIconName — Tailscale
            # ships a car-only icon without declaring it in the plist.
            return car_icon_to_png(app, dest_png)
        else:
            return "no .icns in Resources"

    proc = run(["sips", "-s", "format", "png", "--resampleHeightWidthMax", ICON_SIZE,
                str(icns), "--out", str(dest_png)])
    if proc.returncode != 0 or not dest_png.exists():
        raise ExtractError(f"sips failed: {proc.stderr.strip()[:200]}")
    return None


# ---------------------------------------------------------------------------
# Per-cask pipeline
# ---------------------------------------------------------------------------

def extract_one(cask: dict, output_dir: Path) -> tuple[str, str]:
    """Returns (status, detail): ok (detail = .app selection mode) | no_icon |
    car_only. Raises ExtractError for `failed`."""
    token = cask["token"]
    reason = eligibility(cask)
    if reason:
        return "no_icon", reason

    workdir = Path(tempfile.mkdtemp(prefix=f"icon-{token}-"))
    mounts: list[Path] = []
    try:
        artifact = download(cask, workdir)
        kind = container_type(cask, artifact.name) or sniff_container(artifact)
        if kind is None:
            return "no_icon", f"unsupported container: {artifact.name}"

        root = expand(artifact, kind, workdir, mounts)
        wanted = [n if n.endswith(".app") else f"{n}.app" for n in app_names_from_artifacts(cask)]
        hit = find_app(root, wanted, token)

        if hit is None:
            nested = find_nested_archive(root)
            if nested:
                inner, inner_kind = nested
                root = expand(inner, inner_kind, workdir / "nested", mounts)
                hit = find_app(root, wanted, token)
        if hit is None:
            # Deterministic outcome (e.g. suite/pkg of CLI binaries) — park it
            # rather than burning retries. --tokens bypasses parked entries.
            return "no_icon", "no .app found in expanded artifact"
        app, selection = hit
        if selection == "installer_only":
            # An installer stub's icon is a wrong icon, not an icon. Honest gap.
            return "no_icon", "only installer/updater apps in artifact"

        dest_png = output_dir / f"{token}.png"
        icon_reason = icns_to_png(app, dest_png)
        if icon_reason:
            if icon_reason.startswith("car"):
                return "car_only", icon_reason
            return "no_icon", icon_reason
        return "ok", selection
    finally:
        for mnt in mounts:
            run(["hdiutil", "detach", str(mnt), "-force"])
        shutil.rmtree(workdir, ignore_errors=True)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(cwd), *args])


def publish_batch(pngs: dict[str, Path], report: dict[str, dict],
                  dirty: set[str]) -> None:
    """Commit a batch of icons AND the report to the icons branch via a
    throwaway worktree — icons and their state always land in the same push.

    The report is MERGED, not overwritten: the branch's current copy is the
    base and only `dirty` tokens (touched by this run) are overlaid. A batch
    holds its report in memory for an hour+; blind writes clobbered a manual
    audit committed mid-run (third report race, 2026-07-07)."""
    wt = Path(tempfile.mkdtemp(prefix="icons-wt-"))
    wt_added = False
    try:
        if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
            raise ExtractError(f"git fetch origin {ICONS_BRANCH} failed — branch missing?")
        add = _git(REPO_ROOT, "worktree", "add", "--detach", str(wt), "FETCH_HEAD")
        if add.returncode != 0:
            raise ExtractError(f"worktree add failed: {add.stderr.strip()[:200]}")
        wt_added = True
        for token, png in pngs.items():
            shutil.copyfile(png, wt / f"{token}.png")
        base_file = wt / REPORT_FILE
        merged: dict[str, dict] = (
            json.loads(base_file.read_text()) if base_file.exists() else {}
        )
        for token in dirty:
            if token in report:
                merged[token] = report[token]
            else:
                merged.pop(token, None)  # cleared by this run (success)
        base_file.write_text(report_json(merged))
        _git(wt, "add", "-A")
        if _git(wt, "diff", "--cached", "--quiet").returncode == 0:
            return  # everything already on the branch
        # CI runners have no git identity configured; fall back to the bot's.
        name = _git(wt, "config", "user.name").stdout.strip() or "github-actions[bot]"
        email = (_git(wt, "config", "user.email").stdout.strip()
                 or "41898282+github-actions[bot]@users.noreply.github.com")
        msg = f"Add {len(pngs)} icons" if pngs else "Update icon report"
        commit = _git(wt, "-c", f"user.name={name}", "-c", f"user.email={email}",
                      "commit", "-q", "-m", msg)
        if commit.returncode != 0:
            raise ExtractError(f"icon commit failed: {commit.stderr.strip()[:200]}")
        push = _git(wt, "push", "-q", "origin", f"HEAD:{ICONS_BRANCH}")
        if push.returncode != 0:
            raise ExtractError(f"icon push failed: {push.stderr.strip()[:200]}")
        print(f"  ↑ published {len(pngs)} icons to {ICONS_BRANCH}")
    finally:
        if wt_added:
            _git(REPO_ROOT, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def published_tokens() -> set[str]:
    """Tokens already on the icons branch — the done-list. One tree listing,
    no pagination."""
    if _git(REPO_ROOT, "fetch", "-q", "origin", ICONS_BRANCH).returncode != 0:
        raise SystemExit(f"git fetch origin {ICONS_BRANCH} failed — branch missing?")
    ls = _git(REPO_ROOT, "ls-tree", "-r", "--name-only", "FETCH_HEAD")
    if ls.returncode != 0:
        raise SystemExit(f"git ls-tree failed: {ls.stderr.strip()[:200]}")
    return {Path(n).stem for n in ls.stdout.split() if n.endswith(".png")}


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
    from classify_new_casks import is_main_cask  # shared "what the app shows" filter

    done = published_tokens()
    parked = {
        t for t, e in report.items()
        if e["status"] in ("no_icon", "car_only")
        or (e["status"] == "failed" and e.get("attempts", 0) >= MAX_ATTEMPTS)
    }
    candidates = [
        c for c in api_casks
        # is_main_cask: skip @-version variants and font-* — the app filters
        # them out, so their icons would never be requested (--tokens still
        # bypasses this for manual runs)
        if is_main_cask(c)
        and c["token"] not in done and c["token"] not in parked
        and eligibility(c) is None
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
    pending: dict[str, Path] = {}
    dirty: set[str] = set()  # tokens whose report entry this run may change
    start = time.time()
    for i, cask in enumerate(batch, 1):
        token = cask["token"]
        dirty.add(token)
        try:
            status, detail = extract_one(cask, args.output_dir)
            if status == "ok":
                report.pop(token, None)  # clear any prior failure/review
                if detail in ("single", "shallowest"):
                    # Audit queue: the .app was picked heuristically, not by
                    # stanza name or token match — a human should eyeball it.
                    record(report, token, "review", f"non-exact .app selection: {detail}")
                ok += 1
                if args.publish:
                    pending[token] = args.output_dir / f"{token}.png"
                    if len(pending) >= FLUSH_EVERY:
                        publish_batch(pending, report, dirty)
                        pending.clear()
            else:
                record(report, token, status, detail)
        except ExtractError as e:
            status, detail = "failed", str(e)
            record(report, token, status, detail)
        except Exception as e:  # never let one cask kill the batch
            status, detail = "failed", f"{type(e).__name__}: {e}"
            record(report, token, status, detail)
        outcomes.append((token, status, detail))
        print(f"  [{i}/{len(batch)}] {token}: {status} — {detail}", flush=True)

    if args.publish:
        # Always flush at the end — persists report-only outcomes (failures,
        # parks) even when no new icons were extracted.
        publish_batch(pending, report, dirty)
    else:
        print("(local run — report changes not persisted; use --publish)")

    elapsed = time.time() - start
    print(f"\n{ok}/{len(batch)} icons extracted in {elapsed:.0f}s; "
          f"{sum(1 for _, s, _ in outcomes if s == 'failed')} failed, "
          f"{sum(1 for _, s, _ in outcomes if s in ('no_icon', 'car_only'))} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
