#!/usr/bin/env python3
"""Collect tap casks in separate workflow runs, one tap each."""
# Tap code runs only inside `tap-collect.yml` runs. Each run's token reaches only its own
# artifacts, so a hostile tap can shape only its own data: this job keeps from each run
# only the casks of the tap that run was given.
from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 - fixed gh commands, argument lists, and no shell
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import tap_registry
from collect_tap_casks import aliases, load_official, trusted_view
from style_standards import write_json
from tap_registry import REF
from validate_taps import list_casks

WORKFLOW = "tap-collect.yml"
TITLE = "Tap collect "

Gh = Callable[[list[str]], str]


def batches(taps: list[str], size: int) -> list[list[str]]:
    return [taps[i:i + size] for i in range(0, len(taps), size)]


@dataclass
class Runs:
    """Workflow runs of tap-collect.yml, driven through the gh CLI."""

    gh: Gh
    ref: str
    workdir: Path
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def dispatch(self, groups: dict[str, list[str]]) -> dict[str, int]:
        """Start one run per group; the run id of each group whose run could be started and found."""
        dispatched = set()
        for nonce, group in groups.items():
            try:
                self.gh(["workflow", "run", WORKFLOW, "--ref", self.ref, "-f", f"taps={' '.join(group)}", "-f", f"nonce={nonce}"])
                dispatched.add(nonce)
            except subprocess.CalledProcessError:
                print(f"warning: could not dispatch {' '.join(group)}; it waits for the next run")
            self.sleep(1)  # GitHub limits bursts of write requests
        return self._find(dispatched)

    def _find(self, nonces: set[str], attempts: int = 30) -> dict[str, int]:
        found: dict[str, int] = {}
        for _ in range(attempts):
            for run in self._json(["run", "list", "--workflow", WORKFLOW, "--event", "workflow_dispatch",
                                   "--limit", str(len(nonces) + 100), "--json", "databaseId,displayTitle"]) or []:
                if (nonce := run["displayTitle"].removeprefix(TITLE)) in nonces:
                    found.setdefault(nonce, run["databaseId"])
            if len(found) == len(nonces):
                break
            self.sleep(10)
        return found

    def read(self, run_id: int, deadline: float) -> dict | None:
        """The run's collected data once it finishes; a run still going at `deadline` is cancelled."""
        while (self._json(["run", "view", str(run_id), "--json", "status"]) or {}).get("status") != "completed":
            if self.clock() >= deadline:
                self._json(["run", "cancel", str(run_id)])
                return None
            self.sleep(20)
        folder = self.workdir / str(run_id)
        try:
            self.gh(["run", "download", str(run_id), "-n", "tap-casks", "-D", str(folder)])
            data = json.loads((folder / "tap_casks.json").read_text(encoding="utf-8"))
        except (subprocess.CalledProcessError, OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("casks"), dict) else None

    def _json(self, args: list[str]):
        try:
            return json.loads(self.gh(args) or "null")
        except (subprocess.CalledProcessError, ValueError):
            return None


@dataclass
class Selection:
    """The taps one pipeline run collects, and each tap's cask files from git."""

    listed: dict[str, dict[str, str]]
    todo: list[str]
    unchanged: list[str]


def select(taps: list[str], lister: Callable[[str], dict[str, str] | None], known: dict[str, str],
           only_changed: bool) -> Selection:
    """With `only_changed`, taps whose every listed cask file was already settled (`known`) are skipped."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        listed = {tap: refs for tap, refs in zip(taps, pool.map(lister, taps)) if refs}

    def changed(tap: str) -> bool:
        return tap not in listed or any(known.get(ref) != oid for ref, oid in listed[tap].items())

    todo = [tap for tap in taps if changed(tap)] if only_changed else list(taps)
    return Selection(listed, todo, sorted(set(taps) - set(todo)))


@dataclass
class _Totals:
    official: dict[str, dict]
    selection: Selection
    casks: dict[str, dict] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    cask_failures: dict[str, str] = field(default_factory=dict)
    processed: dict[str, str] = field(default_factory=dict)
    unreached: list[str] = field(default_factory=list)

    def add(self, data: dict | None, group: list[str]) -> None:
        view = self._view(data, group)
        if view is None:
            self.unreached.extend(group)
            return
        self.casks.update(view["casks"])
        self.cask_failures.update(view["caskFailures"])
        self.failures.update({name: str(reason) for name, reason in _items(data.get("failures")) if name in group})
        left = data.get("unreached")
        self.unreached.extend(name for name in (left if isinstance(left, list) else []) if name in group)
        self._settle(group)

    def _view(self, data: dict | None, group: list[str]) -> dict | None:
        try:
            return trusted_view(data, group, self.official) if data is not None else None
        except (TypeError, AttributeError, ValueError):
            return None

    def _settle(self, group: list[str]) -> None:
        """Record the file ids of casks read cleanly, so unchanged taps can be skipped next week."""
        for tap in group:
            if tap not in self.failures and tap not in self.unreached:
                listed = self.selection.listed.get(tap, {})
                self.processed.update({ref: oid for ref, oid in listed.items() if ref not in self.cask_failures})

    def result(self) -> dict:
        return {"casks": dict(sorted(self.casks.items())), "sameAs": aliases(self.casks, self.official),
                "failures": self.failures, "caskFailures": dict(sorted(self.cask_failures.items())),
                "listed": dict(sorted(self.selection.listed.items())), "processed": dict(sorted(self.processed.items())),
                "unchanged": self.selection.unchanged, "unreached": self.unreached}


def _items(value: object) -> list[tuple]:
    return list(value.items()) if isinstance(value, dict) else []


def collect(selection: Selection, runs: Runs, official: dict[str, dict], *, batch_size: int, budget: float,
            on_progress=lambda result: None) -> dict:
    groups = {uuid.uuid4().hex: group for group in batches(selection.todo, batch_size)}
    run_ids = runs.dispatch(groups)
    deadline = runs.clock() + budget
    totals = _Totals(official, selection)
    for nonce, group in groups.items():
        totals.add(runs.read(run_ids[nonce], deadline) if nonce in run_ids else None, group)
        on_progress(totals.result())
    return totals.result()


def summary(result: dict) -> str:
    """Names only, each re-validated: collected text came from tap code."""
    def names(values: object, valid) -> list[str]:
        return sorted(value for value in (values or []) if isinstance(value, str) and valid(value))

    def is_tap(name: str) -> bool:
        return tap_registry.validate_name(name) is None

    sections = (
        ("Taps that failed", names(result.get("failures"), is_tap)),
        ("Casks that failed to load", names(result.get("caskFailures"), lambda ref: bool(REF.fullmatch(ref)))),
        ("Taps left for the next run", names(result.get("unreached"), is_tap)),
    )
    lines = ["### Tap collect"]
    for title, items in sections:
        if items:
            lines += [f"{title} ({len(items)}):", *(f"- {item}" for item in items)]
    return "\n".join(lines) + "\n"


def _gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout  # nosec B603, B607 - gh from PATH; no shell


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taps", nargs="*", required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--budget-minutes", type=float, default=270)
    parser.add_argument("--out", type=Path, default=Path("tap_casks.json"))
    parser.add_argument("--files", type=Path, help="data/tap_files.json: cask files already settled")
    parser.add_argument("--only-changed", action="store_true", help="Skip taps whose cask files are all settled")
    args = parser.parse_args(argv)
    if invalid := [error for name in args.taps if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))

    def save(result: dict) -> None:
        write_json(args.out, result, trailing_newline=True)

    with tempfile.TemporaryDirectory() as workdir:
        registered = {entry["name"]: entry for entry in tap_registry.load()}
        known = json.loads(args.files.read_text(encoding="utf-8")) if args.files and args.files.exists() else {}
        selection = select(args.taps, lambda name: list_casks(registered.get(name, {"name": name}), workdir=Path(workdir)),
                           known, args.only_changed)
        runs = Runs(_gh, os.environ["GITHUB_REF_NAME"], Path(workdir))
        result = collect(selection, runs, load_official(), batch_size=args.batch_size,
                         budget=args.budget_minutes * 60, on_progress=save)
    save(result)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
