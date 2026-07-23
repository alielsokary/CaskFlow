# Contributing to CaskFlow

Thanks for helping improve the data that powers CaskHub. Contributions are welcome across classification quality, automation safety, tests, documentation, and icon coverage.

## Before you start

For a focused bug fix or documentation improvement, open a pull request directly. For schema changes, new primary categories, or changes to release behavior, open an issue first so the producer and CaskHub consumer can be updated together.

Use Python 3.12 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest --cov=scripts --cov-report=term-missing
```

To exercise the classifier without changing tracked files or using a paid provider:

```bash
LLM_PROVIDER=mock python scripts/classify_new_casks.py --dry-run
```

## Correcting a category

Category changes should be based on what the application actually does. Use the evidence order and boundary rules in [docs/CLASSIFICATION_GUIDE.md](docs/CLASSIFICATION_GUIDE.md), then add a reviewed entry to `data/category_corrections.json`.

```json
{
  "token": "example",
  "was": "utilities",
  "shouldBe": "developerTools",
  "confidence": "high",
  "reason": "The application is a development environment."
}
```

Preview corrections before applying them:

```bash
python scripts/apply_corrections.py --dry-run
python scripts/apply_corrections.py
```

Use `--all` only after the non-high-confidence entries have been reviewed. The tool rejects unknown categories, stale `was` values, secondary-only traits used as primaries, duplicate categories, and more than two secondary categories.

## Pull requests

PR titles follow the semantic form `<type>(optional-scope): description`. Allowed types are `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, and `test`.

Examples:

- `fix(categories): Correct game engine classifications`
- `test(icons): Cover checksum failures`
- `docs: Explain the release data contract`

Every PR is assigned to the repository owner for triage. Before requesting review:

- Keep the change focused and explain its user or data impact.
- Add or update tests for behavior changes.
- Run the full Python suite.
- Update the classification guide when a boundary rule changes.
- Call out any CaskHub consumer change that must ship with the producer update.

Generated daily classification PRs follow the same checks. A classification below `0.75` confidence disables auto-merge and requires manual review.
