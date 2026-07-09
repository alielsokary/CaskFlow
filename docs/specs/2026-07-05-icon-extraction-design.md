# Icon Extraction Pipeline — Design

**Date:** 2026-07-05
**Status:** Approved design, pre-implementation
**Repo:** CaskKit (owns *what each cask is*; icons are cask identity data)

## Goal

Display the **original app icon** for every cask in CaskHub — no generated fallbacks (GitHub avatars, favicons). CaskKit downloads each cask's artifact, extracts the icon from the `.app` bundle, and hosts it as a per-cask GitHub release, replacing the stale App-Fair/appcasks source (~74% coverage, pre-2022 only).

Honest coverage target: **100% of extractable apps** (expected asymptote 90–95% of app casks). Some casks are permanently unextractable (paywalled installers, dead URLs, anti-bot walls). The gap must be visible and named in `data/icon_report.json`, never silent.

## Non-goals (v1)

- `Assets.car` parsing. Measured on a current-macOS machine: 94 of 96 apps in /Applications ship an `.icns`; only ~2% are car-only. Car-only apps are recorded as `car_only` and skipped; revisit only if the report count justifies it.
- Re-extraction on version bumps. Icons rarely change; extract once per cask.
- Non-app casks (fonts, CLI binaries, QuickLook plugins, screensavers) — **not supported for now**. The pipeline records them as `no_icon` with a reason and skips them entirely.

## Hosting / release scheme

- One GitHub release per cask: tag `cask-<token>`, single asset `AppIcon.png` (256×256).
- Consumption URL (byte-identical shape to App-Fair):
  `https://github.com/alielsokary/CaskKit/releases/download/cask-<token>/AppIcon.png`
- **Every icon release is marked pre-release.** GitHub excludes pre-releases from `releases/latest`, which CaskHub's `CategoryService` depends on for `categories.json` (`releases/latest/download/categories.json`). A full icon release would hijack `latest` and break category updates. This flag is load-bearing.
- Release creation is throttled to stay under GitHub secondary rate limits; the safe rate is discovered during the CI-subset phase.

## Data source (verified live 2026-07-05)

Public formulae.brew.sh v2 API — no public v3 exists; endpoints CaskHub already uses are current.

Per-cask fields the pipeline uses:

| Field | Use |
|---|---|
| `url` | artifact download URL (current version) |
| `url_specs` | curl quirks (user-agent, referer, cookies) — replicate Homebrew's downloader to rescue long-tail failures |
| `sha256` | verify download before expanding; `"no_check"` (auto-updating casks) is allowed through |
| `artifacts` | detect `app`/`suite` entries → is there an icon to extract, and which `.app` |
| `variations` | per-arch URL overrides; use the variant matching the extraction machine's arch |
| `deprecated` / `disabled` | skip |
| `analytics` | install counts → backfill priority order |

## Extraction protocol (per cask)

1. Skip if `deprecated`/`disabled`, or `artifacts` contains no `app`/`suite` → record `no_icon` with reason.
2. Download `url` honoring `url_specs`; verify `sha256` unless `no_check`.
3. Expand **without executing anything**:
   - DMG: `hdiutil attach -nobrowse -readonly`
   - zip: `ditto -xk`
   - tar/gz/bz2/xz: `tar`
   - pkg: `pkgutil --expand-full` (extracts payload; runs no install scripts)
   - Nested containers (dmg-in-zip etc.): one level of recursion, guided by `container` field.
4. Locate the `.app` named in `artifacts`; read `CFBundleIconFile` from `Info.plist`; convert the `.icns` → 256px PNG via `sips`. If `CFBundleIconFile` is absent, fall back to any single `.icns` in `Contents/Resources/`.
5. No `.icns` but `CFBundleIconName` present → record `car_only`, skip (v1).
6. Publish the pre-release, append outcome to `data/icon_report.json`, detach/delete artifacts (disk hygiene — some artifacts are multi-GB).

All stock macOS tooling orchestrated by Python, matching the existing `scripts/` style. Documented for humans as `docs/ICON_EXTRACTION.md` during implementation.

## Pipeline & state

- `scripts/extract_icons.py` + workflow `.github/workflows/extract-icons.yml` on `macos-latest`, daily cron, independent of the classification workflow (which stays on ubuntu).
- **"Done" = the `cask-*` releases themselves** (one API page-through). No separate done-list to drift.
- `data/icon_report.json` (committed) records `no_icon` / `car_only` / `failed` tokens with reasons. Failed tokens get 3 retries on subsequent runs, then are parked — the pipeline must converge to near-zero weekly maintenance, not chase unwinnable casks forever.
- Batch cap per CI run (default 50 casks) to bound runner time and be polite to vendor servers.

## Rollout

1. **Local subset** (~20 casks covering dmg / zip / pkg / tar / nested container, plus a non-app cask to verify the skip path) → verify PNGs by eye.
2. **CI subset**: same list through `extract-icons.yml` via `workflow_dispatch`, publishing real pre-releases. Discovers the safe release-creation rate.
3. **Backfill**: run locally in batches (total vendor download likely 100s of GB — friendlier from a home connection than CI), ordered by `analytics` install count so the most-seen icons land first. Publish via `gh release create --prerelease`.
4. Enable the daily cron for new casks only.

## CaskHub follow-up (separate PR, after coverage ramps)

`CaskIconURL` chain becomes: CaskKit release → App-Fair release (also original icons) → placeholder. Delete the GitHub-avatar and favicon fallbacks — that is the "no generated icons" end state. Keeping a fallback chain also degrades gracefully if an icon release is ever DMCA'd.

## Risks

- **Long-tail download failures** (est. 5–15% first pass; unknown until backfill). Mitigated by `url_specs`, retries, and the report making the gap explicit.
- **GitHub secondary rate limits** on bulk release creation. Throttle; calibrate in phase 2.
- **Copyright**: publicly rehosting original app icons. Identification/nominative use, tolerated in practice (App-Fair ran for years), but takedowns are possible — fallback chain in CaskHub absorbs them.
- **Maintenance decay** (what killed appcasks). Countered by park-after-N-retries, skip lists, and cron-only deltas — weekly human effort must stay near zero.

---

## Amendment 2026-07-06 — hosting moved from per-cask releases to the `icons` branch

Phase 1+2 validated the extraction protocol unchanged, but hosting switched
before backfill (the last cheap moment to change):

- **Was:** one pre-release per cask (`cask-<token>` / `AppIcon.png`), App-Fair's
  scheme. Feasible (App-Fair proved it at scale) but: per-icon API publishes with
  rate-limit throttling, paginated done-checks, a cluttered releases page, and a
  permanently load-bearing `--prerelease` flag protecting `releases/latest`.
- **Now:** `<token>.png` on the orphan `icons` branch, batch-committed (~25/commit)
  via a throwaway worktree. Served by jsDelivr's edge CDN
  (`cdn.jsdelivr.net/gh/alielsokary/CaskKit@icons/<token>.png`, verified live:
  Cloudflare edge, `max-age=604800`, 20MB/file limit vs our ~50KB, permanent S3
  mirror) with `raw.githubusercontent.com` fallback. Done-list = one `git ls-tree`.
  The pre-release invariant is gone entirely; `releases/*` now belongs solely to
  `data-*`.
- **Added:** `.app` selection mode (`exact`/`single`/`shallowest`) recorded per
  icon; non-exact selections land in `data/icon_report.json` as `review` — the
  audit queue for wrong-but-plausible icons, the pipeline's main quality risk.
- Backfill runs on GitHub-hosted macOS runners (free for public repos), not a
  home connection; the 5–15% long-tail failure estimate may skew higher from
  datacenter IPs — stragglers get mopped up locally via `--tokens`.
