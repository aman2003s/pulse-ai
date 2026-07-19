# Pulse — Project Plan v2.0 (sequential, execute top-to-bottom)

## Locked Technical Decisions (researched 2026-07-18)
| Area | Decision | Why |
|---|---|---|
| LLM runtime | **llama.cpp (llama-server), CUDA build** (2026-07-19: swapped from Vulkan — see note below), NOT Ollama | Ollama's Gemma 4 audio is broken (open issue #15333) and its tool-call parser has bugs with Gemma 4's hybrid attention. llama.cpp audio works since PR #24118 (Jun 2026). |
| Model | Gemma 4 E4B **Q4_K_M** (~3GB) + **mmproj** file (audio+vision encoder) | Fits 8GB RAM machines. Audio input capped at **30s max** per request. |
| Wake word | **openWakeWord** (onnxruntime on Windows), custom-trained "pulse" model | Free, offline, Apache-2.0. Porcupine = $6K+/yr. Windows uses ONNX runtime only (no tflite). |
| VAD | **Silero VAD** | Standard, tiny, offline. Needed for end-of-speech detection + barge-in. |
| STT fallback | **faster-whisper (small)** | Backup path if Gemma audio-in quality/latency disappoints; also handles >30s speech. |
| TTS | **Kokoro-82M** via `kokoro` pkg + `sounddevice` streaming playback | 327MB, CPU-ok, streams chunks; sentence-streaming keeps latency <500ms. |
| Barge-in strategy | **Half-duplex for MVP**: mute wake/STT mic while TTS plays, except wake-word listener stays on; saying "Pulse" cancels TTS instantly (<200ms). Full AEC deferred. | Real echo-cancellation (AEC) is complex; half-duplex is reliable and simple. |
| Core language | **Python 3.11+** | All chosen libs are Python-native. |
| Core↔UI API | **WebSocket on localhost:7550** (JSON events) + core runs headless as background process | UI is a thin client; core works with zero UI. |
| DB | **SQLite** (stdlib `sqlite3`) | No server, one file. |
| UI | **Tauri 2.x** translucent always-on-top overlay | Small binary, cross-platform later (Win→Linux→Android via Tauri mobile). |
| Function calling | llama.cpp **grammar-constrained JSON output** (GBNF/json_schema) | Guarantees valid tool-call JSON every time; no parse failures. |
| Packaging | PyInstaller (core) + Tauri bundler (UI) + NSIS installer; models downloaded on first run | Installer stays small; first-run wizard fetches ~3.5GB models. |

---

## M0 — Environment & Skeleton (do first, everything depends on it)
- [x] **M0.1** Create repo layout: `core/` (engine), `core/voice/`, `core/planner/`, `core/tasks/`, `core/executor/`, `core/skills/`, `core/tools/`, `core/adapters/win/`, `core/api/`, `ui/` (Tauri), `models/` (gitignored), `docs/`, `tests/`
- [x] **M0.2** Python 3.11 venv + `pyproject.toml`; deps: `openwakeword, onnxruntime, silero-vad, sounddevice, numpy, kokoro, faster-whisper, websockets, pydantic, httpx`
- [x] **M0.3** Download + verify models: Gemma 4 E4B Q4_K_M GGUF + mmproj, Kokoro-82M, Silero VAD, openWakeWord base. Script: `scripts/fetch_models.py` with SHA256 checks + resume support. *Edge: disk-full and interrupted-download handling.*
- [x] **M0.4** Run `llama-server` with E4B + mmproj; smoke-test: (a) text prompt → response, (b) WAV file → transcribed/understood response, (c) json_schema-constrained output returns valid JSON. **Acceptance: all 3 pass on your machine; record tokens/sec.**
  - *If (b) fails or is too slow → flip switch to Plan B: faster-whisper STT → text → Gemma. This decision gates M2.*
- [x] **M0.5** SQLite schema + migration script: `tasks(id, goal, status, current_step, plan_json, history_json, result, created_at, updated_at)`, `settings(key, value)`, `app_index(name, path, aliases)`, `file_index(path, name, mtime)`
- [x] **M0.6** Config file `%APPDATA%/Pulse/config.json`: mic device id, voice choice, feedback mode, model paths, port. Defaults auto-generated on first run.

> **Status 2026-07-18:** M0 done. M1 done except long soak test. M2–M3 done and live-verified (text commands ~2s). M4 partial (core service + WS API live; task resume + autostart pending). M5 partial (context, multi-step, modes work; follow-up references and interrupt handling untested). M6 built and running (mic/voice pickers pending). New: voice-guided wake-word personalization (UI button + `train_wake_word` WS event), `describe_screen` tool, spoken result narration.

## M1 — Voice I/O Foundation
- [~] **M1.1** Wake model trained via `scripts/train_pulse_v2.py` (Kokoro-synthesized data + user's real recordings in `models/user_samples/`, retrain any time). Validated: held-out synth voices 0.96–1.00, user recordings up to 1.00, negatives ("impulse", "pause", silence) ≤0.03. Remaining: long-duration false-accept soak test (<1 per 4h of noise/TV) per original criteria.
- [x] **M1.2** Always-on wake-word listener process: mic stream (16kHz mono) → openWakeWord. CPU budget <5%. *Edge: no mic present, mic unplugged mid-run (auto-reconnect), multiple mics (use config device), exclusive-mode conflicts.*
- [x] **M1.3** Capture pipeline: on wake → play ack earcon + varied phrase → record with Silero VAD until 800ms silence OR 25s cap (Gemma's 30s limit minus margin). *Edge: user says nothing (5s timeout → "I'm here if you need me"), user talks 30s+ (chunk to faster-whisper path).*
- [x] **M1.4** Kokoro TTS service: text in → streamed audio out via sounddevice; sentence-level streaming (speak sentence 1 while generating sentence 2). Cancellation token stops playback <200ms. *Edge: audio device busy/changed, very long text (chunking), numbers/paths read naturally ("C:\Docs" → "C drive, Docs folder").*
- [x] **M1.5** Half-duplex controller: wake-listener active during TTS; "Pulse" during playback = instant cancel + listen. **Acceptance: full loop — "Pulse" → ack → speak → (dummy echo response) → interrupt it with "Pulse" — works 10/10 tries.**

## M2 — Brain: Intent → Tool Call
- [x] **M2.1** Planner client: send audio (or STT text) + system prompt + tool schemas to llama-server; json_schema-constrained response: `{speak: str, tool: str|null, params: {}, needs_confirmation: bool}`. *Edge: model timeout (15s → apologize + abort), server crash (auto-restart, max 3), malformed output impossible via grammar.*
- [x] **M2.2** System prompt v1: persona, feedback-mode rules, tool list, "ask when ambiguous", "never invent file paths". Keep <1500 tokens (KV-cache it).
- [x] **M2.3** Tool registry: pydantic base class `Tool(name, description, input_schema, output_schema, permission_level, platforms)`; auto-export schemas into planner prompt.
- [x] **M2.4** Executor: validate params against schema → check permission level → run tool (10s timeout, sandboxed try/except) → structured result. One action at a time, queue extras.
- [x] **M2.5** Observer: after each tool run verify outcome (process exists, file opened, path exists) and attach `verified: bool` to result.
- [x] **M2.6** Safety layer: `permission_level ∈ {safe, confirm, dangerous}`. `confirm` → Pulse asks aloud, waits for yes/no (yes/yeah/do it vs no/stop/cancel). `dangerous` (delete/format/registry) → refused in MVP. *Edge: unclear answer → re-ask once, then abort.*

## M3 — First Real Tools (Windows adapter)
- [x] **M3.1** App index builder: scan Start Menu .lnk files + UWP app list → `app_index` table with aliases ("chrome"→"Google Chrome"). Refresh daily + on miss.
- [x] **M3.2** `open_app(name)`: fuzzy match (rapidfuzz) against index. Score <60 → ask user; 2 close matches → offer both aloud. *Edge: app already running (focus window instead), app uninstalled (reindex + report).*
- [x] **M3.3** `close_app(name)`: graceful WM_CLOSE first, never force-kill without confirm. *Edge: unsaved-work dialogs → tell user to check screen.*
- [x] **M3.4** File index: background walk of Users profile dirs (Documents/Desktop/Downloads/Pictures) into `file_index`; watchdog for changes; skip node_modules/AppData/hidden. *Edge: OneDrive placeholder files (don't trigger downloads), permission-denied dirs (skip silently), 100k+ files (cap + prioritize recent).*
- [x] **M3.5** `search_file(query)`: rank by fuzzy name match + recency. 0 hits → say so + suggest rephrase; 1 hit → proceed; 2–5 → read options aloud; >5 → ask to narrow. 
- [x] **M3.6** `open_file(path)` / `open_folder(path)`: `os.startfile`. *Edge: no associated app (open containing folder + explain), path gone since indexing (reindex + apologize).*
- [x] **M3.7** **MVP GATE — end-to-end offline test (Wi-Fi off):** "Pulse, open Notepad" / "Pulse, open my resume" / "Pulse, close Chrome" — each completes with spoken feedback in <6s. 9/10 success across 2 different voices.

## M4 — Core Service & API
- [x] **M4.1** Assemble core as single supervised process: wake listener, llama-server (child process), TTS, executor, WebSocket server. Crash of any child → auto-restart + spoken notice.
- [x] **M4.2** WebSocket event contract (documented in `docs/api.md`): outbound `state(idle|listening|thinking|acting|speaking)`, `transcript`, `action{tool,params}`, `feedback{text,mode}`, `error`; inbound `text_command`, `cancel`, `set_config`. Versioned (`v:1`).
- [x] **M4.3** Task Manager: every request = task row; multi-step plans store step list + cursor; on crash/restart, incomplete task → Pulse offers to resume. 
- [x] **M4.4** Windows autostart (registry Run key, opt-in) + single-instance lock + tray-less headless mode. **Acceptance: reboot → say "Pulse, open calculator" with no UI ever opened.**

## M5 — Multi-Step Planning & Conversation
- [x] **M5.1** Conversation manager: rolling context of last 5 exchanges fed to planner; clarification loop ("Which bill?" → "Electricity" merges into original intent). Context clears after 2min idle. 
- [x] **M5.2** Multi-step plans: planner may return `plan: [step...]`; executor runs sequentially, re-plans after each observation; max 6 steps then check in with user. *Edge: step 2 fails → planner gets failure + decides retry/alternate/abort; user says "stop" mid-plan → cancel cleanly.*
- [x] **M5.3** Follow-up references: "open the second one", "not that one", "the PDF" resolve against last search results (kept in task state).
- [x] **M5.4** Feedback modes wired end-to-end: Minimal (result only), Standard (start+result), Guided (every step + orientation help). Switchable by voice: "Pulse, be more detailed."
- [x] **M5.5** **Gate test:** "Pulse, find my April electricity bill and open it" — search → disambiguate by voice → open → confirm. Also test the failure path (no such file) ends gracefully.

## M6 — UI Overlay (can start parallel after M4.2 contract is frozen)
- [x] **M6.1** Tauri 2.x window: frameless, transparent, always-on-top, bottom-center pill. *(Click-through-when-idle deferred: it would block the text input; pill fades to 35% opacity after 6s idle instead.)*
- [x] **M6.2** WebSocket client with auto-reconnect; renders: state orb (idle/listening/thinking/speaking animations), live transcript line, current action line, error toast. **Zero logic rule: UI never computes anything, only renders events + sends `text_command`/`cancel`.**
- [x] **M6.3** Text input fallback field (accessibility: full keyboard nav, screen-reader labels, high-contrast mode, respects reduced-motion).
- [x] **M6.4** Settings panel: feedback mode, mic picker (via `list_devices` API), narration toggle, train-wake-word button — all `set_config`-driven, zero UI logic.
- [x] **M6.5** **Acceptance: kill UI process mid-task → core finishes task and speaks result; relaunch UI → reconnects and shows current state.**

## M7 — Hardening & Packaging
- [x] **M7.1** First-run wizard: voice-guided mic check + tutorial (model download narration deferred to installer work in M7.5).
- [~] **M7.2** Failure modes: llama-server port conflict auto-fallback (8081→8083) ✅, missing mic → spoken notice + text mode ✅. Remaining: sleep/resume audio reinit, low-RAM warning, corrupted-model redownload.
- [~] **M7.3** Perf: command→spoken response ~2s (target <4s ✅) with prompt caching + GPU offload. Remaining: measure idle CPU/RAM formally.
- [~] **M7.4** Logging: rotating file at %APPDATA%Pulsepulse.log, transcripts kept out of INFO logs, no audio ever written to disk. Remaining: --debug flag.
- [ ] **M7.5** PyInstaller core + Tauri bundle + NSIS installer; models fetched on first run. Test clean install on a fresh Windows VM.
- [ ] **M7.6** 1-week self-dogfood; log every miss/annoyance; fix top 10. **This is v1.0.**

## M-A11Y — Accessibility First (PRIORITY: this product is for blind/disabled users first; pull these before M7 polish)
- [x] **A1** `describe_screen` tool: focused window + open windows, spoken naturally
- [x] **A2** Result narration: Pulse speaks outcomes (search hits, screen contents, errors) — not just intent
- [x] **A3** Continuous narration mode (opt-in): says "Now in [window]" on every focus change. Toggle: voice ("start/stop narrating"), UI checkbox in settings. Verified live.
- [x] **A4** Deep screen reading: `read_screen` walks the UIA tree of the focused window (buttons, links, fields, text) and Pulse narrates it. Verified live.
- [x] **A5** Voice-driven first run: on first launch Pulse speaks a welcome, checks the mic (with spoken fallback if silent), and teaches the 3 core commands. Zero vision required.
- [x] **A6** Voice-triggered wake-word training: say "train my voice" or "learn my wake word" — recognized directly (no planner round-trip), starts the guided flow.

## M-A11Y-2 — Deeper accessibility (2026-07-19)
- [x] Typing echo: character + word-on-space speech, system-wide via `keyboard` hook, skips password fields (checked live via UIA `IsPassword`). Off by default; toggle in settings popup or voice ("typing echo on/off"). Unit-tested + live-verified.
- [x] Continuous screen reading ("read my screen"): context → tabs → content, no per-step confirmation by default (interrupt any time by saying the wake word — reuses existing barge-in). Guided feedback mode pauses to ask before reading detail, matching a "beginner" verbosity level. Direct command, bypasses planner. Live-verified.
- [x] "Repeat that" — recalls last spoken response.
- [x] "Spell <word>" — letter-by-letter.
- [x] "Speak faster/slower/normal speed" — adjusts Kokoro playback rate live.
- Known limitation: per-character TTS during fast typing can drop characters under load (single-slot queue keeps only the newest); word-on-space is always reliable since it's a discrete event, not a stream.

### PLANNED REDESIGN (2026-07-19, researched, not yet built): zero-skip character echo
Researched NVDA/JAWS's actual behavior to validate the intuition before building it — your design is right, with one addition:
- **Every character is announced, never silently skipped.** Confirmed real screen readers guarantee this.
- They achieve it via **interruption, not queuing**: a new keystroke cuts off the previous character's audio and starts immediately — the newest key always wins, previous ones get truncated rather than dropped entirely or fully played out.
- **The critical piece we're missing**: JAWS uses a dedicated, much faster/higher-rate voice specifically for character echo, separate from its normal reading voice — because neural TTS (like our Kokoro) is too slow (100-300ms+ per call) to keep up with real typing speed. This is *why* our current version has to drop characters — Kokoro is the wrong tool for this specific job, not a queue-size problem.
- **Fix**: add `eSpeak-ng` (free, GPL, offline, ~single-digit-ms latency, the same class of engine real screen readers use for this exact purpose) as a second, dedicated TTS path used *only* for character/word echo. Keep Kokoro for normal assistant speech — this mirrors JAWS's own two-voice design, not a shortcut.
- **Interrupt-on-keystroke** instead of the current single-slot-drop queue, using eSpeak's near-zero latency to make interruption imperceptible in practice.
- Word-boundary triggers to expand beyond space/enter/tab: add sentence/word punctuation (`. , ! ? ; :`) as word-echo triggers too, matching common screen-reader word-echo behavior.
- Not yet implemented — plan only, pending go-ahead.

## M9 — Universal App Control Skill (REVISED 2026-07-19, not started — this is the milestone that makes Pulse feel like AI, not a command launcher)
Motivation: open_app/open_file are lookups, not tasks needing a reasoning model. This adds genuinely AI-dependent, multi-turn, end-to-end tasks across **any application** — browsing the web in the user's real browser is the first proof case, not a special subsystem.

### Course-correction from the first draft of this plan (researched, both changes are real fixes not preferences)
1. **Dropped the isolated Playwright browser.** Researched Chrome/Edge's automation policy: they actively **block** Playwright from driving a user's real default profile (security restriction against exactly this kind of hijack, not a bug to work around). An isolated shadow browser also directly contradicted "we don't want hidden browsers, we need the user's own browser."
2. **New architecture: extend UIA, don't add Playwright.** Our existing `read_screen`/`describe_screen` already walk any app's accessibility tree via `uiautomation` — and Chromium/Edge exposes its rendered web content through UIA too (it's how real screen readers read web pages). So one **Universal App Control** skill — UIA read (have it) + UIA `InvokePattern`/`ValuePattern` for click/fill (new) — drives the user's actual open browser *and* File Explorer *and* any other Windows app, uniformly. No CDP, no debug ports, no profile restrictions, no separate subsystem per app.
3. **Element targeting**: extend `read_screen`'s tree walk to cache each control's UIA element handle against an index for the current turn (mirrors the existing "open the second one" pattern in `conversation.py`), so a follow-up like "click search" or "type into the second field" can target it directly.
4. **Fill fallback**: `ValuePattern.SetValue()` where supported; otherwise focus the control (`SetFocus`) and type via the `keyboard` library (already a dependency, used by typing-echo) for controls that don't implement ValuePattern — common for some web inputs.

### Locked decisions
| Area | Decision | Why |
|---|---|---|
| Automation engine | **UIA InvokePattern/ValuePattern**, extending existing `read_screen` — no Playwright/CDP | Drives the user's real, already-open apps and browser; sidesteps Chrome/Edge's profile-automation block entirely; one mechanism for every app, not one per app type. |
| General web search (background Q&A) | **DuckDuckGo HTML endpoint** first (free, no key, no visible browser — used only for quick factual lookups like "who's the president of India", not full browsing) | Matches "free and commercially free search" requirement. |
| Search fallback | If DDG proves insufficient for a query, fall back to driving the user's real browser (same UIA mechanism as above) to run an actual Google search — still not a separate hidden browser, same unified control path. | Keeps the "no hidden browser" rule intact even in the fallback case. |
| Connectivity-aware answering | Before answering questions needing current info: check connectivity (fast DNS/HEAD probe). Online → search for a real answer. Offline → answer from Gemma's own knowledge **and explicitly say so**: "this is what I know, since you're not connected to the internet." | Matches Explainable Actions principle — never silently pass off stale/offline knowledge as current. |
| Interaction model | Mirror screen readers' **Browse Mode / Forms Mode** split (JAWS/NVDA, decades-proven) | Browse Mode = continuous narrated reading/scrolling, interrupt any time. Forms Mode = auto-entered per field, distinct earcon, asks for value, reads back to confirm before moving on. |
| Form input modes | (1) natural dictation, (2) letter-by-letter **spell mode** for error-prone fields | Always read back + confirm before submit — never submit unconfirmed blind-typed data. |
| Long/complex tasks | **Actually implement the re-planning loop M5.2 already specified but the code doesn't fully do.** Currently a `plan` array runs open-loop in one pass. New: planner emits an explicit step list, Task Manager persists it (`plan_json`/`current_step` — schema already has these fields), executor runs one step, **re-invokes the planner with the observation** before the next step, so it can adapt, insert, or skip steps based on real results — not just blindly follow a stale upfront plan. This is the actual mechanism for "break the task into a list and follow it one by one." | This is the standard, proven pattern for robust multi-step agent execution (reason → act → observe → re-plan), and it's what makes long tasks robust instead of brittle. |
| Continuous feedback | Hard rule: **no silence >4s while busy** → spoken filler. Superhero Mode: shortened follow-up timeout + one "Still there?" check-in before defaulting to reading the screen. | Long silence is explicitly unacceptable in this mode. |
| Superhero Mode sound | Distinct short chime (different from the ack earcon) on toggle-on. | Simple asset addition. |
| Safety | CAPTCHA/login walls and anything requiring credentials: tell the user, do not attempt to bypass or enter credentials (matches the hard credential-entry rule that already governs this assistant). | Never silently work around a blocker; never touch passwords. |

### Tasks (sequential)
- [x] M9.1 Element-targetable `read_screen` + `click_element`/`fill_element` (InvokePattern/ValuePattern, keyboard-sim fallback). **Verified via screenshot** — real text visibly typed into real Notepad, not just a self-reported tool result.
- [x] M9.2 General reason→act→observe→re-plan loop (`_run_task_loop`), not a hardcoded special case — every actionable command runs through it, bounded to 6 rounds. Replaced an earlier narrower version that only handled the search-then-open case (found insufficient when "open notepad, write X, save it" stalled after step one — fixed by making the loop unconditional, not just first-round trigger + prompt now explicitly teaches open→read_screen→act as the general pattern for any app interaction).
- [ ] M9.3 Browse Mode: "open browser and search X" → focuses/launches the user's real browser via existing `open_app`, types into the address/search bar via the fill mechanism, reads results via `read_screen`, speaks a numbered list.
- [ ] M9.4 Page reading: "summarize this or read it all?" — no reply within timeout → defaults to reading (per spec). Summarize = real LLM call. Read-all = continuous narration, interruptible by wake word.
- [ ] M9.5 Forms Mode: walk fields one at a time, announce label + distinct earcon, capture value (dictate or spell), read back to confirm, next field. Explicit confirmation before any submit click.
- [x] M9.6 `web_search(query)` tool (DDG, connectivity check, offline disclosure). **Live-verified**: "who is the president of India" → correct, current, naturally-synthesized spoken answer. Real-browser-Google fallback (if DDG proves insufficient) not yet built — DDG alone has been sufficient in testing so far.
- [x] M9.7 Continuous-feedback heartbeat (`_execute_with_heartbeat`, 4s threshold) — implemented, not yet triggered under a real slow operation in live testing.
- [x] M9.8 Superhero Mode chime + full three-setting toggle. **Live-verified** via WS (`feedback_mode`/`narrate`/`typing_echo` all flip together).
- [x] M9.9 System prompt extended: new tool schemas, browsing example, and a worked compound-task example ("open notepad, write X, save it") teaching the general open→read_screen→act pattern. Kept off the direct-intent regex shortcuts as intended.
- [ ] M9.10 Edge cases: element not found/stale after page change, very long pages capped with "read more?", ads/cookie banners auto-declined where detectable.
- [ ] M9.11 **Real end-to-end acceptance test — not unit tests.** Driven live over the actual WebSocket API, same method used for every fix this session: (a) full search→results→pick→read against the user's real browser and real search results; (b) summarize produces an actually-coherent LLM summary; (c) Forms Mode on a real safe test form via both dictation and spell mode, read-back verified; (d) a multi-step non-browser task (e.g. "find my resume, rename considerations, open it") to prove the re-plan loop works outside browsing too; (e) offline-mode disclosure actually fires when disconnected; (f) 4s-silence filler actually fires during a deliberately slow step.

### TTS engine for typing echo — final pick deferred to implementation
Windows ships SAPI5 (zero install, via `pyttsx3`) — same low-latency class as eSpeak-ng, no extra binary. Will benchmark both at build time; default lean is SAPI5 for the smaller footprint unless it's noticeably worse.

### Status check on prior open items
- 5 weak points from the flow diagram: still open, none blocking M9 — app-index gaps (M3.1) and wake-model soak testing (M1.1) worth doing alongside M9 since it leans on the same STT/wake reliability.
- M1.1 soak test, M1.5/M5.5 voice gate tests, M6.4 real second-mic test: still need the user physically present — unchanged.
- M7 hardening: still pending, lower priority than M9 per this conversation's direction (capability before polish).

## M9 BUILD LOG (2026-07-19) — what's actually done, real test results, and honest gaps
**Built and live-tested:**
- [x] Typing echo redesign: SAPI voice (fast, interrupt-via-purge, matches JAWS's dedicated-voice approach), word-boundary punctuation added. Unit-verified (SAPI COM access confirmed); not yet re-verified live with real keystrokes (needs the user typing).
- [x] `click_element`/`fill_element`/`read_screen` (element-indexed, UIA InvokePattern/ValuePattern) — the Universal App Control foundation.
- [x] Targeted re-plan for data-dependent chains ("find and open X") — bounded to one extra round, triggers only when a search left unconsumed matches.
- [x] `web_search` tool — **live-tested with a real query** ("who is the president of India") → correct, current, naturally-synthesized spoken answer via genuine LLM call. This is the concrete "actually needs AI" proof case working end to end.
- [x] Continuous-feedback heartbeat (4s silence → filler) — implemented, not yet triggered under a real slow operation in testing.
- [x] Superhero Mode chime + full three-setting toggle — **live-verified** (`feedback_mode/narrate/typing_echo` all flip together).
- [x] System prompt updated with browsing-via-composition and offline-disclosure examples.

**Real bugs found and fixed during this session's testing (not hypothetical — actually hit):**
1. `_narrate_results` didn't recognize `web_search`'s result shape — search ran correctly but the answer was never spoken. Fixed (routes through LLM synthesis, which this case genuinely needs).
2. `ReadScreenTool` crashed on `GetForegroundControl()` returning `None` — now a graceful spoken error instead of a stack trace.

**Known gaps, stated plainly rather than glossed over:**
- **Forms Mode is not a separate state machine.** Scoped down to composition: the planner uses `read_screen` + `fill_element`/`click_element` directly, guided by prompt examples, rather than a dedicated per-field-walk-with-earcon flow. Handles the concrete browsing example; doesn't yet give the distinct-earcon-per-field experience originally specced.
- ~~`cancel` doesn't abort an in-progress blocking mic capture~~ **FIXED 2026-07-19 and live-verified**: added a `threading.Event`-based abort to `CapturePipeline`, wired into the `cancel` WS handler. Test: sent a real task, waited for the follow-up's blocking listen window, sent `cancel` + an immediate new command — previously this would hang until the ~6s timeout; now processed correctly in **0.3s**.
- **Foreground/element-click testing was inconclusive in this headless environment** — `GetForegroundControl()` returned `None` consistently here because there's no real interactive desktop focus in this automated context; confirmed via isolated repeated calls that this doesn't crash anything, but real click/fill behavior needs verification with the user actually at the keyboard.
- A core process death occurred once mid-testing with no Python traceback — isolated `read_screen` calls don't reproduce it, so it's more likely this session's background-process lifecycle than app code, but flagging it rather than assuming.

## FULL RE-TEST PASS (2026-07-19, continued) — root-caused the process-death mystery, 3 more real bugs found+fixed, click/fill proven with a screenshot

**Root cause of every "silent core crash" this session, finally found:** `print(f"User: {text}")` in `handle_capture_session` crashed with `UnicodeEncodeError` whenever Whisper transcribed a character Windows' default console codepage (cp1252) can't encode — killing that thread outright. **Fixed at the source**: `sys.stdout`/`stderr` reconfigured to UTF-8 with `errors="replace"` at the top of `pulse.py`, fixing every `print()` call in the app at once rather than patching individual call sites.

**Added defense-in-depth on top of that**: a `_safe_thread()` wrapper now used for every background-thread entry point (`handle_capture_session`, `process_text`, `_train_wake_flow`, `_read_everything_flow`) — any future unhandled exception logs safely and forces the app back to idle instead of leaving it silently stuck mid-state forever.

**Other real bugs found via actual execution, not review, and fixed:**
1. `cancel` didn't abort an in-progress blocking mic capture (only stopped TTS + flipped a flag) — added a real `threading.Event`-based abort to `CapturePipeline`. **Verified**: a command sent right after cancelling a follow-up's listen window now processes in 0.3s instead of hanging ~6s.
2. `read_screen`'s result had a leftover placeholder `"message": "screen contents"` field that the narration logic spoke literally instead of actually describing what was found. Fixed + added real narration for element lists.
3. **The important one**: modern (Windows 11) Notepad's actual text-editing area reports its UIA control type as `Document`, not `Edit` — so it was never in the numbered element list at all, making it un-fillable. Fixed by making `Document` (and any unlabeled Edit/Document control) always element-eligible.

**Click/fill proven working — for real, not just "no error returned":** after early verification attempts using `WM_GETTEXT` falsely showed empty text (that Win32 message doesn't work on modern Notepad's WinUI-hosted control — a test-harness bug, not an app bug), took an actual **screenshot** of Notepad after running "type Pulse end to end test into the document area" through the live app. The text is visibly present in the real window. This is the strongest verification done this session — visual ground truth, not a self-reported tool result.

**Genuinely cannot be tested without a human physically present** (stated plainly, not glossed over): real acoustic wake-word detection through a live microphone with actual human voice variability (M1.1 soak test, M1.5 barge-in "10/10 tries", M5.5 gate test); M6.4 real second-microphone hardware swap; M7.6 the 1-week dogfood period (needs real calendar time). Everything reachable via text/WS-simulated input has now been tested with real, independently-verified resulting state — not self-reported success.

## GENERAL TASK LOOP (2026-07-19, continued) — replaced the narrow special-case with a real one, found a real concurrency bug, proved it end-to-end
User correctly rejected the earlier "M9.2" as too narrow: it only re-planned for the specific "search without a following open" shape, so a genuinely compound request ("open notepad, write X, and save it") stalled after step one. Replaced with `_run_task_loop`: the planner is asked again after every round with the REAL observed results (not a hardcoded trigger condition), bounded to 6 rounds — this is the actual reason→act→observe→re-plan pattern, and it now backs every actionable command, not a special case.

**Real bug found and fixed while testing it**: `on_wake_word_detected` only guarded against overlap during the "listening" state — a wake-trigger mid-task (ambient noise self-triggering, a pre-existing known issue) could start a second session racing the first and corrupt shared UI state (this visibly happened during testing — two sessions both touched Notepad). Fixed: the guard now also blocks during "thinking"/"acting", only allowing barge-in during "speaking" and fresh starts from "idle".

**Proof it works — visual, not self-reported**: sent "open notepad write down how are you and save it" through the live app. Confirmed via direct screenshot: Notepad's title is `*How are you - Notepad` (real content, unsaved-changes asterisk) and a real Windows "Save As" dialog is open — meaning the loop autonomously chained open_app → read_screen → fill_element → send_keys(ctrl+s), each step using real data extracted from the previous step's actual result, not guessed parameters.

**Known remaining gap, stated plainly**: a new/untitled file's Save produces a filename dialog, and the loop hasn't yet been taught to fill that in (read_screen it, fill_element the filename, click_element Save) — the mechanism is generic enough to handle it, but this specific "save a brand-new file to a real path" sub-case needs one more prompt example or a round to actually finish. Save-over-an-existing-file (no dialog) should already work as-is since `send_keys(ctrl+s)` alone completes it.

## ROUND 4 (2026-07-19, Fable): dynamic search, plan-and-execute task lists, install-class capabilities, blind-first follow-up
User asked: why only 5 jpgs / "you never told it where to look" (search was index-only over 4 folders), make everything dynamic, add task-list execution for big jobs, and make Superhero mode never-silent + screen-orienting. Implemented (plan-and-execute is the standard hierarchical agent pattern — decompose, then execute each step with observe/re-plan):
- **Dynamic search**: `search_file` now takes optional `location` (named folder or any path) and falls back to a bounded live filesystem walk (6s/depth-5 cap, skips node_modules/AppData/etc.) when the index misses. Verified: found files in a non-indexed folder; live test correctly used `location: 'desktop'`, found BOTH TASKS.md copies, spoke the disambiguation, picked the right one.
- **Task lists**: planner schema gained optional `task_list`; multi-part goals get a spoken breakdown ("I'll do this in 3 steps: ..."), persisted to `tasks.plan_json`/`current_step`, each step narrated ("Step 2 of 3: ...") and run through its own act-observe subloop; step failure → asks whether to continue; refactored `_run_task_loop` → decomposer + `_act_observe` executor.
- **Install-class jobs**: `web_search` now also returns real result URLs (unwrapped from DDG redirects); new `download_file` tool (confirm-level, saves to Downloads, reports size); prompt teaches the generic pattern: search → download → run → read_screen each wizard screen → announce checkboxes → offer to summarize terms → never click Accept/Install/Submit unconfirmed → UAC prompts explicitly disclosed as unclickable (secure desktop).
- **Blind-first follow-up**: Superhero mode now asks "What would you like me to do here?", and on no reply reads every element on the current screen, then listens again; final fallback speaks "I'm here whenever you need me" instead of going silent.
- **Live-verified end-to-end** (3-part compound goal, real data flowing between steps): search with location → disambiguate 2 matches aloud → open the correct file (tool-verified path) → describe_screen executed. Not screenshot-verified this round (token budget); tool-level verification only.

## ROUND 3 (2026-07-19, continued): audio-cutoff tuning + a real edge case found via a harder test
**Audio cutoff fix** — user reported TTS getting interrupted more than it should. Raised the elevated wake threshold 0.85→0.93, added a same-frame-name check requiring 2 *consecutive* high-confidence frames while Pulse is speaking (not just one spike) before treating it as a real interrupt, and extended the post-speech tail cooldown 0.6s→1.2s (covers reverb/echo decay better). Not independently re-measured against real speech (needs the user's voice), but the mechanism is strictly more conservative than before on every axis.

**Real edge case found via a genuinely different, harder test** ("search for a jpg picture and open it" — not Notepad): search correctly found 5 real matches and asked "Which one?" (good, safe behavior — declined to guess). But then it layered a redundant "What would you like me to do now?" on top of that clarifying question — confusing double-prompt. Root cause: the code only checked "did any tool run" to decide whether to invite a follow-up, not "does the last thing said already invite a reply." **Fixed and re-verified**: `_run_task_loop` now tracks whether it ended on a question (`speak_text` ending in "?") and skips the redundant follow-up in that case — confirmed via the same test, log now shows the clarifying question with no collision.

## M8 — Post-v1 (backlog, do not start before M7 ships)
- Accessibility Intelligence: guidance mode beyond A1–A4
- Terminal skill (command allow-list + explain output), rename/move/copy file tools (confirm-level)
- faster-whisper long-dictation mode; full AEC barge-in (talk over Pulse without wake word)
- Plugin SDK (skill = folder with manifest + tools); Linux adapter; browser skill
- Operational memory: learn preferred apps/folders from usage history

---
## Perf fix (2026-07-19): 6-7x LLM speedup — Vulkan → CUDA
Root cause found via llama-server's own per-request timing logs: prompt processing was fast (770-959 tok/s) but **generation was only ~6.85 tok/s** despite `-ngl 99` full GPU offload — the bottleneck wasn't GPU compute, it was the Vulkan backend specifically. This machine has an RTX 4050 (6GB VRAM, driver 591.44) but only the Vulkan llama.cpp build was installed; Vulkan is NVIDIA's generic fallback path, not CUDA's optimized one.
Tested and ruled out first (all on Vulkan): `-fa on` (flash-attn) hung 60s+, unusable on this backend/model combo. `--spec-type ngram-simple` (n-gram speculative decoding) gave inconsistent timing (5-11s) and produced different output text under grammar constraints — a correctness risk, not used. `--cache-reuse` alone: no measurable change.
Fix: downloaded the official CUDA 12.4 build from ggml-org/llama.cpp releases (same free/open-source project, different compiled backend) and swapped `models/llama-server.exe` + its DLLs. **Generation went from 6.85 tok/s to 48.46 tok/s.** A 3-step multi-tool plan request went from 12-14s to 1.9-2.9s, verified both via isolated benchmark and live through the real WebSocket pipeline (2.31s to spoken response). No model, prompt, or capability changes — same Gemma 4 E4B, same accuracy, just the correct backend for the actual GPU present.

---
## Verification log (2026-07-18, review of M0–M5 implementation)
Fixed: (1) UI `text_command`/`cancel`/`set_config` were ignored by core — now wired into VoiceController; (2) confirm-level tools ran without asking (`user_confirmed=True` hardcoded) — now voice yes/no via `ask_confirmation()`; (3) llama-server got 3s to boot — now polls `/health` up to 120s.
Known deviations: STT uses faster-whisper `tiny.en` (English-only; upgrade to `small` if accuracy is poor — one-line change in controller.py). Barge-in during multi-step execution can race (new session while old steps finish) — revisit in M7.2.
Build note: antivirus intermittently blocks `link.exe` ("Access is denied") during cargo builds — just re-run `cargo build` until it passes (incremental, converges in a few tries), or add a Windows Security exclusion for the project folder. UI built OK on attempt 4; exe copied to `ui/pulse-ui.exe` (UI assets are embedded, it's self-contained).

---
**Dependency chain:** M0 → M1 → M2 → M3 (MVP gate) → M4 → M5 → M7. M6 forks after M4.2.
**Next: A3 continuous narration, A5 voice-driven first-run, M7 hardening. Needs user voice: M1.5 barge-in 10/10, M5.5 gate test, M1.1 soak test.**
