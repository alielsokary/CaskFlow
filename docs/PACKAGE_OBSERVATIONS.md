# Package installation observations

The **Observe package installations** workflow is a finite manual pilot. It
installs selected Homebrew package casks on disposable macOS runners and saves
what actually appeared. It does not modify the icons branch, identity manifests,
categories, releases, CaskHub, or any GitHub schedule.

## Run the pilot

The workflow must first be merged through `develop` into the default branch
(`master`) before GitHub can dispatch this new manual workflow.

In Actions, choose **Observe package installations**, then **Run workflow**.
Leave `tokens` empty for all ten pilot casks. `architectures` defaults to `both`:
`macos-15` (Apple Silicon) and `macos-15-intel` (Intel). There are at most 20 jobs,
with four active at once and one cask per fresh VM. Selecting a subset is useful
for diagnosing a failure without repeating the full pilot.

```sh
gh workflow run observe-packages.yml --repo alielsokary/CaskFlow \
  -f architectures=both -f tokens='airtool zoom'
```

The fixed pilot covers `airtool`, `microsoft-teams`,
`microsoft-office-businesspro`, `onedrive`, `zoom`, `zoom-for-it-admins`,
`adobe-acrobat-reader`, `openvpn-connect`, `wacom-tablet`, and `blackhole-2ch`.
Arbitrary tokens and duplicate tokens are rejected.

## Isolation and provenance

The planning job resolves the current stable Homebrew release and cask tap to
immutable commits, shared by every job. It also saves the passive identity
manifest from one exact icons-branch commit. Each observation records its actual
OS, architecture, runner image, workflow source, cask definition, resolved source
URL/version, expected checksum and observed artifact hash.

The collector fetches and installs the pinned official cask through Homebrew,
including its installer choices and pre/postflight behavior. It requires a
declared SHA-256 checksum and verifies the cached download again before install.
There is no checksum bypass, forced installation, uninstall cleanup, or publication.

The initial pilot requires dependency-free casks. If Homebrew reports a dependency
(including an implicit extraction dependency), the job stops before installation.
Expanding that scope requires installing and recording prerequisites before the
target baseline. The job also stops when a matching receipt, known target app, or
Homebrew-managed target already exists. It does not delete preinstalled software
to manufacture a clean baseline.

Observation jobs have read-only repository permissions, no publishing credentials,
no repository secrets and no persisted checkout credentials. Results remain
untrusted data; the report job parses JSON and never executes downloaded content.
The collector refuses installation execution outside a matching GitHub-hosted
macOS runner. Do not spoof those guards to run it on a personal Mac.

## Evidence and outcomes

Artifacts are retained for 30 days:

- `package-observation-plan`: selected jobs, immutable revisions and passive baseline.
- `package-observation-<token>-<architecture>`: command records, complete stdout and
  stderr, cask source, before/after snapshots and `observation.json`.
- `package-pilot-report`: `summary.json` and a Markdown outcome table, also shown
  in the Actions job summary.

The collector scans `/Applications` and `~/Applications`, including nested suites
and flat bundles but excluding embedded helper apps and symlinked app directories.
It records bundle IDs, plist and executable hashes, versions, and changed receipts.
For observed bundles it captures `codesign --display` output; this is signing
information, not a claim that signature validity or product ownership was proved.
For changed receipts it preserves native `pkgutil` metadata and file lists.

Post-install snapshots are sampled every 15 seconds for at most 120 seconds,
requiring three consecutive unchanged intervals before accepting a settled result.
Every sample is retained. `settling.json` records exact changed fields, the old
and new records, sample times, and whether any mutation happened after installation.
Later stability does not erase evidence that an updater replaced the original
package's files. This bounded check cannot prove that a delayed updater will never run.
`no_application_observed` means no new or changed application was found in the
scanned roots during this observation; it is not a claim about every install path
or configuration. A nonzero installer exit, timeout, unreadable evidence, missing
Homebrew registration, or unstable snapshots cannot become a successful empty
result. Changed pre-existing software is reported separately.

Native `InstallHistory.plist` copies before and after installation preserve
transaction-level receipt groups for investigation. Missing or unreadable history
is recorded explicitly as supplementary evidence; history does not itself prove
current application ownership. It is collected only inside the disposable runner.

Raw JSON evidence preserves exact filenames and identifiers using ASCII JSON
escapes; typography normalization is only appropriate for authored report prose.
Download artifacts before their retention expires if they are needed for longer
investigations. Failed jobs do not cancel sibling jobs; the report includes missing
and interrupted evidence instead of silently omitting it.

## Interpreting the result

The report compares observed identities with the frozen passive manifest and
lists identities shared with other casks, including partial observations whose
source status and evidence quality remain explicit. Diagnostic codes explain
incomplete results, including artifacts from the original two-snapshot pilot.
A new observed identity is not necessarily
the primary product; it can be an updater or shared component. A passive identity
not observed can be optional, version-dependent, or unsupported in that environment.
Neither difference is automatically published as a correction.

Every result has `ownershipVerified: false`. Installing Teams and Office in CI
can show that both contain Teams; it does not prove that an existing Teams app on
a user's Mac belongs to Office. CaskHub's managed-owner precedence, exact local
validation, ambiguity handling and operation checks remain necessary.

Before using observations in a production mapping, review platform differences,
shared components, failures, missing evidence and the corresponding CaskHub
consumer behavior. No production schema or consumer change is part of this pilot.

## Validation

```sh
pytest tests/test_package_observations.py
pytest --cov=scripts --cov-report=term-missing
actionlint .github/workflows/observe-packages.yml
```

Tests exercise the collector entrypoint with substituted process/filesystem seams,
real temporary bundle/receipt plists, subprocess timeout handling, and cross-cask
reporting. They do not install vendor software on a developer machine. Real CI
installation results are a separate validation step after the workflow is available.
