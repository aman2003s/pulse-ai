# Security Policy

## What "security" means for Pulse

Pulse automates your real Windows desktop through the same UI-Automation APIs a screen reader uses — it can click buttons, type text, and save files. That's inherent to what it does, not a vulnerability by itself. What we do treat as a security issue:

- Pulse taking a **sensitive action without the confirmation it's supposed to require** (deleting something, an irreversible action) due to a bug, not user configuration.
- **Local model prompt injection** that gets Pulse to do something the user never asked for — e.g. text on screen or in a file that Pulse reads gets interpreted as a new instruction rather than data.
- **Any network call Pulse makes that isn't the one the user explicitly asked for** (Pulse is meant to run local-first; see the [Privacy](README.md#privacy) section — an unexpected outbound request is a bug at minimum, possibly a security issue).
- Anything that lets code execute or files get written **outside of what the user's request and confirmation actually authorized.**

## Reporting a vulnerability

**Please do not open a public GitHub issue for a security vulnerability.**

Instead, use GitHub's private vulnerability reporting for this repository (`Security` tab → `Report a vulnerability`), or message the maintainer directly on GitHub ([@aman2003s](https://github.com/aman2003s)). Include:

- What you did (the exact command/request given to Pulse).
- What happened, ideally with the relevant log lines (`%APPDATA%\Pulse\pulse.log`).
- Why you believe it's a security issue rather than a functional bug.

We'll acknowledge reports as quickly as we can and work with you on a fix before any public disclosure. This is a small, early-stage project — please be patient, but reports won't be ignored.

## Supported versions

Pulse is pre-1.0 and does not yet maintain multiple supported release branches. Security fixes land on `main`; please run the latest version.
