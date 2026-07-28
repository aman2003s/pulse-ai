# Changelog

All notable changes to Pulse are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [Semantic Versioning](https://semver.org/) once there's a stable public API to version against; until then, expect breaking changes between minor versions.

See [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) for how entries here should be written going forward.

## [Unreleased]

### Added
- `look_at_screen` and `click_at_position` tools — real screenshot-based visual analysis and coordinate-based clicking, as a fallback when the accessibility tree alone is ambiguous (matches how production computer-use agents ground actions when text alone isn't enough).
- Vision support wired into the local model server (`--mmproj`) so the above tools actually work.
- A code-level "document identity" precondition check before writing into a document — catches apps that resume a stale previous session instead of starting genuinely blank, before Pulse types into the wrong place.
- Open-source release scaffolding: `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue/PR templates, `docs/ARCHITECTURE.md`, and the rest of `docs/`.

### Fixed
- `read_screen` now walks UI Automation's filtered **Control View** (the same view screen readers use) instead of the raw, unfiltered tree — this removes large amounts of decorative browser/app chrome noise that could previously bury or entirely crowd out the real controls a task needed.
- Removed an arbitrary 50-item cap on `read_screen`'s returned control list — it was silently hiding real controls in richly-populated windows (confirmed: a document's own editable area landed past position 100 in one real case).
- Fixed a bug where a coordinate-calculation helper silently discarded every matched on-screen control due to calling library methods as if they were attributes — this was the root cause of `read_screen` intermittently returning completely empty results.
- `scripts/fetch_models.py` no longer depends on a path local to the original developer's machine for the core model — it now downloads from a public source, and also fetches the vision projector.
- `save_file` now matches Word's "File name" field label without requiring an exact colon-suffixed match, fixing save flows on apps that label the field slightly differently than Notepad does.
- Window-title matching for `open_app`/focus now uses word-boundary matching instead of a raw substring check, fixing cases like "word" incorrectly matching a file named `password.txt`.

## [0.1.0] — Initial preview

The foundation this release builds on: voice interaction (wake word, local speech-to-text, local text-to-speech), a local LLM-driven planner, a reason → act → observe → re-plan execution loop with self-correction, a growing tool set for interacting with arbitrary Windows apps, task persistence with pause/resume, and a thin Tauri overlay UI. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full picture of what's here today.

[Unreleased]: https://github.com/aman2003s/pulse-ai/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/aman2003s/pulse-ai/releases/tag/v0.1.0
