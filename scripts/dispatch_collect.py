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
from pathlib import Path
from typing import Callable

import tap_registry
from collect_tap_casks import aliases, load_official, trusted_view
from style_standards import write_json

WORKFLOW = "tap-collect.yml"
TITLE = "Tap collect "

Gh = Callable[[list[str]], str]


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
    return data if isinstance(data, dict) else None


def collect(taps: list[str], gh: Gh, ref: str, official: dict[str, dict], *, batch_size: int, budget: float,
            workdir: Path, sleep=time.sleep, clock=time.monotonic, on_progress=lambda result: None) -> dict:
    groups = {uuid.uuid4().hex: group for group in batches(taps, batch_size)}
    for nonce, group in groups.items():
        gh(["workflow", "run", WORKFLOW, "--ref", ref, "-f", f"taps={' '.join(group)}", "-f", f"nonce={nonce}"])
    runs = _find_runs(gh, set(groups), sleep)
    deadline = clock() + budget
    casks: dict[str, dict] = {}
    failures: dict[str, str] = {}
    unreached: list[str] = []

    def result() -> dict:
        return {"casks": dict(sorted(casks.items())), "sameAs": aliases(casks, official),
                "failures": failures, "unreached": unreached}

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
            casks.update(trusted_view(data, group, official)["casks"])
            reported = data.get("failures")
            failures.update({name: str(reason) for name, reason in (reported if isinstance(reported, dict) else {}).items()
                             if name in group})
            left = data.get("unreached")
            unreached.extend(name for name in (left if isinstance(left, list) else []) if name in group)
        on_progress(result())
    return result()


def _gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taps", nargs="+", required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--budget-minutes", type=float, default=270)
    parser.add_argument("--out", type=Path, default=Path("tap_casks.json"))
    args = parser.parse_args(argv)
    if invalid := [error for name in args.taps if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))

    def save(result: dict) -> None:
        write_json(args.out, result, trailing_newline=True)

    with tempfile.TemporaryDirectory() as workdir:
        save(collect(args.taps, _gh, os.environ["GITHUB_REF_NAME"], load_official(), batch_size=args.batch_size,
                     budget=args.budget_minutes * 60, workdir=Path(workdir), on_progress=save))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
