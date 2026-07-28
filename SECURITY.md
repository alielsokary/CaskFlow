# Security Policy

CaskFlow is a free, open-source data pipeline that turns the Homebrew cask catalog into reviewed metadata and icon assets consumed by [CaskHub](https://github.com/alielsokary/CaskHub). The integrity of the published assets is the core security concern of this project, and we appreciate the work of security researchers who report issues responsibly.

## Supported Versions

CaskFlow follows a rolling release model: only the [latest release](https://github.com/alielsokary/CaskFlow/releases/latest) assets and the latest commit on `master` are supported. Security fixes ship in a new release rather than being backported.

| Version                                                                   | Supported |
| ------------------------------------------------------------------------- | --------- |
| [Latest release](https://github.com/alielsokary/CaskFlow/releases/latest) | ✅        |
| Latest commit on `master`                                                 | ✅        |
| Older releases                                                            | ❌        |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Report them privately through GitHub's private vulnerability reporting:

**[Open a security advisory](https://github.com/alielsokary/CaskFlow/security/advisories/new)**

Please include as much of the following as you can:

- A description of the vulnerability and its impact
- The affected script, workflow, or published asset
- Step-by-step instructions to reproduce the issue
- Any proof-of-concept code or crafted input
- A suggested mitigation, if you have one

### What to expect

CaskFlow is maintained by a single developer in their spare time, so please allow a little slack on timelines:

- Your report will be acknowledged within **7 days**.
- You will receive updates as the issue is triaged and fixed.
- Confirmed vulnerabilities will be fixed as quickly as severity demands.

### Disclosure policy

We follow coordinated disclosure. Please allow a reasonable amount of time (up to 90 days) for a fix to be released before disclosing the issue publicly. Once fixed, an advisory will be published and you will be credited for the discovery unless you prefer to remain anonymous.

There is no bug bounty program — CaskFlow is free software with no revenue — but reporters are credited in the advisory and release notes.

## Scope

### In scope

- The pipeline code in this repository (`scripts/` and the GitHub Actions workflows)
- The integrity of published assets: `categories.json`, `added_dates.json`, release artifacts, and the [`icons` branch](https://github.com/alielsokary/CaskFlow/tree/icons)

Examples of in-scope issues:

- Tampering with published assets or releases through the project's CI
- Escaping the [icon extraction](docs/ICON_EXTRACTION.md) safety protocol — e.g. archive path traversal, bypassing checksum verification, or causing vendor installer code to execute during extraction
- Injecting malicious content into published data past human review — e.g. crafted cask metadata or homepage content that abuses the high-confidence auto-merge classification path
- Leaking CI secrets (LLM provider keys, tokens) through workflows or logs
- Code execution in the pipeline via attacker-controlled inputs (cask metadata, homepages, vendor artifacts)

### Out of scope

- **Homebrew itself.** Vulnerabilities in Homebrew or its cask catalog belong to [Homebrew's security policy](https://github.com/Homebrew/brew/security/policy).
- **Malicious or vulnerable upstream apps.** CaskFlow classifies third-party software; the behavior of those apps is not a CaskFlow vulnerability. (A vendor artifact that exploits the extraction pipeline itself *is* in scope.)
- **Misclassification without security impact.** Wrong categories or dates are quality bugs — see the [classification guide](docs/CLASSIFICATION_GUIDE.md) and file a regular [issue](https://github.com/alielsokary/CaskFlow/issues).
- **The CaskHub app.** Issues in the consumer belong to [CaskHub's security policy](https://github.com/alielsokary/CaskHub/security/policy).
- **Attacks requiring local control** of the machine running the scripts, or of the repository/CI credentials themselves.
- **Availability of third-party services** the pipeline relies on (GitHub, Homebrew's API, LLM providers).

Thank you for helping keep CaskFlow, CaskHub, and their users safe!
