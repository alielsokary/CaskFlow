"""Install-time icon harvest: token gate, app choice, and validated publication."""
import json
from pathlib import Path

import pytest

import harvest_installed_icons as harvest

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def _evidence(directory: Path, token: str, status: str, detail: str, png: bytes | None = None,
              recorded_token: str | None = None) -> None:
    evidence = directory / f"harvest-{token}"
    evidence.mkdir(parents=True)
    (evidence / "harvest.json").write_text(json.dumps(
        {"token": recorded_token or token, "status": status, "detail": detail}))
    if png is not None:
        (evidence / "icon.png").write_bytes(png)


@pytest.mark.parametrize("text", ["", "a b a", "good bad;rm", "../x", " ".join(f"t{i}" for i in range(51))])
def test_token_gate_rejects_unsafe_or_oversized_input(text):
    with pytest.raises(ValueError):
        harvest.tokens_from(text)


def test_matrix_lists_one_job_per_token():
    assert harvest.matrix("endnote gpg-suite font-foo@2") == {"include": [
        {"token": "endnote"}, {"token": "gpg-suite"}, {"token": "font-foo@2"}]}


def test_pick_app_skips_installers_and_prefers_the_token_match(tmp_path):
    apps = [tmp_path / "Uninstall EndNote.app", tmp_path / "EndNote 2025.app", tmp_path / "Helper.app"]
    assert harvest.pick_app(apps, "endnote") == (tmp_path / "EndNote 2025.app", "token")
    assert harvest.pick_app(apps[:1], "endnote") is None
    assert harvest.pick_app([], "endnote") is None


@pytest.mark.parametrize("returncode,added,expected", [
    (0, ["/Applications/Uninstall EndNote.app", "/Applications/EndNote 2025.app"], ("ok", "token")),
    (0, ["/Applications/Uninstall EndNote.app"], ("no_icon", "install added only installer/updater apps")),
    (0, [], ("no_icon", "install added no app")),
    (1, ["/Applications/EndNote 2025.app"], ("failed", "brew install failed")),
])
def test_harvest_records_the_installed_app_icon(tmp_path, monkeypatch, returncode, added, expected):
    snapshots = iter([{"applications": {}}, {"applications": {path: {"path": path} for path in added}}])
    monkeypatch.setattr(harvest, "require_disposable_runner", lambda arch: None)
    monkeypatch.setattr(harvest, "snapshot", lambda roots, receipts: next(snapshots))
    monkeypatch.setattr(harvest.Commands, "run", lambda self, *args, **kw: {
        "returncode": returncode, "timedOut": False})
    monkeypatch.setattr(harvest, "_icon_status", lambda app, selection, dest: ("ok", selection))

    harvest.harvest("endnote", tmp_path / "out")

    result = json.loads((tmp_path / "out" / "harvest.json").read_text())
    assert (result["status"], result["detail"]) == expected


def test_publish_accepts_only_validated_unpublished_results(tmp_path, monkeypatch):
    _evidence(tmp_path, "endnote", "ok", "token", PNG)
    _evidence(tmp_path, "fake-png", "ok", "token", b"<svg/>")
    _evidence(tmp_path, "spoofed", "ok", "token", PNG, recorded_token="other")
    _evidence(tmp_path, "already", "ok", "token", PNG)
    _evidence(tmp_path, "driver", "no_icon", "install added no app")
    published = {}
    monkeypatch.setattr(harvest, "load_report", lambda: {"endnote": {"status": "no_icon"}})
    monkeypatch.setattr(harvest, "published_tokens", lambda: {"already"})
    monkeypatch.setattr(harvest, "publish_batch", lambda pngs, report, dirty: published.update(
        pngs=pngs, report=report, dirty=dirty))

    harvest.publish(tmp_path, "endnote fake-png spoofed already driver missing")

    assert list(published["pngs"]) == ["endnote"]
    assert published["dirty"] == {"endnote", "driver"}
    assert "endnote" not in published["report"]
    assert published["report"]["driver"]["reason"] == "install harvest: install added no app"
