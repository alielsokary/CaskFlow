#!/usr/bin/env python3
"""Read taps with brew, each tap's brew calls inside its own macOS sandbox. The only step that runs tap code."""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import signal
import subprocess  # nosec B404 - fixed git, brew and sandbox-exec commands, argument lists, and no shell
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import tap_registry
from tap_registry import OBJECT_ID, REF, tap_of

BREW_API = "https://formulae.brew.sh/api/cask.json"
_ARTIFACT_KEYS = ("app", "suite", "binary", "pkg")
_VERSIONISH = re.compile(r"[\d._-]+")


HOST_ENV = {"HOMEBREW_NO_AUTO_UPDATE": "1", "HOMEBREW_NO_ANALYTICS": "1", "HOMEBREW_NO_ENV_HINTS": "1",
            "GIT_TERMINAL_PROMPT": "0"}
# Tap code may read Homebrew and the system, write only its own folder, and reach no network.
# The real home holds the runner's files and the workspace, so it is unreadable.
PROFILE = """
(version 1)
(allow default)
(deny network*)
(deny file-read* (subpath (param "REAL_HOME")))
(deny file-write*)
(allow file-write*
  (subpath (param "WORK"))
  (literal "/dev/null") (literal "/dev/zero") (literal "/dev/dtracehelper")
  (regex #"^/dev/tty") (regex #"^/dev/fd/"))
"""
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
CALL_TIMEOUT = 180
_TRUST = threading.Lock()


def _run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Host commands that run no tap code: git clone, brew trust, cache warming."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900,  # nosec B603 - argument list; no shell
                          env={**os.environ, **HOST_ENV, **(env or {})})


@dataclass(frozen=True)
class TapUser:
    """A hidden account with no shell, home or sudo rights, so tap code cannot touch the runner user's files."""

    name: str
    uid: int


def create_tap_user(index: int, run=_run) -> TapUser:
    user = TapUser(f"_caskflowtap{index}", 7700 + index)
    record = f"/Users/{user.name}"
    for attribute in ([], ["UniqueID", str(user.uid)], ["PrimaryGroupID", "-2"], ["UserShell", "/usr/bin/false"],
                      ["NFSHomeDirectory", "/var/empty"], ["IsHidden", "1"]):
        if (result := run(["sudo", "-n", "dscl", ".", "-create", record, *attribute])).returncode != 0:
            raise RuntimeError(f"cannot create {user.name}: {result.stderr.strip()[-300:]}")
    return user


def clean_tap_user(user: TapUser, run=_run) -> bool:
    """Kill everything the user owns, including launchd jobs and agents; False if anything is left."""
    run(["sudo", "-n", "launchctl", "bootout", f"user/{user.uid}"])
    run(["sudo", "-n", "pkill", "-9", "-u", user.name])
    # pgrep exits 1 when the user owns no process.
    return run(["sudo", "-n", "pgrep", "-u", user.name]).returncode == 1


class TapSandbox:
    """brew calls for one tap: a private folder and home, no tokens, and no network.

    With a `user`, brew also runs as that account; whoever hands the user out cleans it after the tap.
    """

    def __init__(self, root: Path, cache: Path, user: TapUser | None = None, run=_run):
        root.mkdir(parents=True, exist_ok=True)
        self.user, self.run = user, run
        self.work = Path(tempfile.mkdtemp(dir=root)).resolve()
        self.real_home = Path(os.environ["HOME"]).resolve()
        home = self.work / "home"
        (home / ".homebrew").mkdir(parents=True)
        trust = self.real_home / ".homebrew" / "trust.json"
        if trust.exists():
            shutil.copy(trust, home / ".homebrew" / "trust.json")
        # Taps are cloned by the runner user; git refuses repositories another user owns otherwise.
        (home / ".gitconfig").write_text("[safe]\n\tdirectory = *\n", encoding="utf-8")
        # The warmed cache (API data brew cannot download here, and compiled Ruby), as a copy-on-write clone on macOS.
        if sys.platform == "darwin":
            subprocess.run(["cp", "-cR", str(cache), str(self.work / "cache")], check=True)  # nosec B603, B607 - no shell
        else:
            shutil.copytree(cache, self.work / "cache", symlinks=True)
        for sub in ("logs", "tmp"):
            (self.work / sub).mkdir()
        if user:
            # The tap user owns the folder; this user may only enter it, to start brew there.
            for handover in (["sudo", "-n", "chown", "-R", user.name, str(self.work)], ["sudo", "-n", "chmod", "711", str(self.work)]):
                if (result := run(handover)).returncode != 0:
                    raise RuntimeError(f"cannot hand the sandbox to {user.name}: {result.stderr.strip()[-300:]}")
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "en_US.UTF-8", **HOST_ENV,
            "HOMEBREW_CACHE": str(self.work / "cache"), "HOMEBREW_LOGS": str(self.work / "logs"),
            "HOMEBREW_TEMP": str(self.work / "tmp"), "TMPDIR": str(self.work / "tmp"),
            "HOMEBREW_API_AUTO_UPDATE_SECS": "86400",
            # tap-info would ask GitHub whether the tap is private, and wait out the blocked network.
            "HOMEBREW_NO_GITHUB_API": "1",
        }

    def command(self, cmd: list[str]) -> list[str]:
        sandboxed = ["sandbox-exec", "-p", PROFILE, "-D", f"WORK={self.work}", "-D", f"REAL_HOME={self.real_home}", *cmd]
        if not self.user:
            return sandboxed
        return ["sudo", "-n", "-u", self.user.name, "/usr/bin/env", "-i",
                *(f"{key}={value}" for key, value in self.env.items()), *sandboxed]

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess:
        # Unnamed files the sandbox cannot reach: a process the tap leaves behind cannot hold the call
        # open, and tap code cannot swap what the host reads back.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(  # nosec B603 - argument list; no shell
                self.command(cmd), stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=self.env,
                start_new_session=True, cwd=self.work)  # brew.sh's bash writes here-documents to its working directory
            leftovers = False
            try:
                code = process.wait(timeout=CALL_TIMEOUT)
            except subprocess.TimeoutExpired:
                code, leftovers = -9, True
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    leftovers = True  # only another account's processes are left in the group
                if leftovers and self.user:
                    self.run(["sudo", "-n", "pkill", "-9", "-u", self.user.name])
            stdout = _read_capped(out)
            if stdout is None:
                return subprocess.CompletedProcess(cmd, 1, "", "output too large")
            return subprocess.CompletedProcess(cmd, code, stdout, _read_capped(err) or "")


def _read_capped(handle) -> str | None:
    handle.seek(0)
    data = handle.read(MAX_OUTPUT_BYTES + 1)
    if len(data) > MAX_OUTPUT_BYTES:
        return None
    return data.decode("utf-8", errors="replace")


def warm_cache(cache: Path) -> None:
    """Homebrew's API data and compiled Ruby, fetched once on the host before any tap code runs."""
    for cmd in (["brew", "info", "--json=v2", "wget"], ["brew", "info", "--cask", "--json=v2", "firefox"]):
        _run(cmd, env={"HOMEBREW_CACHE": str(cache)})


@functools.cache
def _taps_root() -> Path:
    return Path(_run(["brew", "--repository"]).stdout.strip()) / "Library" / "Taps"


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


def read_tap(entry: dict, sandbox, *, run=_run, taps_root: Path | None = None, deadline: float | None = None,
             clock=time.monotonic) -> dict:
    """One tap's casks: cloned and trusted on the host, then read with brew inside a fresh `sandbox()`."""
    name = entry["name"]
    if deadline is not None and clock() >= deadline:
        return {"unreached": [name]}
    try:
        user, repo = name.split("/")
        target = (taps_root or _taps_root()) / user / f"homebrew-{repo}"
        if not target.exists():
            # git runs no code from the repository it clones.
            clone = run(["git", "clone", "--quiet", "--depth", "1", tap_registry.remote_of(entry), str(target)])
            if clone.returncode != 0:
                raise RuntimeError(f"git clone failed: {clone.stderr.strip()[-300:]}")
        with _TRUST:
            if (trust := run(["brew", "trust", "--tap", name])).returncode != 0:
                raise RuntimeError(f"brew trust failed: {trust.stderr.strip()[-300:]}")
        brew = sandbox()
        info = brew(["brew", "tap-info", "--json", name])
        if info.returncode != 0:
            raise RuntimeError(f"tap-info failed: {info.stderr.strip()[-300:]}")
        casks, failed = _info(cask_refs(json.loads(info.stdout)), brew, deadline, clock)
        return {"casks": casks, "caskFailures": failed}
    except (RuntimeError, ValueError, TypeError, AttributeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"warning: skipping {name}: {error}")
        return {"casks": {}, "failures": {name: str(error)}}


def load_official() -> dict[str, dict]:
    with urlopen(BREW_API, timeout=120) as response:  # nosec B310 - fixed HTTPS Homebrew API
        return {cask["token"]: cask for cask in json.loads(response.read())}
