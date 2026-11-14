#!/usr/bin/env python3
"""Decide which taps a tap-pipeline run covers, and which new tap (if any) it registers."""
from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - fixed gh and git commands, argument lists, and no shell

import tap_registry
from validate_taps import changed_entries, check_tap


def resolve(event: str, *, base: list[dict], head: list[dict], dispatch_tap: str = "", issue_title: str = "",
            issue_titles: list[str] = (), check=check_tap, data_taps: list[str] = ()) -> tuple[list[str], list[str], list[str]]:
    """Taps to process, taps to register, and taps removed from the registry whose data must go."""
    registered = [entry["name"] for entry in head]
    if event == "push":
        removed = sorted({entry["name"] for entry in base} - set(registered))
        return [entry["name"] for entry in changed_entries(base, head)], [], removed
    if event == "schedule":
        return _sweep(registered, issue_titles, check, data_taps)
    name = _requested_name(event, dispatch_tap, issue_title)
    if name in registered:
        return [name], [], []
    # A requested tap must exist and hold casks before any of its code runs.
    if error := _refusal(check, name):
        raise SystemExit(error)
    return [name], [name], []


def _refusal(check, name: str) -> str | None:
    try:
        return check({"name": name})
    except (subprocess.SubprocessError, OSError) as error:
        return f"{name} could not be checked: {type(error).__name__}"


def _sweep(registered: list[str], issue_titles: list[str], check, data_taps: list[str]) -> tuple[list[str], list[str], list[str]]:
    # A labeled issue whose run was dropped from the concurrency queue is picked up here.
    requested = {name for title in issue_titles if (name := tap_registry.parse_issue_title(title))}
    new = []
    for name in sorted(requested - set(registered)):
        if error := _refusal(check, name):
            print(f"skipping requested tap: {error}")
        else:
            new.append(name)
    # A removal whose push run was dropped or failed is caught up here.
    return sorted(registered + new), new, sorted(set(data_taps) - set(registered))


def _requested_name(event: str, dispatch_tap: str, issue_title: str) -> str:
    if event == "workflow_dispatch":
        name = dispatch_tap.strip().lower()
        if error := tap_registry.validate_name(name):
            raise SystemExit(error)
        return name
    if event == "issues":
        if (name := tap_registry.parse_issue_title(issue_title)) is None:
            raise SystemExit("the issue title is not 'Add tap: user/repo'")
        return name
    raise SystemExit(f"unsupported event {event!r}")


def approved(events: list[dict]) -> bool:
    """A maintainer approved the title as it is now: the latest add-tap label came after the last rename."""
    labeled = [e["created_at"] for e in events if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == "add-tap"]
    renamed = [e["created_at"] for e in events if e.get("event") == "renamed"]
    return bool(labeled) and max(labeled) > max(renamed, default="")


def _gh_json(*args: str):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)  # nosec B603, B607 - no shell
    return json.loads(result.stdout)


def approved_issue_titles() -> list[str]:
    """Titles of open add-tap issues, for picking up runs the concurrency queue dropped."""
    repo = os.environ["GITHUB_REPOSITORY"]
    titles = []
    try:
        for issue in _gh_json("issue", "list", "--repo", repo, "--label", "add-tap", "--state", "open",
                              "--limit", "200", "--json", "number,title"):
            pages = _gh_json("api", f"repos/{repo}/issues/{issue['number']}/timeline", "--paginate", "--slurp")
            if approved([event for page in pages for event in page]):
                titles.append(issue["title"])
    except (subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f"warning: cannot list add-tap issues: {error}")
    return titles


def _data_taps() -> list[str]:
    path = tap_registry.REPO_ROOT / "data" / "tap_categories.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    refs = list(data.get("casks", {})) + list(data.get("sameAs", {}))
    return sorted({ref.rsplit("/", 1)[0] for ref in refs if tap_registry.validate_name(ref.rsplit("/", 1)[0]) is None})


def _base_entries(before: str) -> list[dict]:
    if not before or set(before) == {"0"}:
        return []
    shown = subprocess.run(  # nosec B603, B607 - git from PATH; the event's commit sha; no shell
        ["git", "show", f"{before}:taps.json"], capture_output=True, text=True, cwd=tap_registry.REPO_ROOT)
    return json.loads(shown.stdout).get("taps", []) if shown.returncode == 0 else []


def main() -> int:
    taps, register, prune = resolve(
        os.environ["EVENT_NAME"],
        base=_base_entries(os.environ.get("BEFORE_SHA", "")),
        head=tap_registry.load(),
        dispatch_tap=os.environ.get("DISPATCH_TAP", ""),
        issue_title=os.environ.get("ISSUE_TITLE", ""),
        issue_titles=approved_issue_titles() if os.environ["EVENT_NAME"] == "schedule" else [],
        data_taps=_data_taps(),
    )
    print(f"taps: {taps}, register: {register}, prune: {prune}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"taps={' '.join(taps)}\nregister={' '.join(register)}\nprune={' '.join(prune)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
