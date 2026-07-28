# Contributing to Pulse

Thanks for considering contributing. This is an early-stage, honest preview of Pulse — the most valuable contributions right now are bug reports, small focused fixes, and documentation improvements, not large new features.

Please read [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) before participating.

## Ways to contribute

- **Report a bug** — [open an issue](../../issues/new/choose) using the bug report template. Include exactly what you asked Pulse to do, what happened instead, and your Windows version.
- **Suggest an idea** — [open an issue](../../issues/new/choose) using the feature request template. Check [Known Limitations](README.md#known-limitations) and the [Roadmap](README.md#roadmap) first — it may already be tracked.
- **Fix something** — see [Good first contributions](#good-first-contributions) below.
- **Improve docs** — typos, unclear setup steps, and missing detail are all fair game.

## Development setup

See [`docs/DEVELOPER_SETUP.md`](docs/DEVELOPER_SETUP.md) for getting a working environment. Short version:

```bash
git clone https://github.com/aman2003s/pulse-ai.git
cd pulse-ai
python -m venv venv
venv\Scripts\activate
pip install -e .
python scripts/fetch_models.py
run.bat
```

## Pull request workflow

All changes go through pull requests — nobody, including maintainers, pushes directly to `main`. See [`docs/BRANCH_PROTECTION.md`](docs/BRANCH_PROTECTION.md) for the exact repository rules this enforces.

1. **Fork** the repo and create a branch off `main` (`git checkout -b fix/short-description`).
2. **Make your change.** Keep it scoped — a PR that fixes one thing is far easier to review than one that fixes three.
3. **Explain the *why*, not just the *what*,** in your commit messages and PR description. If you're fixing a bug, describe what you actually observed (a log snippet, a repro command) — see [`FLOW_PLAN.md`](FLOW_PLAN.md) for the level of detail that's been useful in this project's own history of root-causing bugs.
4. **Test your change manually** against a real scenario before opening the PR — there isn't a full automated test suite yet (see [Testing](#testing) below), so a clear description of how you verified it matters.
5. **Open the PR against `main`.** CI checks must pass and a maintainer must approve before it can merge — see [`docs/BRANCH_PROTECTION.md`](docs/BRANCH_PROTECTION.md).
6. Be responsive to review feedback. If a change needs discussion, the PR thread is the place for it.

## Testing

Pulse doesn't yet have a full automated test suite — `tests/` currently holds standalone integration scripts you run directly (e.g. `python tests/test_brain.py`), not a `pytest` suite. Converting these into proper, CI-runnable tests is itself a great first contribution. Until then:

- Run the relevant script(s) in `tests/` for the area you touched.
- For changes to tool execution or the planner loop, actually run Pulse (`run.bat`) and exercise the real scenario — this codebase's history (see `FLOW_PLAN.md`) has repeatedly found that assumptions about what a fix does, without a live run against the real target app, turn out wrong.

## Good first contributions

- Anything in [Known Limitations](README.md#known-limitations) that has a concrete repro.
- Converting `tests/*.py` into a real, CI-runnable `pytest` suite.
- Adding a new [tool](core/tools/) for a common Windows app interaction gap you've hit.
- Documentation fixes — setup steps that didn't work for you are exactly what needs fixing.

## Code style

- Match the surrounding code's style rather than introducing a new one.
- Prefer clear, root-cause fixes over quick patches — this project's own commit/PR history favors understanding *why* something broke over papering over the symptom.
- Comments should explain *why*, not *what* — see the existing codebase for the tone we're going for.

## Reporting a security issue

Please **do not** open a public issue for a security vulnerability — see [`SECURITY.md`](SECURITY.md) for how to report it privately.
