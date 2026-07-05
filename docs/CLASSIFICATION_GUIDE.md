# CaskHub Category Audit — Claude Code Instructions

## Context
CaskHub is a macOS SwiftUI app for browsing Homebrew Casks. We have 3,763 casks categorized into 15 categories. The initial categorization was done with keyword matching, which produced hundreds of errors. We need to verify and fix every categorization using homepage metadata + your knowledge of each app.

## The 15 Categories
- **developerTools**: IDEs, code editors, version control, API clients, databases, SDKs, terminal emulators, debuggers, CI/CD, containers, build tools
- **browsers**: Web browsers ONLY (Chrome, Firefox, Arc, Brave, etc.) — NOT database browsers, file browsers, or app wrappers
- **communication**: Chat, email, video conferencing, social media clients, messaging, team chat (Slack, Discord, Teams)
- **productivity**: Note-taking, task management, calendars, office suites, writing tools, clipboard managers, text expanders, AI assistants/chatbots, RSS readers, PDF editors, project management
- **utilities**: System utilities, file managers, disk tools, backup, uninstallers, converters, system monitoring, drivers, input methods, remote desktop, download managers, torrent clients, file transfer tools
- **designGraphics**: Image/photo editors, vector tools, 3D modeling, UI/UX design, screenshot annotation, color pickers, icon editors, digital art, CAD
- **audioMusic**: DAWs, music players, audio editors, synthesizers, DJ software, podcast tools, audio plugins (VST/AU), music streaming, music notation, MIDI tools
- **videoMedia**: Video players, video editors, screen recorders, streaming software (OBS), video converters, media servers, webcam tools, subtitle editors
- **games**: Games, game launchers, game engines, emulators, game-related tools
- **securityPrivacy**: VPNs, password managers, encryption, firewalls, antivirus, privacy tools, network security, ad blockers, 2FA/authenticators
- **financeCrypto**: Trading, banking, crypto wallets, accounting, invoicing, tax tools, budgeting, financial planning
- **cloudStorage**: Cloud storage/sync services (Dropbox, Google Drive, iCloud, OneDrive), NAS clients, WebDAV, cloud backup — NOT remote desktop, NOT SSH, NOT download managers
- **scienceEducation**: Scientific tools, calculators, education platforms, language learning, research tools, reference managers, astronomy, chemistry, math, GIS
- **menuBar**: Apps whose PRIMARY purpose is providing a menu bar widget
- **other**: Truly uncategorizable (should have <10 apps)

## Known Systematic Errors to Fix
The keyword-based classifier made these systematic mistakes:
1. **cloudStorage is massively inflated**: Remote desktop apps (AnyDesk, NoMachine, RustDesk), printer drivers, SSH clients, download managers, torrent clients, file transfer tools, hardware drivers were all dumped into cloudStorage. Most should be utilities or developerTools.
2. **browsers contains non-browsers**: App wrappers (Biscuit, Wavebox, SingleBox), developer testing tools (Polypane, Responsively, Blisk), and even a music player (Strawberry) are miscategorized as browsers.
3. **communication contains AI chatbots**: LLM chat apps (ChatBox, BoltAI, AnythingLLM, Poe, etc.) should be in productivity, not communication.
4. **audioMusic contains non-audio apps**: osquery (monitoring), hstracker (Hearthstone tracker), jyutping (input method), waveforms (electronics test equipment), clone-hero (game), sys-pc-tool (paragliding).
5. **games contains game peripherals**: Controller config software (8BitDo) should be utilities.
6. **securityPrivacy contains finance apps**: Crypto wallets, budgeting apps should be financeCrypto.

## Step-by-Step Plan

### Step 1: Fetch all homepages
```bash
cd ~/Desktop/Projects/Side\ Projects/CaskHub
python3 scripts/fetch_homepages.py
```
This fetches metadata for all 3,763 casks in parallel. Takes ~2-3 minutes. Has resume support.

### Step 2: Run the keyword-based audit
```bash
python3 scripts/audit_categories.py
```
This produces `category_corrections.json` and `audit_report.md`.

### Step 3: YOUR CRITICAL TASK — Manual verification with your knowledge
The keyword audit will catch obvious things but miss subtle ones. You need to:

1. Read `categories.json`, `filtered_casks.json`, and `homepage_metadata.json`
2. Go through EVERY category and verify each app belongs there
3. Use this priority for classification evidence:
   - **Your training knowledge** of what the app does (highest priority)
   - **Homepage title + meta description** (high priority)
   - **Cask description** from Homebrew (medium — sometimes wrong/vague)
   - **Token name** (low — often misleading)
   - **Homepage domain** (low — contextual clue only)

4. For each miscategorization, add to the corrections list:
   ```json
   {"token": "app-name", "was": "wrongCategory", "shouldBe": "rightCategory", "confidence": "high", "reason": "explanation"}
   ```

5. Pay special attention to:
   - The entire **cloudStorage** category (~187 apps) — probably 100+ are wrong
   - The entire **browsers** category — check each is a real web browser
   - All **communication** entries — separate real communication from AI chatbots
   - All **audioMusic** entries — check for non-audio apps
   - All **securityPrivacy** entries — check for finance apps miscategorized
   - The **games** category — check for game-adjacent tools that aren't games
   - Apps in **other** — try to categorize them

6. Write the final corrected `category_corrections.json`

### Step 4: Apply corrections
```bash
python3 scripts/apply_corrections.py --dry-run  # Preview
python3 scripts/apply_corrections.py             # Apply high-confidence
```

### Step 5: Generate final report
After applying, write a summary to `audit_report.md` with:
- Total corrections made
- Per-category before/after counts
- List of every change with reason
- Any remaining uncertain items for manual review

## Important Rules
- When in doubt, use utilities (it's the catch-all for system tools)
- An app can only have ONE primary category
- Don't move apps that are borderline — only move CLEAR miscategorizations
- Game controller/peripheral software → utilities (not games)
- AI chatbot/LLM clients → productivity (not communication)
- Cloud CLI tools (gcloud, aws-cli) → developerTools (not cloudStorage)
- SSH/SFTP clients → developerTools (not cloudStorage)
- Remote desktop apps → utilities (not cloudStorage)
- Download managers and torrent clients → utilities (not cloudStorage)
- Digital signature/e-signing tools → productivity (not securityPrivacy)
- Screen recorders → videoMedia (not utilities, unless purely screenshot)
- Calculator apps → scienceEducation (not utilities)
