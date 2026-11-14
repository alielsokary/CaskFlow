#!/usr/bin/env python3
"""Read registered taps with brew. The only tap-pipeline step that runs tap code: CI gives it no secrets."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 - fixed brew commands, argument lists, and no shell
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import tap_registry
from tap_registry import OBJECT_ID, REF, tap_of
from style_standards import write_json

BREW_API = "https://formulae.brew.sh/api/cask.json"
_ARTIFACT_KEYS = ("app", "suite", "binary", "pkg")
_VERSIONISH = re.compile(r"[\d._-]+")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)  # nosec B603 - argument list; shell is never used


def cask_refs(tap_info: list[dict]) -> list[str]:
    return [ref for tap in tap_info for ref in tap.get("cask_tokens", [])]


def by_ref(info_json: object) -> dict[str, dict]:
    casks = info_json.get("casks") if isinstance(info_json, dict) else None
    if not isinstance(casks, list) or not all(isinstance(c, dict) and isinstance(c.get("full_token"), str) for c in casks):
        raise ValueError("unexpected brew info output")
    return {cask["full_token"]: cask for cask in casks}


def _artifact_names(cask: dict) -> set[str]:
    names = set()
    for artifact in cask.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for key in _ARTIFACT_KEYS:
            for entry in artifact.get(key, []):
                if isinstance(entry, str):
                    names.add(_VERSIONISH.sub("", Path(entry).name.lower()))
    return names


# Hosts whose first path segments name the project; a bare host would match unrelated apps.
_FORGES = frozenset({"github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "gitee.com", "git.sr.ht"})
_BUCKET_HOSTS = frozenset({"storage.googleapis.com", "s3.amazonaws.com"})
_SOURCEFORGE = frozenset({"downloads.sourceforge.net", "sourceforge.net"})
# Shared file hosts say nothing about which app a file belongs to.
# Regional S3 endpoints, Backblaze B2 (/file/<bucket>) and DevMate (/<bundle id>) name their owner in the path.
_S3_REGIONAL = re.compile(r"s3[.-][a-z0-9-]+\.amazonaws\.com")
_B2_HOST = re.compile(r"f\d+\.backblazeb2\.com")
_SHARED_HOSTS = frozenset({
    "dl.dropboxusercontent.com", "www.dropbox.com", "dropbox.com", "drive.google.com", "dl.google.com",
    "download.microsoft.com", "objects.githubusercontent.com",
})


def _homepage(cask: dict) -> str | None:
    parsed = urlparse(cask.get("homepage") or "")
    host = parsed.netloc.lower().removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/').lower()}" if host else None


def _canonical(host: str, parts: list[str]) -> tuple[str, list[str]]:
    """One host name per file service, with the path segments that follow its fixed prefix."""
    if host in _SOURCEFORGE:
        return "sourceforge.net", parts[1:] if parts[:1] in (["project"], ["projects"]) else []
    if _B2_HOST.fullmatch(host):
        return "backblazeb2.com", parts[1:] if parts[:1] == ["file"] else []
    return host, parts


def _owner_segments(host: str) -> int:
    """How many leading path segments name the project on `host` (0: the host itself does)."""
    if host in _FORGES:
        return 2
    one = host in _BUCKET_HOSTS or host in ("dl.devmate.com", "sourceforge.net", "backblazeb2.com")
    return 1 if one or _S3_REGIONAL.fullmatch(host) else 0


def _download_source(cask: dict) -> str | None:
    parsed = urlparse(cask.get("url") or "")
    host, parts = _canonical(parsed.netloc.lower(), [part for part in parsed.path.lower().split("/") if part])
    if not host or host in _SHARED_HOSTS:
        return None
    owner = parts[:_owner_segments(host)]
    return "/".join([host, *owner]) if len(owner) == _owner_segments(host) else None


def _known_equal(left: str | None, right: str | None) -> bool:
    return left is not None and left == right


def same_app(tap_cask: dict, official: dict) -> bool:
    """Same app only if it installs the same files AND shares a known homepage or download source."""
    if not _artifact_names(tap_cask) & _artifact_names(official):
        return False
    return (_known_equal(_homepage(tap_cask), _homepage(official))
            or _known_equal(_download_source(tap_cask), _download_source(official)))


def aliases(casks: dict[str, dict], official: dict[str, dict]) -> dict[str, str]:
    return {
        ref: cask["token"] for ref, cask in casks.items()
        if cask["token"] in official and same_app(cask, official[cask["token"]])
    }


def trusted_view(collected: dict, taps: list[str], official: dict[str, dict]) -> dict:
    """What privileged jobs may use from collect's output, which tap code could have shaped."""
    # Keeps only well-formed entries of the requested taps whose names agree, and recomputes
    # aliases from the official API instead of trusting the file.
    wanted = set(taps)

    def ours(ref: object) -> bool:
        return isinstance(ref, str) and bool(REF.fullmatch(ref)) and tap_of(ref) in wanted

    def files(refs: dict, tap: str | None = None) -> dict[str, str]:
        return {ref: oid for ref, oid in sorted(refs.items(), key=lambda item: str(item[0]))
                if ours(ref) and (tap is None or tap_of(ref) == tap)
                and isinstance(oid, str) and OBJECT_ID.fullmatch(oid)}

    def mapping(key: str) -> dict:
        value = collected.get(key) if isinstance(collected, dict) else None
        return value if isinstance(value, dict) else {}

    casks = {
        ref: cask for ref, cask in mapping("casks").items()
        if isinstance(cask, dict) and ours(ref)
        and cask.get("full_token") == ref and cask.get("token") == ref.rsplit("/", 1)[1]
    }
    return {
        "casks": dict(sorted(casks.items())),
        "sameAs": aliases(casks, official),
        "caskFailures": {ref: "" for ref in sorted(filter(ours, mapping("caskFailures")))},
        "listed": {tap: files(refs, tap) for tap, refs in sorted(mapping("listed").items(), key=lambda item: str(item[0]))
                   if tap in wanted and isinstance(refs, dict)},
        "processed": files(mapping("processed")),
    }


def _info(refs: list[str], run, deadline: float | None = None, clock=time.monotonic) -> tuple[dict[str, dict], dict[str, str]]:
    """Read casks with brew info, halving around broken ones: one broken cask fails the whole call."""
    if not refs:
        return {}, {}
    if deadline is not None and clock() >= deadline:
        return {}, {ref: "out of time" for ref in refs}
    result = run(["brew", "info", "--cask", "--json=v2", *refs])
    if result.returncode == 0:
        return by_ref(json.loads(result.stdout)), {}
    if len(refs) == 1:
        return {}, {refs[0]: result.stderr.strip()[-300:]}
    middle = len(refs) // 2
    left, left_failed = _info(refs[:middle], run, deadline, clock)
    right, right_failed = _info(refs[middle:], run, deadline, clock)
    return {**left, **right}, {**left_failed, **right_failed}


def _read_tap(entry: dict, run, deadline: float | None = None) -> tuple[dict[str, dict], dict[str, str]]:
    name = entry["name"]
    for cmd in (
        ["brew", "tap", name, *([entry["remote"]] if entry.get("remote") else [])],
        ["brew", "trust", name],
    ):
        if (result := run(cmd)).returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:2])} failed: {result.stderr.strip()[-300:]}")
    info = run(["brew", "tap-info", "--json", name])
    if info.returncode != 0:
        raise RuntimeError(f"tap-info failed: {info.stderr.strip()[-300:]}")
    refs = cask_refs(json.loads(info.stdout))
    return _info(refs, run, deadline)


def collect(entries: list[dict], run, official: dict[str, dict], *, budget: float | None = None,
            clock=time.monotonic, on_progress=lambda result: None) -> dict:
    """Read taps until `budget` seconds pass; taps left over are `unreached` and wait for the next run."""
    casks: dict[str, dict] = {}
    failures: dict[str, str] = {}
    cask_failures: dict[str, str] = {}
    start = clock()

    def result(unreached: list[dict]) -> dict:
        return {"casks": dict(sorted(casks.items())), "sameAs": aliases(casks, official), "failures": failures,
                "caskFailures": dict(sorted(cask_failures.items())),
                "unreached": [entry["name"] for entry in unreached]}

    for index, entry in enumerate(entries):
        if budget is not None and clock() - start >= budget:
            print(f"warning: budget spent; {len(entries) - index} taps wait for the next run")
            return result(entries[index:])
        try:
            read, failed = _read_tap(entry, run, None if budget is None else start + budget)
            casks.update(read)
            cask_failures.update(failed)
        except (RuntimeError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as error:
            failures[entry["name"]] = str(error)
            print(f"warning: skipping {entry['name']}: {error}")
        on_progress(result([]))
    return result([])


def load_official() -> dict[str, dict]:
    with urlopen(BREW_API, timeout=120) as response:  # nosec B310 - fixed HTTPS Homebrew API
        return {cask["token"]: cask for cask in json.loads(response.read())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taps", nargs="+", required=True, help="Tap names to read")
    parser.add_argument("--out", type=Path, default=Path("tap_casks.json"))
    parser.add_argument("--budget-minutes", type=float, help="Stop starting new taps after this long")
    args = parser.parse_args(argv)

    registered = {entry["name"]: entry for entry in tap_registry.load()}
    # A dispatched or issue-requested tap is registered only by the publish job, after this run.
    if invalid := [error for name in args.taps if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))
    official = load_official()
    entries = [registered.get(name, {"name": name}) for name in args.taps]

    def save(result: dict) -> None:
        write_json(args.out, result, trailing_newline=True)

    budget = args.budget_minutes * 60 if args.budget_minutes else None
    save(collect(entries, _run, official, budget=budget, on_progress=save))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
