# Branch Protection & Repository Governance

This documents the recommended GitHub settings so Pulse's `main` branch stays safe as an open, public, contribution-friendly repo. These are **settings to configure in the GitHub repo UI** (Settings → Branches → Branch protection rules) — this file just records what they should be and why, so they're reproducible and reviewable rather than tribal knowledge.

## Branch protection rule for `main`

Settings → Branches → Add branch protection rule → branch name pattern: `main`

- ☑ **Require a pull request before merging**
  - ☑ Require approvals: **1** (the repository owner)
  - ☑ Dismiss stale pull request approvals when new commits are pushed
  - ☑ Require review from Code Owners *(optional — only if a `CODEOWNERS` file is added later)*
- ☑ **Require status checks to pass before merging**
  - ☑ Require branches to be up to date before merging
  - Required check: `CI / syntax-check` (from [`.github/workflows/ci.yml`](../.github/workflows/ci.yml))
- ☑ **Require conversation resolution before merging**
- ☑ **Do not allow bypassing the above settings** *(applies the rule to admins too — otherwise "owner-only merge" isn't actually enforced)*
- ☐ Require signed commits *(optional, not required for this project's threat model)*
- ☑ **Restrict who can push to matching branches** — set to nobody (all changes via PR, no direct pushes, including from the owner)
- ☐ Allow force pushes — **off**
- ☐ Allow deletions — **off**

## Why "owner-only merge," specifically

The project goal for this release is to let anyone install, try, and contribute — but every merge to `main` is code that will run with real desktop automation permissions on other people's machines. Requiring the owner's review on every PR (not just "any maintainer," until there are trusted co-maintainers) keeps that bar in one place while the project is small enough for that to be practical.

## Other recommended repository settings

- **General → Pull Requests**
  - ☑ Automatically delete head branches after merge
  - Merge method: squash merge only (keeps `main` history readable — one commit per PR)
- **General → Features**
  - ☑ Issues
  - ☑ Discussions *(referenced from `.github/ISSUE_TEMPLATE/config.yml` — enable this or remove that link)*
  - ☐ Wiki *(docs live in-repo under `docs/` instead, so history and PR review cover them too)*
- **Security → Code security**
  - ☑ Enable private vulnerability reporting (this is what `SECURITY.md` points contributors to)
  - ☑ Dependabot alerts

## CODEOWNERS (optional, for later)

Once there are trusted co-maintainers, add a `.github/CODEOWNERS` file mapping paths to reviewers, and turn on "Require review from Code Owners" above. Not needed while there's a single maintainer.
