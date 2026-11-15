#!/usr/bin/env python3
"""Collect tap casks on this runner, each tap's brew calls in its own sandbox."""
# Tap code runs only inside collect_tap_casks.TapSandbox: no network, no writes outside its
# own folder, no tokens, and no process outliving a call. A hostile tap can still shape its
# own output, so this job keeps from each tap's read only the casks of that tap.
from __future__ import annotations

import argparse
import json
import os
import queue
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import tap_registry
from collect_tap_casks import TapSandbox, aliases, create_tap_users, load_official, read_tap, trusted_view, warm_cache
from style_standards import write_json
from tap_registry import REF
from validate_taps import list_casks

Read = Callable[[str, float], dict]


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


def collect(selection: Selection, read: Read, official: dict[str, dict], *, workers: int, budget: float,
            clock=time.monotonic, on_progress=lambda result: None) -> dict:
    """Read the selected taps in parallel; reads not started by the deadline leave their tap for the next run."""
    deadline = clock() + budget
    totals = _Totals(official, selection)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(read, tap, deadline): tap for tap in selection.todo}
        for future in as_completed(futures):
            try:
                data = future.result()
            except (RuntimeError, OSError, ValueError, KeyError, TypeError) as error:  # a crashed read costs only its tap
                print(f"warning: reading {futures[future]} crashed: {type(error).__name__}")
                data = None
            totals.add(data, [futures[future]])
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taps", nargs="*", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget-minutes", type=float, default=270)
    parser.add_argument("--out", type=Path, default=Path("tap_casks.json"))
    parser.add_argument("--files", type=Path, help="data/tap_files.json: cask files already settled")
    parser.add_argument("--only-changed", action="store_true", help="Skip taps whose cask files are all settled")
    parser.add_argument("--tap-users", action="store_true", help="Run brew as throwaway accounts (CI; needs sudo)")
    args = parser.parse_args(argv)
    if invalid := [error for name in args.taps if (error := tap_registry.validate_name(name))]:
        raise SystemExit("; ".join(invalid))

    def save(result: dict) -> None:
        write_json(args.out, result, trailing_newline=True)

    with tempfile.TemporaryDirectory() as workdir:
        work = Path(workdir).resolve()
        registered = {entry["name"]: entry for entry in tap_registry.load()}
        known = json.loads(args.files.read_text(encoding="utf-8")) if args.files and args.files.exists() else {}
        selection = select(args.taps, lambda name: list_casks(registered.get(name, {"name": name}), workdir=work),
                           known, args.only_changed)
        # Warmed on the host before any tap code runs; each sandbox gets its own clone of it.
        warm_cache(work / "cache")
        # Tap users must reach their sandboxes, which this user's private temp folder would hide.
        boxes = Path(tempfile.mkdtemp(prefix="caskflow-sandboxes-", dir="/private/tmp" if args.tap_users else None))
        boxes.chmod(0o711)
        users = queue.Queue()
        for user in create_tap_users(args.workers) if args.tap_users else [None] * args.workers:
            users.put(user)

        def read(tap: str, deadline: float) -> dict:
            user = users.get()
            try:
                return read_tap(registered.get(tap, {"name": tap}), lambda: TapSandbox(boxes, work / "cache", user),
                                deadline=deadline)
            finally:
                users.put(user)

        result = collect(selection, read, load_official(), workers=args.workers,
                         budget=args.budget_minutes * 60, on_progress=save)
    save(result)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
