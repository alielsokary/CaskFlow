#!/usr/bin/env python3
"""Collect tap casks in separate workflow runs, a few taps each.

Tap code runs only inside `tap-collect.yml` runs. Each run's token reaches only its own
artifacts, so a hostile tap can shape data for the taps in its own batch and nothing else:
this job keeps from each run only the casks of the taps that run was given.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import tap_registry
from collect_tap_casks import _REF, aliases, load_official, trusted_view
from style_standards import write_json
from validate_taps import _run as _git_run

WORKFLOW = "tap-collect.yml"
TITLE = "Tap collect "

Gh = Callable[[list[str]], str]


def list_casks(entry: dict, run=_git_run, workdir: Path | None = None) -> list[str] | None:
    """The tap's cask refs from its repository's file names (blob-less clone, no tap code run); None if unknown."""
    try:
        with tempfile.TemporaryDirectory(dir=workdir) as clone:
            cloned = run(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout",
                          tap_registry.remote_of(entry), clone])
            if cloned.returncode != 0:
                return None
            listing = run(["git", "ls-tree", "-r", "--name-only", "HEAD", "Casks"], cwd=clone)
    except (subprocess.SubprocessError, OSError):
        return None
    if listing.returncode != 0:
        return None
    refs = sorted({f"{entry['name']}/{Path(path).stem}" for path in listing.stdout.splitlines() if path.endswith(".rb")})
    # An empty listing more likely means a restructured repo than a tap that removed every cask.
    return [ref for ref in refs if _REF.fullmatch(ref)] or None


def batches(taps: list[str], size: int) -> list[list[str]]:
    return [taps[i:i + size] for i in range(0, len(taps), size)]


def _find_runs(gh: Gh, nonces: set[str], sleep, attempts: int = 30) -> dict[str, int]:
    found: dict[str, int] = {}
    for _ in range(attempts):
        listed = json.loads(gh(["run", "list", "--workflow", WORKFLOW, "--event", "workflow_dispatch",
                                "--limit", "500", "--json", "databaseId,displayTitle"]))
        for run in listed:
            nonce = run["displayTitle"].removeprefix(TITLE)
            if nonce in nonces:
                found.setdefault(nonce, run["databaseId"])
        if len(found) == len(nonces):
            break
        sleep(10)
    return found


def _finished(gh: Gh, run_id: int, sleep, clock, deadline: float) -> bool:
    while True:
        if json.loads(gh(["run", "view", str(run_id), "--json", "status"]))["status"] == "completed":
            return True
        if clock() >= deadline:
            return False
        sleep(20)


def _download(gh: Gh, run_id: int, workdir: Path) -> dict | None:
    folder = workdir / str(run_id)
    try:
        gh(["run", "download", str(run_id), "-n", "tap-casks", "-D", str(folder)])
        data = json.loads((folder / "tap_casks.json").read_text(encoding="utf-8"))
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("casks"), dict) else None


def collect(taps: list[str], gh: Gh, ref: str, official: dict[str, dict], *, batch_size: int, budget: float,
            workdir: Path, sleep=time.sleep, clock=time.monotonic, on_progress=lambda result: None,
            lister: Callable[[str], list[str] | None] = lambda name: None) -> dict:
    groups = {uuid.uuid4().hex: group for group in batches(taps, batch_size)}
    for nonce, group in groups.items():
        gh(["workflow", "run", WORKFLOW, "--ref", ref, "-f", f"taps={' '.join(group)}", "-f", f"nonce={nonce}"])
    runs = _find_runs(gh, set(groups), sleep)
    deadline = clock() + budget
    casks: dict[str, dict] = {}
    failures: dict[str, str] = {}
    cask_failures: dict[str, str] = {}
    unreached: list[str] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        listed = {tap: refs for tap, refs in zip(taps, pool.map(lister, taps)) if refs}

    def result() -> dict:
        return {"casks": dict(sorted(casks.items())), "sameAs": aliases(casks, official), "failures": failures,
                "caskFailures": dict(sorted(cask_failures.items())),
                "listed": dict(sorted(listed.items())), "unreached": unreached}

    for nonce, group in groups.items():
        run_id = runs.get(nonce)
        data = None
        if run_id is not None:
            if _finished(gh, run_id, sleep, clock, deadline):
                data = _download(gh, run_id, workdir)
            else:
                gh(["run", "cancel", str(run_id)])
        if data is None:
            unreached.extend(group)
        else:
            try:
                view = trusted_view(data, group, official)
            except (TypeError, AttributeError, ValueError):
                unreached.extend(group)
                on_progress(result())
                continue
            casks.update(view["casks"])
            cask_failures.update(view["caskFailures"])
            reported = data.get("failures")
            failures.update({name: str(reason) for name, reason in (reported if isinstance(reported, dict) else {}).items()
                             if name in group})
            left = data.get("unreached")
            unreached.extend(name for name in (left if isinstance(left, list) else []) if name in group)
        on_progress(result())
    return result()


def summary(result: dict) -> str:
    """Names only, each re-validated: collected text came from tap code."""
    def names(values: object, valid) -> list[str]:
        return sorted(value for value in (values or []) if isinstance(value, str) and valid(value))

    def is_tap(name: str) -> bool:
        return tap_registry.validate_name(name) is None

    sections = (
        ("Taps that failed", names(result.get("failures"), is_tap)),
        ("Casks that failed to load", names(result.get("caskFailures"), lambda ref: bool(_REF.fullmatch(ref)))),
        ("Taps left for the next run", names(result.get("unreached"), is_tap)),
    )
    lines = ["### Tap collect"]
    for title, items in sections:
        if items:
            lines += [f"{title} ({len(items)}):", *(f"- {item}" for item in items)]
    return "\n".join(lines) + "\n"


def _gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taps", nargs="*", required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--budget-minutes", type=float, default=270)
    parser.add_argument("--out", type=Path, default=Path("tap_casks.json"))
    args = parser.parse_args(argv)
    if invalid := [error for name in args.taps if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))

    def save(result: dict) -> None:
        write_json(args.out, result, trailing_newline=True)

    with tempfile.TemporaryDirectory() as workdir:
        registered = {entry["name"]: entry for entry in tap_registry.load()}

        def lister(name: str) -> list[str] | None:
            return list_casks(registered.get(name, {"name": name}), workdir=Path(workdir))

        result = collect(args.taps, _gh, os.environ["GITHUB_REF_NAME"], load_official(), batch_size=args.batch_size,
                         budget=args.budget_minutes * 60, workdir=Path(workdir), on_progress=save, lister=lister)
    save(result)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
