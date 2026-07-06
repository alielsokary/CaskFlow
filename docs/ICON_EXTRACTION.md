# Icon Extraction

Extracts original app icons from Homebrew cask artifacts and publishes them as
per-cask GitHub pre-releases. Full design rationale:
`docs/superpowers/specs/2026-07-05-icon-extraction-design.md`.

## Consumption

```
https://github.com/alielsokary/CaskKit/releases/download/cask-<token>/AppIcon.png
```

256×256 PNG, one release per cask, published once (icons are not re-extracted
on version bumps). **Icon releases are always pre-releases** — this keeps
`releases/latest` owned by the `data-*` releases that CaskHub's
`CategoryService` depends on. Never publish a `cask-*` tag as a full release.

## Protocol (per cask)

1. **Eligibility** — skip `deprecated`/`disabled`; require an `app`, `suite`,
   or `pkg` artifact (pkg payloads contain the `.app`).
2. **Download** the cask `url` via curl, honoring `url_specs` (user-agent,
   referer, cookies, headers). Verify `sha256` unless `no_check`.
3. **Expand without executing anything**:
   dmg → `hdiutil attach -nobrowse -readonly`, zip → `ditto -xk`,
   tar → `tar -xf`, pkg → `pkgutil --expand-full` (no install scripts run).
   One level of nested-container recursion (dmg-in-zip etc.).
4. **Locate the `.app`** named by the artifacts (fallback: single/shallowest
   `.app`; symlinks never followed — DMGs ship an `/Applications` link).
5. **Icon**: `CFBundleIconFile` from `Info.plist` → `.icns` → 256px PNG via
   `sips`. No `.icns` but `CFBundleIconName` → recorded `car_only`, skipped (v1).
6. Publish the pre-release, record the outcome, delete the artifact.

## State

- **Done** = the `cask-*` releases themselves (no separate list to drift).
- `data/icon_report.json` records `no_icon` / `car_only` / `failed` with
  reasons. Failures retry up to 3 runs, then park. `--tokens` bypasses parking
  for manual retries.
- Backfill order: 30-day install counts from the brew analytics API, so the
  most-installed apps get icons first.

## Running

```sh
# Local, no publishing — PNGs land in icons_out/
python3 scripts/extract_icons.py --tokens obsidian rectangle

# Batch with publishing (CI does this via extract-icons.yml workflow_dispatch)
python3 scripts/extract_icons.py --publish --limit 50
```

Stdlib + stock macOS tooling only — no extra dependencies.
