# CaskHub Category Audit Report

**Date**: 2026-04-13
**Total casks**: 3,763
**Total corrections applied**: 350
**Confidence level**: All high-confidence (manually verified)

## Category Distribution: Before vs After

| Category | Before | After | Change |
|----------|--------|-------|--------|
| Utilities | 717 | 883 | +166 |
| Developer Tools | 589 | 650 | +61 |
| Productivity | 417 | 477 | +60 |
| Design & Graphics | 331 | 337 | +6 |
| Audio & Music | 290 | 260 | -30 |
| Video & Media | 240 | 249 | +9 |
| Games | 281 | 183 | -98 |
| Security & Privacy | 196 | 187 | -9 |
| Science & Education | 142 | 138 | -4 |
| Communication | 148 | 125 | -23 |
| Finance & Crypto | 100 | 94 | -6 |
| Menu Bar | 70 | 72 | +2 |
| Cloud & Storage | 188 | 62 | -126 |
| Browsers | 49 | 45 | -4 |
| Other | 5 | 1 | -4 |

## Methodology

1. **Homepage metadata fetch**: Fetched title, meta description, and og:description for 3,612 of 3,763 casks (96% coverage)
2. **Keyword-based audit**: Ran automated keyword matching to identify initial suspects
3. **Manual verification**: Every app in every flagged category was individually verified using:
   - Training knowledge of what each app does (highest priority)
   - Homepage title and meta description (high priority)
   - Cask description from Homebrew (medium priority)
   - Token name and domain (low priority)

## Corrections by Source Category

### From Cloud & Storage (129 corrections)

The most heavily impacted category. The keyword classifier treated any app with "sync", "remote", "transfer", "driver", or "cloud" in its name/description as cloud storage. The actual cloud storage apps remaining (62) are legitimate services like Dropbox, Google Drive, iCloud, Nextcloud, etc.

**Moved to Utilities (74)**: Remote desktop apps (AnyDesk, NoMachine, RustDesk, TeamViewer, Splashtop, Jump Desktop, etc.), printer drivers (HP, Canon, Konica, Toshiba), USB/serial drivers (FTDI, Silicon Labs, WCH, Prolific), backup tools (Carbon Copy Cloner, SuperDuper, Vorta, Kopia), download managers (JDownloader, Motrix, Folx, Free Download Manager), file transfer tools (LocalSend, FlyingCarpet, Send Anywhere), BitTorrent clients (BiglyBT, Transmission), file managers (ForkLift, Spacedrive), and other system utilities.

**Moved to Developer Tools (22)**: SSH/SFTP clients (Termius, PortX, Electerm, FinalShell, Shell360), cloud CLIs (gcloud-cli, ibm-cloud-cli, yandex-cloud-cli), WordPress dev tools (DevKinsta, Local, WordPressCom Studio), version control (PlasticSCM), VM tools (Anka Build), and server software (CrushFTP, Transfer, Trivial).

**Moved to Communication (4)**: Cloud-PBX, CoScreen, Nextcloud Talk, Synology Chat.

**Moved to Other Categories (29)**: MEGA (molecular evolution -> Science), Syncroom (music session -> Audio), design tools (Scrapp, Thangs-sync -> Design), security tools (NordLocker, Synology Surveillance -> Security), and more.

### From Games (103 corrections)

The keyword classifier placed many non-game apps in games due to shared terms like "emulator", "engine", "controller", "launcher", or being on gaming-adjacent domains.

**Moved to Developer Tools (29)**: JDK distributions (Corretto, GraalVM, Temurin, SapMachine, Microsoft OpenJDK, Zulu), terminal emulators (Archipelago, Contour, Termora, Wave, ZOC), Unity engine tools (Hub + 4 platform supports), game dev tools (TexturePacker, Tiled), and other dev tools (Captain, Expo Orbit, F-Bar, AutoDMG, ObjectiveSharpie, SwiftDialog, Asset Catalog Tinkerer).

**Moved to Utilities (45)**: Game peripheral software (8BitDo x2, ASTRO, SteelSeries GG), system utilities (BlueHarvest, Bluesnooze, Caffeine clones, Monolingual, Swish), OCR tools (TextSniper, TextGrabber2), file tools (File Juicer, Pacifist), remote desktop (Parsec, Splashtop, JollysFastVNC), downloaders (YouTube, macOS tools), and miscellaneous.

**Moved to Science & Education (8)**: Calculator emulators (CEmu, HP Prime, TI SmartView), Scratch (educational programming), scientific tools (SuperMJOGraph, wxMacMolPlt, KLatexFormula, Panoply).

**Moved to Productivity (10)**: Deckset, iA Presenter, Mem, MonoFocus, Nessie, Niftyman, Output Factory, Readwise iBooks, Sync-my-L2P.

**Moved to Other (11)**: VideoMedia (FxFactory, Recut, SnapMotion), DesignGraphics (StarNet++, StarNet2), AudioMusic (VB-Cable, MIDI Router, Ultimate Vocal Remover), Finance (CashNotify, Profit), Security (Cookie, Google Analytics Opt-Out, HMA VPN), MenuBar (Doll, Radar).

### From Audio & Music (34 corrections)

**Moved to Utilities (17)**: Hardware companions (AnkerWork, Bose Updater, Galaxy Buds Client, Jabra Direct, Toneprint, Creative), noise tools (Krisp, RNNoise, Utterly), novelty (Mechvibes, Jazzup, LidAngleSensor), and misc (Jyutping input, MediaInfo, Music Decoy, Musaicfm screensaver, Music Presence).

**Moved to Video & Media (5)**: 5KPlayer, LosslessCut, QCTools, Screenflick.

**Moved to Other (12)**: Games (Clone Hero, HSTracker), Communication (Alfaview, RemoteHamRadio), Productivity (QuickWhisper, ExpressScribe, FocusAtWill, Pimosa), Developer Tools (osquery), Science (ELAN, Waveforms), Utilities (sys-pc-tool, transocks).

### From Communication (31 corrections)

**Moved to Productivity (24)**: AI chatbot clients (AiFun, Alma, AnythingLLM, BoltAI, ChatAll, Chatbox, ChatGLM, ChatWise, DeepChat, Doubao, HuggingChat, LlamaChat, LobeHub, Macai, MindMac, OpenCat, PDF-Pals, Poe, Qianwen), RSS readers (FeedFlow, NetNewsWire), collaboration tools (Mural, Pally), and QQNews.

**Moved to Developer Tools (4)**: ALCOM (VRChat package manager), Komet (git commit editor), Stratoshark (log analyzer), WeChat DevTools.

**Moved to Other (3)**: Emailchemy -> Utilities, GPG Suite No Mail -> Security, Xiaomi Cloud -> Cloud Storage.

### From Security & Privacy (19 corrections)

**Moved to Productivity (7)**: Digital signature tools (AutoFirma, Autogram, GoSign, Podpisuj), note-taking (Beaver Notes, Notesnook, Lunatask), PIA compliance tool.

**Moved to Finance & Crypto (3)**: Actual (budgeting), Anchor Wallet, Moneydance.

**Moved to Developer Tools (3)**: Crescendo, Git Credential Manager, Leapp.

**Moved to Other (6)**: Cloud Storage (oDrive, Proton Drive), Communication (Proton Mail), Utilities (VNC Viewer), Video (Yattee).

### From Finance & Crypto (11 corrections)

**Moved to Productivity (5)**: AliWorkbench, Google Ads Editor, MacDown 3000, Sleek App, Taobao.

**Moved to Security & Privacy (4)**: GPGFrontend, JCrypTool, MailTrackerBlocker, SimplySign.

**Moved to Other (2)**: PicFindr -> Design, Planet -> Dev Tools.

### From Science & Education (15 corrections)

**Moved to Productivity (8)**: Mind mapping tools (MindJet, MindMaster, SimpleMind, TheBrain), note-taking (Reflect, Roam Research, Skim).

**Moved to Utilities (5)**: Geburtstagschecker, iStat Server, IPRemoteUtility, Meteorologist, QWERTY-FR.

**Moved to Other (2)**: Runway -> Design, Swama -> Dev Tools, Navigraph Charts -> Games.

### From Browsers (4 corrections)

**Moved to Developer Tools (3)**: Blisk, Polypane, Responsively (all developer testing tools).

**Moved to Productivity (1)**: Biscuit (web app organizer).

### From Other (4 corrections)

**Moved to Utilities (3)**: Cardo Update, FlexOptix, LazyCat.

**Moved to Games (1)**: Rouvy (indoor cycling game).

## Summary of Systematic Issues Fixed

| Issue | Count | Fix |
|-------|-------|-----|
| Remote desktop apps in Cloud Storage | 23 | -> Utilities |
| Printer/USB/serial drivers in Cloud Storage | 12 | -> Utilities |
| Download managers/torrent clients in Cloud Storage | 11 | -> Utilities |
| SSH/SFTP clients in Cloud Storage | 6 | -> Developer Tools |
| Cloud CLI tools in Cloud Storage | 4 | -> Developer Tools |
| AI chatbots in Communication | 19 | -> Productivity |
| JDK distributions in Games | 6 | -> Developer Tools |
| Terminal emulators in Games | 5 | -> Developer Tools |
| Game peripheral software in Games | 4 | -> Utilities |
| Unity engine tools in Games | 5 | -> Developer Tools |
| Digital signature tools in Security | 4 | -> Productivity |
| Non-audio hardware companions in Audio | 6 | -> Utilities |
| Calculator emulators in Games | 3 | -> Science & Education |

## Remaining in "Other" (1 app)

- **moose**: No clear description available. Token appears to be a generic app at getmoose.in. Unable to classify with confidence.
