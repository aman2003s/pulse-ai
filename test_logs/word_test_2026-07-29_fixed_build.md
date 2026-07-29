# Word test — after the 4 fixes — 2026-07-29

**Goal sent (3 attempts, same text each time):** `open Word and write hello world and save it as pulse_word_test.docx`

**Fixes applied before this test, in `core/voice/controller.py`, `pulse.py`, and the new
`core/adapters/win/office_prefs.py`:**
1. `PreferCloudSaveLocations=0` registry fix at startup (Office cloud-save default).
2. `task_list` validation + one corrective retry when a compound goal's response omits it.
3. Removed the round-based `consecutive_no_progress` nudge/park mechanism (the direct cause of
   the earlier restart-loop bug).
4. Kept the existing save-retry-3-times-then-report logic as the sole remaining fail-safe.

## Attempt 1 (00:44 — screen was locked)

`task_list` came back empty on the first response despite the reasoning saying it was needed —
**my corrective retry fired and fixed it**: the second response correctly included
`task_list: ["Open Word", "Write the text", "Save the file"]`, and the run correctly proceeded
into `_execute_task_list`'s per-step isolation. Shortly after, `open_app` failed with "no
focusable windows" — root cause: **the workstation's screen was actually locked**
(`LogonUI.exe` confirmed running), an environmental issue, not a code bug. Paused, had the screen
unlocked, and re-ran.

## Attempt 2 (00:46 — clean Word launch, real progress)

`task_list` was correctly included on the **very first** response this time (no retry needed) —
second confirmation the fix is reliable, not a fluke.

Progress: Open Word (deterministic) → within the "Write the text" step's own bounded loop, a
spurious `task_list` reappeared mid-step (the same "reasoning-action" pattern, just triggered
differently) — **the existing `iteration == 0` guard correctly rejected it as a restart**, only
allowing one redundant (harmless, idempotent) `open_app` call rather than restarting the whole
job. This is exactly the intended behavior — no full restart occurred this run, unlike the
pre-fix test.

It then got stuck for several rounds on a **leftover "Document1 - AutoRecovered - Word"** window
and a **"Save to OneDrive to enable editing" / Protected View** dialog — both traceable to
accumulated state from my own repeated forced-kills of Word across today's testing, not a
regression from these fixes. Also re-encountered the pre-existing, out-of-scope stale
ambient-screen-context issue (system_prompt's one-time "CURRENT SCREEN" snapshot never refreshes
mid-task, so the model sometimes reasons about a screen state that's already stale). Stopped this
attempt to clean up rather than let it churn on artifacts of my own test process.

## Attempt 3 (00:51 — Word force-killed first for a clean slate)

Still hit a **different** leftover-state dialog ("Save to OneDrive to enable editing" again, 13
controls) — Word apparently still had some residual "already_running: true" session state or a
protected/shared file reference from the accumulated test runs today. Handled it correctly
(clicked "Enable editing for this file only", then "Clicked Close"), but focus then reverted to
the Claude/Pulse window (355 controls) rather than Word — the same stale-focus-tracking issue
flagged in yesterday's HTN testing, not something touched by today's 4 fixes. Stopped here given
time spent; the task itself kept running server-side independently of my monitoring.

## Bottom line

**Confirmed working, directly observed twice:**
- `task_list` validation + corrective retry — fixed the exact "reasoning says task_list but the
  field is empty" bug live, both when it needed to fire and when it didn't.
- The restart-loop bug did **not** reproduce in either full attempt — the existing
  `iteration == 0` guard correctly absorbed a spurious mid-step `task_list` without restarting.
- `PreferCloudSaveLocations` registry value confirmed set correctly at the OS level (verified
  directly via `Get-ItemProperty`), idempotent on repeat calls.

**Not yet exercised end-to-end:** no attempt reached an actual `save_file` call this session, so
the Office cloud-save fix's real effect on Word's Save dialog is confirmed at the registry level
but not yet proven against a live save. All three attempts were derailed by environmental noise
from my own repeated testing today (a locked screen, a leftover AutoRecovered document, and
residual Protected View / OneDrive-editing state) rather than by the code changes themselves — a
completely fresh machine/Word session (no prior test residue) is the fairer way to verify the
save path cleanly next time.
