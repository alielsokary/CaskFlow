# CaskHub Categorization v2 - Review Summary

**Date:** 2026-04-12  
**Total casks:** 3763  
**Categories:** 15  
**Entries with secondary categories:** 97  

## Category Distribution

| Category | Count | % | Change from v1 |
|----------|-------|---|----------------|
| Utilities | 692 | 18.4% | +165 |
| Developer Tools | 578 | 15.4% | +157 |
| Productivity | 412 | 10.9% | +45 |
| Design & Graphics | 331 | 8.8% | +38 |
| Audio & Music | 290 | 7.7% | +41 |
| Games | 281 | 7.5% | +47 |
| Video & Media | 232 | 6.2% | +25 |
| Security & Privacy | 192 | 5.1% | +42 |
| Cloud & Storage | 187 | 5.0% | +6 |
| Communication | 147 | 3.9% | +25 |
| Science & Education | 141 | 3.7% | +47 |
| Finance & Crypto | 98 | 2.6% | +7 |
| Browsers | 88 | 2.3% | +1 |
| Menu Bar | 69 | 1.8% | -1 |
| Other | 25 | 0.7% | -650 |

## Changes from v1

- **Reclassified from Other:** 652 apps
- **Category corrections:** 30 apps
- **Secondary categories added:** 97 apps

### Notable Category Corrections

| App | Was | Now | Description |
|-----|-----|-----|-------------|
| 4k-image-compressor | Video & Media | Utilities | Image compressor |
| 4k-youtube-to-mp3 | Video & Media | Audio & Music | Turn YouTube links into MP3 files |
| 8x8-work | Video & Media | Communication | Communications application with voice, video, chat, and web  |
| a-better-finder-rename | Audio & Music | Utilities | Renamer for files, music and photos |
| ace-link | Video & Media | Menu Bar | Menu bar app for playing Ace Stream video streams in an exte |
| acreom | Developer Tools | Productivity | Personal knowledge base for developers |
| activedock | Productivity | Utilities | Customizable dock, application launcher, dock replacement |
| adapter | Audio & Music | Video & Media | Converts video, audio and images |
| adobe-acrobat-pro | Design & Graphics | Productivity | View, create, manipulate, print and manage files in Portable |
| adobe-acrobat-reader | Design & Graphics | Productivity | View, print, and comment on PDF documents |
| adobe-air | Design & Graphics | Developer Tools | Framework used in the development of applications and games |
| adobe-connect | Design & Graphics | Communication | Virtual meeting client |
| adobe-creative-cloud-cleaner-tool | Design & Graphics | Utilities | Utility to clean up corrupted installations of Adobe softwar |
| adobe-digital-editions | Design & Graphics | Productivity | E-book reader |
| aerial | Video & Media | Utilities | Apple TV Aerial screensaver |
| ai-studio | Science & Education | Developer Tools | Data science platform |
| aigcpanel | Audio & Music | Developer Tools | AI video, audio and broadcast generator |
| airbuddy | Menu Bar | Utilities | AirPods companion app |
| aircall | Utilities | Communication | Cloud-based call center and phone system software |
| airdash | Cloud & Storage | Utilities | Transfer photos and files to any device |
| boom-3d | Design & Graphics | Audio & Music | Volume booster and equaliser software |
| bot-framework-emulator | Games | Developer Tools | Test and debug chat bots built with the Bot Framework SDK |
| elgato-camera-hub | Video & Media | Other | Elgato FACECAM configuration tool |
| elgato-capture-device-utility | Video & Media | Other | Update and configure Elgato Capture devices |
| elgato-control-center | Video & Media | Other | Control your Elgato key lights |
| exifrenamer | Audio & Music | Utilities | Tool to rename digital photos, movie- and audio-clips |
| filebot | Audio & Music | Utilities | Tool for organising and renaming movies, TV shows, anime or  |
| raycast | Productivity | Utilities | Control your tools with a few keystrokes |
| rode-central | Utilities | Audio & Music | RØDE companion app |
| topaz-video | Design & Graphics | Video & Media | Video upscaler and quality enhancer |

### Remaining Other (25 apps)

These are hardware config tools and MDM clients that genuinely do not fit standard categories:

- **bose-updater**: Software updates for Bose products
- **brickstore**: BrickLink offline management tool
- **cardo-update**: Update Packtalk and Freecom motorcycle intercoms
- **creative**: Control panel for the Creative hardware
- **dymo-connect**: Software for DYMO LabelWriters
- **elgato-camera-hub**: Elgato FACECAM configuration tool
- **elgato-capture-device-utility**: Update and configure Elgato Capture devices
- **elgato-control-center**: Control your Elgato key lights
- **flexoptix**: Connect to your FLEXBOX without cables and configure transceivers
- **fujitsu-scansnap-home**: Fujitsu ScanSnap Scanner software
- **galaxybudsclient**: Unofficial manager for the Buds, Buds+, Buds Live and Buds Pro
- **garmin-express**: Update maps and software, sync with Garmin Connect and register your device
- **glkvm**: App for controlling GL.iNet KVM devices
- **hp-easy-start**: Set up your HP printer
- **intune-company-portal**: App to manage access to corporate apps, data, and resources
- **jabra-direct**: Optimise and personalise your Jabra headset
- **lazycat**: Client for LazyCat hardware
- **moose**: no desc
- **nanoleaf**: Control your Nanoleaf lights
- **papercut-mobility-print-client**: Client for printing to PaperCut Mobility Print queues
- **pcoipclient**: Client for VM agents and remote workstation cards
- **rippling**: MDM for Rippling
- **rouvy**: Indoor cycling and workout app
- **setapp**: Collection of apps available by subscription
- **workspace-one-intelligent-hub**: VMware workspace

## New Category Recommendation

Based on analysis, **no new categories are recommended at this time.** The current 15 categories provide excellent coverage with only 25 apps (0.7%) in Other.

AI/ML (62 apps) was considered as a new category, but these apps are well-distributed across Developer Tools, Productivity, and Design with secondary tags capturing the AI aspect.

## Schema Changes (v1 to v2)

```json
// v1: "token": "categoryId"
// v2: "token": {"primary": "categoryId", "secondary": ["categoryId2"]}
```