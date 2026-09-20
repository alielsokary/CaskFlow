"""The pilot must preserve uncertainty and isolate executable installs from publication."""
import argparse
import json
import plistlib
import sys
from pathlib import Path

import pytest

import observe_package_install as pilot
from report_package_observations import build_report
from style_standards import write_json


def make_app(root, name="Product.app", identifier="com.example.product", version="1", flat=False):
    bundle = root / name
    content = bundle if flat else bundle / "Contents"
    executable = content if flat else content / "MacOS"
    executable.mkdir(parents=True)
    (executable / "Product").write_bytes(b"executable")
    (content / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": identifier, "CFBundleShortVersionString": version,
        "CFBundleExecutable": "Product",
    }))
    return bundle


def empty_snapshot():
    return {"applications": {}, "receipts": {}, "errors": []}


def test_matrix_is_finite_and_uses_real_architecture_labels():
    jobs = pilot.matrix("", "both")
    assert len(jobs) == 20
    assert {job["runner"] for job in jobs} == {"macos-15", "macos-15-intel"}
    assert len(pilot.matrix("zoom airtool", "arm64")) == 2
    for tokens in ("zoom zoom", "zoom;touch /tmp/oops", "unreviewed-cask"):
        with pytest.raises(ValueError):
            pilot.matrix(tokens, "both")


def test_installation_entrypoint_refuses_a_local_machine(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(ValueError, match="GitHub-hosted"):
        pilot.require_disposable_runner("arm64")


def test_installation_entrypoint_rejects_self_hosted_or_wrong_architecture(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "macOS")
    monkeypatch.setattr(pilot.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(pilot.platform, "machine", lambda: "arm64")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    with pytest.raises(ValueError):
        pilot.require_disposable_runner("arm64")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    with pytest.raises(ValueError):
        pilot.require_disposable_runner("x86_64")
    pilot.require_disposable_runner("arm64")


@pytest.mark.parametrize("flat", [False, True])
def test_snapshot_reads_real_bundles_and_skips_embedded_helpers(tmp_path, flat):
    apps, receipts = tmp_path / "Applications", tmp_path / "receipts"
    receipts.mkdir()
    bundle = make_app(apps / "Suite", flat=flat)
    make_app(bundle / "Contents", "Helper.app", "com.example.helper")
    (apps / "Alias.app").symlink_to(bundle, target_is_directory=True)
    (receipts / "component.plist").write_bytes(plistlib.dumps({
        "PackageIdentifier": "com.example.component", "PackageVersion": "1", "InstallPrefixPath": "/Applications",
    }))
    result = pilot.snapshot([apps], receipts)
    assert list(result["applications"]) == [str(bundle)]
    assert result["applications"][str(bundle)]["bundleIdentifier"] == "com.example.product"
    assert result["applications"][str(bundle)]["executablePresent"]
    assert list(result["receipts"]) == ["com.example.component"]
    assert not result["errors"]


def test_malformed_and_escaping_bundle_evidence_is_not_accepted(tmp_path):
    bundle = make_app(tmp_path)
    info = bundle / "Contents/Info.plist"
    info.write_bytes(b"<plist><dict>")
    assert "error" in pilot.app_record(bundle)
    info.unlink()
    outside = tmp_path / "outside.plist"
    outside.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.fake"}))
    info.symlink_to(outside)
    assert "error" in pilot.app_record(bundle)


@pytest.mark.parametrize("install,registered,stable,expected", [
    ({"returncode": 0}, True, True, "observed_applications"),
    ({"returncode": 1}, True, True, "install_failed"),
    ({"returncode": -9, "timedOut": True}, True, True, "install_timeout"),
    ({"returncode": 0}, False, True, "incomplete_observation"),
    ({"returncode": 0}, True, False, "incomplete_observation"),
])
def test_installer_success_is_not_sufficient_evidence(install, registered, stable, expected):
    before, after = empty_snapshot(), empty_snapshot()
    after["applications"]["/Applications/Product.app"] = {
        "bundleIdentifier": "com.example.product", "identifierAccepted": True, "executablePresent": True}
    result = pilot.observation(before, after, install, registered, stable)
    assert result["status"] == expected
    assert result["ownershipVerified"] is False


def test_no_app_and_changed_preinstalled_software_are_distinct():
    before, after = empty_snapshot(), empty_snapshot()
    assert pilot.observation(before, after, {"returncode": 0}, True, True)["status"] == "no_application_observed"
    before["receipts"]["shared"] = {"version": "1"}
    after["receipts"]["shared"] = {"version": "2"}
    assert pilot.observation(before, after, {"returncode": 0}, True, True)["status"] == "preexisting_software_changed"
    after["receipts"].clear()
    assert pilot.observation(before, after, {"returncode": 0}, True, True)["status"] == "preexisting_software_changed"
    after["errors"].append("Unreadable receipt")
    assert pilot.observation(before, after, {"returncode": 0}, True, True)["status"] == "incomplete_observation"


def test_process_boundary_preserves_stderr_without_corrupting_json(tmp_path):
    commands = pilot.Commands(tmp_path)
    text = commands.text("query", [sys.executable, "-c", 'import sys; print("{}"); print("warning", file=sys.stderr)'])
    assert json.loads(text) == {}
    assert (tmp_path / "query.stderr.log").read_text().strip() == "warning"
    result = commands.run("timeout", [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1, check=False)
    assert result["timedOut"]
    assert result["returncode"] != 0


def report_plan(tmp_path, tokens="microsoft-teams microsoft-office-businesspro"):
    plan = tmp_path / "package-observation-plan"
    plan.mkdir()
    write_json(plan / "matrix.json", {"include": pilot.matrix(tokens, "arm64")})
    write_json(plan / "revisions.json", {"brewRevision": "b" * 40, "caskRevision": "c" * 40})
    write_json(plan / "passive-identities.json", {"casks": {}})


def save_observation(tmp_path, token, status="observed_applications"):
    folder = tmp_path / f"package-observation-{token}-arm64"
    folder.mkdir()
    result = {"schemaVersion": 1, "token": token, "architecture": "arm64", "ownershipVerified": False,
              "brewRevision": "b" * 40, "caskRevision": "c" * 40, "status": status,
              "install": {"returncode": 0}, "homebrewRegistered": True, "snapshotsStable": True,
              "checksumVerified": True, "artifactSHA256": "d" * 64, "expectedSHA256": "d" * 64,
              "applicationChanges": {"added": [{"bundleName": "Microsoft Teams.app",
                  "bundleIdentifier": "com.microsoft.teams2", "identifierAccepted": True, "executablePresent": True}],
                  "changed": []}}
    write_json(folder / "observation.json", result)
    return folder / "observation.json", result


def test_report_preserves_shared_component_ambiguity(tmp_path):
    report_plan(tmp_path)
    for token in ("microsoft-teams", "microsoft-office-businesspro"):
        save_observation(tmp_path, token)
    report = build_report(tmp_path)
    assert report["counts"] == {"observed_applications": 2}
    assert report["sharedIdentities"][0]["candidateTokens"] == ["microsoft-office-businesspro", "microsoft-teams"]
    assert report["ownershipVerified"] is False


def test_report_accounts_for_missing_interrupted_and_mismatched_jobs(tmp_path):
    report_plan(tmp_path)
    path, record = save_observation(tmp_path, "microsoft-teams", "installing")
    report = build_report(tmp_path)
    assert report["counts"] == {"incomplete_observation": 1, "missing_evidence": 1}
    record["caskRevision"] = "other"
    write_json(path, record)
    assert build_report(tmp_path)["counts"] == {"invalid_evidence": 1, "missing_evidence": 1}


def test_workflow_never_executes_installers_on_prs_or_publishes():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/observe-packages.yml"
    text = workflow.read_text()
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text and "pull_request:" not in text and "push:" not in text
    assert "write" not in text and "secrets." not in text
    assert "max-parallel: 4" in text and "fail-fast: false" in text
    assert "persist-credentials: false" in text
    assert "if: always()" in text
    assert "release.yml" not in text and "--publish" not in text


def test_raw_observations_preserve_actual_bundle_names(tmp_path):
    original = {"path": "/Applications/An App\u2019s Name.app"}
    path = tmp_path / "observation.json"
    pilot.save_evidence(path, original)
    assert json.loads(path.read_text()) == original


def test_successful_report_rejects_contradictory_install_evidence(tmp_path):
    report_plan(tmp_path, "zoom")
    path, record = save_observation(tmp_path, "zoom")
    record["install"]["returncode"] = 1
    write_json(path, record)
    assert build_report(tmp_path)["counts"] == {"invalid_evidence": 1}


@pytest.mark.parametrize("failure", [None, "checksum", "dependencies", "installed", "no-check"])
def test_prepare_pins_sources_and_requires_clean_checked_downloads(tmp_path, monkeypatch, failure):
    tap = tmp_path / "Library/Taps/homebrew/homebrew-cask"
    source = tap / "Casks/a/airtool.rb"
    source.parent.mkdir(parents=True)
    source.write_bytes(b'cask "airtool" do\nend\n')
    archive = tmp_path / "archive.pkg"
    archive.write_bytes(b"package")
    output = tmp_path / "output"
    output.mkdir()
    cask = {"token": "airtool", "artifacts": [{"pkg": ["Airtool.pkg"]}],
            "ruby_source_path": "Casks/a/airtool.rb", "sha256": pilot.digest(archive), "installed": None}
    if failure == "checksum":
        cask["sha256"] = "0" * 64
    if failure == "no-check":
        cask["sha256"] = "no_check"
    if failure == "installed":
        cask["installed"] = "1"
    calls, checkouts = [], []

    class FakeCommands:
        def __init__(self):
            self.output = output

        def run(self, name, args, *unused):
            calls.append(args)

        def text(self, name, args):
            return {"brew-root": str(tmp_path), "cask-info": json.dumps({"casks": [cask]}),
                    "dependencies": "dependency" if failure == "dependencies" else "",
                    "download-path": str(archive)}[name]

    monkeypatch.setattr(pilot, "checkout", lambda *args: checkouts.append(args[2:]))
    if failure:
        with pytest.raises(ValueError):
            pilot.prepare(FakeCommands(), "airtool", "b" * 40, "c" * 40)
    else:
        result = pilot.prepare(FakeCommands(), "airtool", "b" * 40, "c" * 40)
        assert result["checksumVerified"] is True
        assert (output / "cask.rb").read_bytes() == source.read_bytes()
    assert checkouts == [("Homebrew/brew", "b" * 40, "brew"), ("Homebrew/homebrew-cask", "c" * 40, "cask")]
    assert all("install" not in args for args in calls)


@pytest.mark.parametrize("exit_code,expected", [(0, "observed_applications"), (1, "install_failed")])
def test_observation_entrypoint_collects_evidence_after_install_failure(tmp_path, monkeypatch, exit_code, expected):
    calls = []

    class FakeCommands:
        def __init__(self, output):
            self.output = output

        def run(self, name, args, *unused, **kwargs):
            calls.append(args)
            (self.output / f"{name}.log").write_bytes(b"zoom\n" if name == "installed-casks" else b"")
            return {"returncode": exit_code if name == "install" else 0, "log": f"{name}.log", "timedOut": False}

    before, after = empty_snapshot(), empty_snapshot()
    after["applications"]["/Applications/zoom.us.app"] = {
        "path": "/Applications/zoom.us.app", "bundleName": "zoom.us.app", "bundleIdentifier": "us.zoom.xos",
        "identifierAccepted": True, "executablePresent": True}
    snapshots = iter([before, after, after, after, after])
    monkeypatch.setattr(pilot, "Commands", FakeCommands)
    monkeypatch.setattr(pilot, "require_disposable_runner", lambda arch: None)
    monkeypatch.setattr(pilot, "prepare", lambda *a: {"cask": {"artifacts": []}})
    monkeypatch.setattr(pilot, "snapshot", lambda *a: next(snapshots))
    monkeypatch.setattr(pilot, "capture_install_history", lambda *a: {"entryCount": 0})
    monkeypatch.setattr(pilot.time, "sleep", lambda seconds: None)
    output = tmp_path / "evidence"
    code = pilot.observe(argparse.Namespace(token="zoom", architecture="arm64", brew_revision="b" * 40,
                                           cask_revision="c" * 40, output=output))
    result = json.loads((output / "observation.json").read_text())
    assert code == exit_code
    assert result["status"] == expected
    assert (output / "after.json").exists()
    assert ["brew", "install", "--cask", "--require-sha", "zoom"] in calls
    assert all("uninstall" not in args and "--force" not in args for args in calls)


@pytest.mark.parametrize("keeps_changing", [False, True])
def test_settling_preserves_updater_changes_and_stops_at_a_deadline(tmp_path, monkeypatch, keeps_changing):
    clock = [0]

    def read_snapshot(*unused):
        value = empty_snapshot()
        version = str(clock[0]) if keeps_changing else str(min(clock[0], 15))
        value["applications"]["/Applications/OneDrive.app"] = {"bundleIdentifier": "com.microsoft.OneDrive", "version": version}
        return value
    monkeypatch.setattr(pilot, "snapshot", read_snapshot)
    monkeypatch.setattr(pilot.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pilot.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    after, settling = pilot.settle_snapshots(tmp_path, [])
    assert settling["stable"] is not keeps_changing
    assert clock[0] == (120 if keeps_changing else 60)
    assert settling["changedAfterInstall"] is True
    change = settling["samples"][1]["changes"][0]
    assert change["changedFields"] == ["version"]
    assert change["before"]["version"] == "0" and change["after"]["version"] == "15"
    assert json.loads((tmp_path / "after.json").read_text()) == after
    assert all((tmp_path / sample["snapshot"]).exists() for sample in settling["samples"])


def test_report_keeps_partial_office_overlap_and_explains_unstable_state(tmp_path):
    report_plan(tmp_path)
    save_observation(tmp_path, "microsoft-teams")
    path, record = save_observation(tmp_path, "microsoft-office-businesspro", "incomplete_observation")
    record["snapshotsStable"] = False
    write_json(path, record)
    report = build_report(tmp_path)
    assert report["observations"][1]["diagnostics"] == ["post_install_state_not_settled"]
    collision = report["sharedIdentities"][0]
    assert collision["candidateTokens"] == ["microsoft-office-businesspro", "microsoft-teams"]
    assert {item["evidenceQuality"] for item in collision["observations"]} == {"stable", "partial"}
    assert report["ownershipVerified"] is False


def test_install_history_keeps_original_groups_and_reports_missing_evidence(tmp_path):
    source = tmp_path / "history.plist"
    records = [{"displayName": "Suite", "packageIdentifiers": ["com.example.app", "com.example.helper"]}]
    source.write_bytes(plistlib.dumps(records))
    captured = pilot.capture_install_history(tmp_path, "before", source)
    assert captured["entryCount"] == 1
    assert (tmp_path / captured["file"]).read_bytes() == source.read_bytes()
    source.write_bytes(b"not a plist")
    assert "error" in pilot.capture_install_history(tmp_path, "after", source)
    source.unlink()
    assert "error" in pilot.capture_install_history(tmp_path, "after", source)
