# Known Limitations

A short version of this lives in the [README](../README.md#known-limitations). This is the detailed version — read it before filing a bug, and please still file it if you hit something not listed here.

## Platform

- **Windows only.** Pulse automates the desktop through Windows UI Automation. There's no macOS or Linux support, and no near-term plan for it (see [Roadmap](../README.md#roadmap)).
- **No packaged installer yet.** You currently run Pulse from source — see [`docs/INSTALLATION.md`](INSTALLATION.md). A one-click Windows installer is planned.

## Model quality and behavior

- **Planning runs on a small, local model, not a frontier cloud model.** This is a deliberate trade-off for speed and privacy, and it means Pulse will occasionally misjudge a step — click a plausible-but-wrong control, or misread what's on screen. Pulse is built with several layers to *notice and correct* this live (see [Architecture → Command Processing Flow](ARCHITECTURE.md#command-processing-flow)), but it isn't infallible.
- **The vision fallback (`look_at_screen`) is a real capability, not a perfect one.** It can miss small, low-contrast, or unusually-styled on-screen elements — the same class of mistake any vision model can make. When it can't find something with high confidence, Pulse falls back to its text-based reasoning rather than guessing wildly.

## Application compatibility

- **Apps with unusual save/session behavior are a known hard case.** Specifically:
  - Apps that **resume a previous session** automatically (Word, and Windows 11 apps generally) instead of starting genuinely blank — Pulse checks whether a freshly-opened document actually matches what you asked for before writing to it, but this check is heuristic, not perfect, especially for phrasing it hasn't seen.
  - Apps that **only autosave to their own cloud storage** rather than using a traditional Save-As dialog — Pulse's save flow is built around the standard Windows save dialog; apps that skip it entirely can leave Pulse unable to confirm exactly where a file landed.
  - Trial/preview editions of apps that **restrict functionality** (e.g. a "buy now" nag that blocks creating new documents) can produce behavior Pulse can correctly diagnose but not work around, since the restriction is real, not a UI-reading mistake.
- **Apps with heavily custom or non-standard UI** (games, some Electron apps, canvas-rendered interfaces) may expose little or nothing to UI Automation, which limits what Pulse's text-based reading can do — `look_at_screen` helps here but isn't a complete substitute for a well-behaved accessibility tree.

## Safety and scope

- **No sandboxing.** Pulse acts on your real desktop with real effects — it can click, type, and save files for real, through the same APIs a screen reader uses. Sensitive/irreversible actions require confirmation, but you're trusting Pulse with real desktop access when you use it.
- **No automated test suite yet.** `tests/` holds standalone scripts, not a CI-runnable suite — see [`CONTRIBUTING.md`](../CONTRIBUTING.md#testing). This means regressions are more likely to slip through than in a project with full test coverage; live testing against real apps has been the primary validation method so far.

## Reporting something not listed here

[Open an issue](https://github.com/aman2003s/pulse-ai/issues/new/choose) with the bug report template — the more concrete (exact phrasing used, exact app, log excerpt from `%APPDATA%\Pulse\pulse.log`), the faster it can be root-caused.
