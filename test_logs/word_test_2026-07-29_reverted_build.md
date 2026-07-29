# Word test — reverted (pre-HTN) build — 2026-07-29

**Goal sent:** `open Word and write hello world and save it as pulse_word_test.docx`
**Build:** the flat `_run_task_loop` / `_execute_task_list` / `_act_observe` design, reverted back from
today's HTN (task_steps tree) redesign attempt per direct instruction ("okay its worse then previous
revert it right now").

## Timeline (from pulse.log, all times 2026-07-29)

| Time | Event |
|---|---|
| 00:23:15 | SEND: root goal. |
| 00:23:25 | RESPONSE: reasoning says *"This requires a task_list on the first turn"* — but the returned JSON has **no `task_list` field at all**, only `plan: [open_app Word]`. Because `task_list` came back empty, `len(task_list) > 1` is false, so `_execute_task_list` (the per-step isolation path) never runs. **The entire job runs as one continuous `_act_observe` session from here on**, not as independent steps. |
| 00:23:39 – 00:24:44 | Several rounds of real, correct navigation: read_screen → sees Word's start/recent-files screen → clicks "New" → still a template list → clicks "Blank document". Each round correctly reused the accumulated `ACTIONS YOU JUST PERFORMED` history to reason about state. |
| 00:24:52 | `click_element` on "Blank document" raises a real COM/UIA error: `An event was unable to invoke any of the subscribers`. |
| 00:25:03 | Model reacts to the error: response cuts off mid-JSON (truncated generation) → fails to parse → `PlannerClient` retries the same call automatically. |
| 00:25:03 | `_act_observe`'s no-progress detector hits 5 consecutive not-quite-working rounds and injects: *"Step back: is there a fundamentally different way to approach this... or is this genuinely blocked?"* |
| **00:25:12** | **Restart bug reproduces.** Response: *"This requires a task_list on the first response... the previous 5 rounds were likely related to a different goal or context that has now been superseded by this new, clear instruction."* Re-issues `open_app` on an already-open Word (`already_running: true` — wasteful but harmless here since Word really was already open). This is the exact same restart-loop failure mode that originally motivated the HTN rewrite, just triggered here by the no-progress nudge rather than the original trigger. |
| 00:25:24 – 00:26:06 | Recovers: read_screen → "Blank document" (again) → `click_element` succeeds this time (`"message": "Clicked Blank document"`) → read_screen shows the real Word ribbon/editor (142 controls) with the genuine trial-edition banner ("This preview of Word is ending soon..."). |
| **00:26:38** | `fill_element` on the real document body (`[7] Edit: Page 1 content`) succeeds: **`"message": "Typed into Page 1 content"`** — the text actually got typed. This is farther than any of the three HTN-build attempts reached. |
| 00:26:47 | Correctly proceeds straight to `save_file` (filename `pulse_word_test.docx`, folder defaulted to desktop) without re-reading the screen first, per the "don't double-check, just save" nudge. |
| 00:26:51 | `save_file` reports missing info: **no folder was ever named in the original goal** (this goal really didn't say one, unlike the earlier "emails folder on desktop" test) — correctly asks: *"what folder should I save the file in?"* and parks awaiting an answer. |
| 00:27:07 | (Second restart-flavored response while formulating the question — same "must use task_list on the first turn" framing appears in reasoning again, but this time it correctly recognized the override instruction and asked the question anyway, so no actual harm this time.) |
| 00:27:34 | I sent `"desktop"` as a **new, separate** websocket `text_command` (a fresh connection each time, in my one-shot test script) rather than answering through the live mic-based `ask_slot()` prompt Pulse was actually waiting on. Pulse correctly has no way to know this was meant as an answer to the parked question — it treated `"desktop"` as a brand-new, ambiguous command and asked for clarification instead. **This is a limitation of how I drove the test (a disconnected one-shot script), not a bug in Pulse** — a real spoken/typed answer inside the same session would go through `ask_slot`'s synchronous listen, not a fresh `text_command`. |

## Bottom line

One real bug reproduced: the mid-task restart loop (the original motivation for the HTN rewrite) still
happens in the reverted flat design too — triggered here by the no-progress nudge misfiring after a
genuine UIA error, not by the original trigger, but the same failure class. It was **not fatal** this
time — Word was already open, so the redundant `open_app` cost one wasted round rather than derailing
the task, and the run recovered and made real progress afterward (text was actually typed into the
document — farther than any HTN-build run got). The save step correctly asked for the one genuinely
missing piece of information (no location was ever given) and parked correctly; the "wrong answer"
after that was a limitation of my test script, not the app.

## Unresolved tail-end discrepancy (flagged, not root-caused)

After the confusion above (my one-shot test script answering "desktop" as an unrelated new command
instead of through the live `ask_slot` flow, then a "continue" resume attempt), the task's DB row
(`tasks` table) ended up with `status = 'completed'` and a stored `result` that **stops right after
"Typed into Page 1 content"** — it never includes a `save_file` attempt, and no `pulse_word_test.docx`
exists anywhere on disk (checked Desktop and OneDrive-redirected Desktop). So the task reports itself
done, but the file was genuinely never saved.

I did not fully trace the exact code path that produced this — it's tangled up with my own test
script's non-standard way of answering a parked question (a fresh one-shot `text_command` connection
rather than a live conversation feeding `ask_slot`'s synchronous mic listen), which is a real confound.
It's worth a closer look with a cleaner reproduction (a real spoken "continue" + spoken folder answer,
not a scripted one-shot message) before concluding whether this is a genuine latent bug in the
"completed" bookkeeping or purely an artifact of how this particular test was driven.
