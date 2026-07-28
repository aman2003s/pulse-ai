# Release Process

Lightweight by design — this is a small, early-stage project. The goal is that every release is traceable to *why* it happened, not just *what* changed.

## While working (every PR)

- Add an entry under `[Unreleased]` in [`CHANGELOG.md`](../CHANGELOG.md) for any user-facing change — a new capability, a fixed bug, a behavior change. Skip pure internal refactors with no observable effect.
- Write the entry the way this project already writes its own bug fixes (see `FLOW_PLAN.md` for the tone): what was actually broken/missing, and what changed — not just a restatement of the diff. "Fixed X" is weaker than "Fixed X, which was caused by Y, confirmed via Z."
- Categorize under `Added` / `Fixed` / `Changed` / `Removed` / `Security` (standard [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) categories) — skip categories with nothing in them.

## Cutting a release

1. **Move `[Unreleased]` entries under a new version heading** with today's date: `## [0.2.0] — 2026-08-15`.
2. **Pick the version number** (once past `0.1.0`, follow [SemVer](https://semver.org/) intent even pre-1.0): breaking change to the WebSocket API or tool behavior → bump the minor; bug fixes only → bump the patch.
3. **Update the compare links** at the bottom of `CHANGELOG.md`.
4. **Tag it**: `git tag v0.2.0 && git push origin v0.2.0`.
5. **Create a GitHub Release** from that tag, with the changelog section for that version as the release body — this is what most users will actually read, not the raw changelog file.
6. **Bump `version` in `pyproject.toml` and `ui/src-tauri/tauri.conf.json`** to match, in the same PR.

## What belongs in a release, vs. what doesn't

Given the project's own stated approach ("one by one we'll fix, with proper release notes") — prefer smaller, more frequent releases with a clear focus over infrequent large ones. A release note like *"Fixed the session-resume bug where Word could silently overwrite unrelated leftover content"* is far more useful to someone deciding whether to update than *"various fixes."*
