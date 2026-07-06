# Icon Extraction

Extracts original app icons from Homebrew cask artifacts and publishes them to
the orphan **`icons` branch**, served through jsDelivr's edge CDN. Full design
rationale: `docs/superpowers/specs/2026-07-05-icon-extraction-design.md`
(hosting amended 2026-07-06 — see the spec's amendment section).

## Consumption

```
https://cdn.jsdelivr.net/gh/alielsokary/CaskKit@icons/<token>.png     (primary — edge CDN)
https://raw.githubusercontent.com/alielsokary/CaskKit/icons/<token>.png   (fallback)
```

256×256 PNG per cask token, published once (icons are not re-extracted on
version bumps). jsDelivr caches branch refs for 12h at the edge and mirrors
served files permanently to its own storage; new icons are visible within
minutes on first request, cached aggressively thereafter.

## Protocol (per cask)

1. **Eligibility** — skip `deprecated`/`disabled`; require an `app`, `suite`,
   or `pkg` artifact (pkg payloads contain the `.app`).
2. **Download** the cask `url` via curl, honoring `url_specs` (user-agent,
   referer, cookies, headers). Verify `sha256` unless `no_check`.
3. **Expand without executing anything**:
   dmg → `hdiutil attach -nobrowse -readonly`, zip → `ditto -xk`,
   tar → `tar -xf`, pkg → `pkgutil --expand-full` (no install scripts run).
   One level of nested-container recursion (dmg-in-zip etc.).
4. **Locate the `.app`** — selection is recorded as an audit signal:
   `exact` (matches the artifact stanza), `single` (only app present), or
   `shallowest` (heuristic — lands in the review queue). Symlinks are never
   followed (DMGs ship an `/Applications` link).
5. **Icon**: `CFBundleIconFile` from `Info.plist` → `.icns` → 256px PNG via
   `sips`. No `.icns` but `CFBundleIconName` → recorded `car_only`, skipped (v1).
6. **Publish**: batch-committed to the `icons` branch (one commit per ~25
   icons via a throwaway git worktree) — no release plumbing, no rate limits.

## State

- **Done** = the files on the `icons` branch (one `git ls-tree`, no pagination).
- `data/icon_report.json` records:
  - `no_icon` / `car_only` — permanently parked with a reason
  - `failed` — retried up to 3 runs, then parked (`--tokens` bypasses parking)
  - `review` — icon published but the `.app` was picked heuristically
    (`single`/`shallowest`); **the human audit queue**. Eyeball these after
    each batch; a wrong-but-plausible icon is worse than a missing one.
- Backfill order: 30-day install counts from the brew analytics API, so the
  most-installed apps get icons first.

## Running

```sh
# Local, no publishing — PNGs land in icons_out/
python3 scripts/extract_icons.py --tokens obsidian rectangle

# Batch with publishing (CI does this via extract-icons.yml workflow_dispatch)
python3 scripts/extract_icons.py --publish --limit 300
```

Backfill runs on GitHub-hosted macOS runners (free for public repos; Azure
bandwidth makes it download-bound-fast, and per-cask cleanup keeps disk flat).
Stdlib + stock macOS tooling only — no extra dependencies.
