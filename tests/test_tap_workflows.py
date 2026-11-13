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
    next_job = [i for i in (rest.find("\n  " + j + ":\n") for j in ("resolve", "explain-refusal", "collect", "classify", "icons", "publish")) if i > 0]
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
    collect = _workflow("tap-collect.yml")
    assert "secrets." not in collect and "contents: read" in collect and "persist-credentials: false" in collect


def test_untrusted_inputs_only_reach_scripts_through_env():
    pipeline = _workflow("tap-pipeline.yml")

    for line in pipeline.splitlines():
        if "inputs.tap" in line or "issue.body" in line or "issue.title" in line or "needs.resolve.outputs" in line:
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


def test_issue_runs_read_the_tap_from_the_title_never_the_body():
    resolve = _job(_workflow("tap-pipeline.yml"), "resolve")
    assert "ISSUE_TITLE: ${{ github.event.issue.title }}" in resolve
    assert "issue.body" not in resolve


def test_jobs_with_secrets_or_write_tokens_restore_no_actions_cache():
    # collect runs tap code that could seed cache entries other jobs restore.
    assert "cache: pip" not in _workflow("tap-pipeline.yml")
    assert "cache: pip" not in _workflow("classify-new-casks.yml")


def test_collect_keeps_partial_progress_within_its_budget():
    collect = _job(_workflow("tap-pipeline.yml"), "collect")
    assert "--budget-minutes 270" in collect and "timeout-minutes: 300" in collect
    upload = collect[collect.index("upload-artifact"):]
    assert "if: always()" in upload


def test_tap_code_runs_only_in_separate_runs_per_small_batch():
    pipeline = _workflow("tap-pipeline.yml")
    collect = _job(pipeline, "collect")
    assert "runs-on: ubuntu-latest" in collect and "actions: write" in collect
    assert "python scripts/dispatch_collect.py --taps $TAPS --batch-size 5" in collect
    assert "collect_tap_casks.py" not in pipeline and "brew " not in pipeline

    per_batch = _workflow("tap-collect.yml")
    assert "workflow_dispatch:" in per_batch and "run-name: Tap collect ${{ inputs.nonce }}" in per_batch
    assert "runs-on: macos-latest" in per_batch and "python scripts/collect_tap_casks.py --taps $TAPS" in per_batch
    assert "inputs.taps" not in "\n".join(line for line in per_batch.splitlines() if line.strip().startswith("run:"))
    assert "if: always()" in per_batch[per_batch.index("upload-artifact"):]


def test_data_prs_roll_on_one_branch_and_continue_an_open_one():
    pipeline = _workflow("tap-pipeline.yml")
    assert "branch: bot/tap-pipeline\n" in pipeline
    for name in ("classify", "publish"):
        job = _job(pipeline, name)
        assert "gh pr list --head bot/tap-pipeline --state open" in job, name
        assert "pull-requests: read" in job or "pull-requests: write" in job, name
    publish = _job(pipeline, "publish")
    assert "tap_registry.py --add $REGISTER --only-readable data/tap_run.json" in publish
    assert "tap_registry.py --add $CONTINUED" in publish


def test_scheduled_sweep_reads_issues_through_the_resolver():
    resolve = _job(_workflow("tap-pipeline.yml"), "resolve")
    assert "GH_TOKEN: ${{ github.token }}" in resolve and "issues: read" in resolve
    assert "gh issue list" not in resolve, "titles are checked against the label timeline in resolve_tap_run.py"


def test_open_data_pr_is_used_only_from_this_repository_and_by_its_head_commit():
    pipeline = _workflow("tap-pipeline.yml")
    for name in ("classify", "publish"):
        job = _job(pipeline, name)
        assert "select(.isCrossRepository == false)" in job and "headRefOid" in job, name
        assert 'git fetch -q origin "$HEAD"' in job, name


def test_no_workflow_restores_an_actions_cache():
    # Dispatched tap-collect runs share the default branch's cache scope.
    for path in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "cache: pip" not in text and "actions/cache" not in text, path.name


def test_removed_taps_reach_classify_and_publish_through_env():
    pipeline = _workflow("tap-pipeline.yml")
    assert "prune: ${{ steps.resolve.outputs.prune }}" in _job(pipeline, "resolve")
    assert "needs.resolve.outputs.prune != ''" in _job(pipeline, "collect")
    classify = _job(pipeline, "classify")
    assert "PRUNE: ${{ needs.resolve.outputs.prune }}" in classify and "--prune $PRUNE" in classify
    assert "data/tap_run.json" in classify
    publish = _job(pipeline, "publish")
    assert "--publish-from icons_out --taps $TAPS $PRUNE --run-file data/tap_run.json" in publish
    paths = publish[publish.index("add-paths:"):]
    assert "tap_run.json" not in paths


def test_refused_issue_requests_get_a_fixed_explanation():
    job = _job(_workflow("tap-pipeline.yml"), "explain-refusal")
    assert "needs: resolve" in job and "if: failure() && github.event_name == 'issues'" in job
    assert "issues: write" in job and "ISSUE: ${{ github.event.issue.number }}" in job
    assert "Couldn't find a tap with casks at that name" in job
    assert "issue.title" not in job and "issue.body" not in job
