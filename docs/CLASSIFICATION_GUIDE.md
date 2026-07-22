# Classification Guide

Category definitions and boundary rules for classifying Homebrew casks in
[`categories.json`](../categories.json). This is the source of truth for both
the LLM system prompt (`scripts/prompts.py`) and human reviewers — if a rule
changes here, the prompt should change with it.

## Taxonomy

There are 17 primary categories and one secondary-only trait:

- **developerTools**: IDEs, code editors, version control, API clients, databases, SDKs, terminal emulators, debuggers, CI/CD, containers, build tools, game engines and game-creation tools
- **browsers**: Web browsers ONLY (Chrome, Firefox, Arc, Brave, etc.) — NOT database browsers, file browsers, or app wrappers
- **communication**: Chat, email, video conferencing, social media clients, messaging, team chat (Slack, Discord, Teams)
- **productivity**: Note-taking, task management, calendars, writing tools, clipboard managers, text expanders, AI assistants/chatbots, RSS readers, PDF editors, project management; full office suites belong in **officeTools**
- **utilities**: System utilities, file managers, disk tools, backup, uninstallers, converters, system monitoring, drivers, input methods, remote desktop, download managers, torrent clients, file transfer tools
- **designGraphics**: Image/photo editors, vector tools, 3D modeling, UI/UX design, screenshot annotation, color pickers, icon editors, digital art, CAD
- **audioMusic**: DAWs, music players, audio editors, synthesizers, DJ software, podcast tools, audio plugins (VST/AU), music streaming, music notation, MIDI tools
- **videoMedia**: Video players, video editors, screen recorders, streaming software (OBS), video converters, media servers, webcam tools, subtitle editors
- **games**: Games, game launchers, console emulators, and tools whose main purpose is playing or managing games; game engines belong in **developerTools**
- **securityPrivacy**: VPNs, password managers, encryption, firewalls, antivirus, privacy tools, network security, ad blockers, 2FA/authenticators
- **financeCrypto**: Trading, banking, crypto wallets, accounting, invoicing, tax tools, budgeting, financial planning
- **cloudStorage**: Cloud storage/sync services (Dropbox, Google Drive, iCloud, OneDrive), NAS clients, WebDAV, cloud backup — NOT remote desktop, NOT SSH, NOT download managers
- **scienceEducation**: Scientific tools, calculators, education platforms, language learning, research tools, reference managers, astronomy, chemistry, math, GIS
- **menuBar**: Apps whose PRIMARY purpose is providing a menu bar widget
- **officeTools**: Full office suites and their components, including Microsoft Office, LibreOffice, OnlyOffice, WPS Office, and FreeOffice
- **screensaverWallpaper**: macOS screensavers, dynamic wallpaper, and wallpaper-management applications
- **other**: A last resort for applications without a defensible fit elsewhere

The **ai** category is a trait, never a primary category. Add it as a secondary category when AI or an LLM is central to the application’s value.

## Classification Evidence

When deciding a category, weigh evidence in this order:

1. **Knowledge of what the app actually does** (highest priority)
2. **Homepage title + meta description**
3. **Cask description** from Homebrew (sometimes wrong or vague)
4. **Token name** (often misleading)
5. **Homepage domain** (contextual clue only)

## Boundary Rules

- An app has exactly one primary category and zero to two distinct secondary categories.
- A primary category must not also appear as a secondary category.
- When in doubt, use **utilities** — it's the catch-all for system tools.
- Only move an app when the miscategorization is CLEAR; borderline apps stay put.
- Game controller/peripheral software → **utilities** (not games)
- Game engines and game-creation tools → **developerTools** (not games)
- AI chatbot/LLM clients → **productivity** (not communication)
- Cloud CLI tools (gcloud, aws-cli) → **developerTools** (not cloudStorage)
- SSH/SFTP clients → **developerTools** (not cloudStorage)
- Remote desktop apps → **utilities** (not cloudStorage)
- Download managers and torrent clients → **utilities** (not cloudStorage)
- Digital signature/e-signing tools → **productivity** (not securityPrivacy)
- Crypto wallets and budgeting apps → **financeCrypto** (not securityPrivacy)
- Screen recorders → **videoMedia** (not utilities, unless purely screenshot)
- Calculator apps → **scienceEducation** (not utilities)
- App wrappers and browser-based dev testing tools are NOT **browsers**.

## Fixing a Misclassification

Spotted a cask in the wrong category?

1. Edit its entry in [`categories.json`](../categories.json) (data only — don't touch the `version` field).
2. Run the tests to validate the full mapping.
3. Open a PR stating what the app actually does and which boundary rule applies.

If the fix reveals a gap in the rules above, update this guide (and
`scripts/prompts.py`) in the same PR so the daily classifier stops repeating
the mistake.
