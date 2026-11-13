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
