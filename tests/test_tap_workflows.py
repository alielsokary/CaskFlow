"""Contracts for the tap registry and tap pipeline workflows: no secrets where tap code or PR input runs."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _workflow(name: str) -> str:
    return (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_registry_validation_runs_on_prs_without_secrets():
    validate = _workflow("validate-taps.yml")

    assert "pull_request:" in validate
    assert "taps.json" in validate
    assert "contents: read" in validate
    assert "secrets." not in validate
    assert "pull_request_target" not in validate
    assert "python scripts/validate_taps.py --base-ref" in validate


def _job(workflow: str, name: str) -> str:
    start = workflow.index(f"\n  {name}:\n")
    rest = workflow[start + 1:]
    next_job = [i for i in (rest.find("\n  " + j + ":\n") for j in ("resolve", "collect", "classify", "icons", "publish")) if i > 0]
    return rest[: min(next_job)] if next_job else rest


def test_pipeline_triggers_and_serialization():
    pipeline = _workflow("tap-pipeline.yml")

    for trigger in ("push:", "schedule:", "workflow_dispatch:", "issues:", "taps.json", "labeled"):
        assert trigger in pipeline
    assert "group: tap-pipeline" in pipeline
    assert "cancel-in-progress: false" in pipeline
    assert "pull_request" not in pipeline
    assert "github.event.label.name == 'add-tap'" in pipeline


def test_jobs_that_touch_tap_code_or_installers_hold_no_secrets():
    pipeline = _workflow("tap-pipeline.yml")

    for name in ("collect", "icons"):
        job = _job(pipeline, name)
        assert "secrets." not in job, name
        assert "contents: read" in job, name
        assert "persist-credentials: false" in job, name


def test_untrusted_inputs_only_reach_scripts_through_env():
    pipeline = _workflow("tap-pipeline.yml")

    for line in pipeline.splitlines():
        if "inputs.tap" in line or "github.event.issue.body" in line or "needs.resolve.outputs" in line:
            stripped = line.strip()
            assert not stripped.startswith("run:"), line
            assert ":" in stripped and not stripped.startswith("python"), line


def test_publish_runs_no_extraction_and_opens_a_pr():
    publish = _job(_workflow("tap-pipeline.yml"), "publish")

    assert "--publish-from" in publish
    assert "collect_tap_casks.py" not in publish
    assert "create-pull-request" in publish
    assert "taps.json" in publish and "data/tap_categories.json" in publish


def test_every_job_that_reads_collect_output_downloads_it():
    pipeline = _workflow("tap-pipeline.yml")

    for name in ("classify", "icons"):
        job = _job(pipeline, name)
        assert "tap_casks.json" in job
        assert "name: tap-casks" in job and "download-artifact" in job, name


def test_publish_takes_artifacts_only_by_id_from_their_producing_jobs():
    pipeline = _workflow("tap-pipeline.yml")
    publish = _job(pipeline, "publish")

    for producer, output in (("classify", "steps.data.outputs.artifact-id"), ("icons", "steps.upload.outputs.artifact-id")):
        assert output in _job(pipeline, producer), producer
        assert f"needs.{producer}.outputs.artifact-id" in publish, producer
    assert "name: tap-data" not in publish and "name: tap-icons" not in publish
    assert "cache: pip" not in publish
    assert "--publish-from icons_out --taps $TAPS" in publish
