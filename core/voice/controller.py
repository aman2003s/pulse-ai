import threading
import time
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.voice.wake_listener import WakeListener
from core.voice.capture import CapturePipeline
from core.voice.tts import TTSService
from core.voice.stt import STTService
from core.planner.client import PlannerClient
from core.executor.executor import ToolExecutor
from core.tools.registry import registry
import core.tools.win_tools  # noqa: F401 — registers tools
import core.tools.system_tools as system_tools  # registers tools; also used for set_planner_port below
from core.task_manager import TaskManager
from core.conversation import ConversationManager
from core.db import get_db
from core.voice.typing_echo import TypingEcho
from core.adapters.win.focus import ensure_target_focused
import asyncio

class VoiceController:
    def __init__(self, ws_server, planner_port=8081):
        self.tts = TTSService()
        self.capture = CapturePipeline()
        self.stt = STTService()
        self.planner = PlannerClient(port=planner_port)
        # LookAtScreenTool needs a PlannerClient of its own (vision calls go
        # through a different endpoint than the main text-only prompt() path
        # — see PlannerClient.analyze_image) but tools are constructed by the
        # registry, not VoiceController, so there's no direct handle to pass
        # one through. Same module-level-state pattern already used for
        # _LAST_ELEMENTS (system_tools.py) rather than threading a new
        # parameter through ToolExecutor.execute for one tool.
        system_tools.set_planner_port(planner_port)
        self.executor = ToolExecutor()
        self.tasks = TaskManager()
        self.conversation = ConversationManager()
        self.ws_server = ws_server
        
        self.listener = WakeListener(
            self.on_wake_word_detected,
            is_speaking_fn=lambda: self.tts.is_playing or (time.time() - self.tts.last_active) < 1.2
        )
        self.state = "idle"
        self.lock = threading.Lock()
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='wake_word'").fetchone()
        self.wake_word = row["value"] if row else "pulse"
        self.feedback_mode = "Standard"
        self.narrate = False  # A3: announce focused-window changes
        self.typing_echo = TypingEcho(self.tts)  # opt-in, off by default, skips password fields
        self.last_spoken = ""

        # Proactive screen-context cache: refreshed in the background on foreground-window
        # change (see _screen_context_loop), not read live on every command. The read itself
        # (a UIA tree walk) already runs in the background this way — instead of paying that
        # cost synchronously after the user finishes speaking, it's usually already done by
        # the time a command arrives, so context injection below becomes a cache read.
        self._screen_cache = {}
        self._screen_cache_lock = threading.Lock()
        self._screen_cache_generation = 0

        # We need a reference to the running event loop for broadcasting
        self.loop = asyncio.get_event_loop()
        
    def _safe_thread(self, target, *args):
        """Wrap a background-thread entry point so an unhandled exception (e.g. a
        transcription containing characters that crash a naive print/log call, or any
        other surprise) can't leave the app silently stuck mid-state forever — it logs
        the error and forces a return to idle instead of just dying silently."""
        def run():
            try:
                target(*args)
            except Exception as e:
                import traceback
                print(f"Unhandled error in {getattr(target, '__name__', target)}: {e}")
                traceback.print_exc()
                try:
                    self._speak_broadcast("Sorry, something went wrong. Let's start over.")
                except Exception:
                    pass
                self.broadcast_state("idle")
        threading.Thread(target=run, daemon=True).start()

    def broadcast_state(self, state: str):
        with self.lock:
            self.state = state
        asyncio.run_coroutine_threadsafe(
            self.ws_server.broadcast({"v": 1, "type": "state", "payload": state}),
            self.loop
        )

    def on_wake_word_detected(self):
        with self.lock:
            if self.state in ("listening", "thinking", "acting"):
                # A task is actively running — barging in here would start a second
                # overlapping session racing the first (found via real testing: this
                # corrupted a multi-step task when ambient noise self-triggered the
                # wake word mid-execution). Only "speaking" is interruptible (barge-in
                # over Pulse's own voice) and "idle" starts fresh.
                return
            print("\n[WAKE WORD DETECTED]")
            was_speaking = self.state == "speaking"
            # Set state to "listening" in THIS SAME critical section as the check
            # above — confirmed real race otherwise: the lock used to release here
            # with state still "idle" until a later, separate broadcast_state() call
            # actually flipped it. A second trigger in that window (voice wake-word
            # firing the same moment the UI button is clicked, e.g.) would see "idle"
            # too and slip past the guard, starting a second overlapping capture
            # session — two concurrent PortAudio streams on the same mic, a known
            # crash risk on Windows.
            self.state = "listening"

        if was_speaking:
            print("Interrupting TTS (Barge-in)...")
            self.tts.cancel()
        asyncio.run_coroutine_threadsafe(
            self.ws_server.broadcast({"v": 1, "type": "state", "payload": "listening"}),
            self.loop
        )
        self._safe_thread(self.handle_capture_session)

    def _vocab_hint(self) -> str:
        """Dynamic contextual biasing (Google-style): feed the STT model real installed
        app names and recently used file names so it favors them over sound-alikes."""
        try:
            conn = get_db()
            apps = [r["name"] for r in conn.execute("SELECT name FROM app_index LIMIT 40")]
            files = [r["name"] for r in conn.execute("SELECT name FROM file_index ORDER BY mtime DESC LIMIT 20")]
            return ", ".join(apps + files)
        except Exception:
            return ""

    def handle_capture_session(self):
        # Stop the wake-word listener's own always-on InputStream before opening
        # capture's — two concurrent PortAudio streams on the same device is a
        # known crash risk on Windows (already worked around for wake-word
        # training; this closes the same gap for every normal command, which runs
        # this exact path on every single utterance, not just an occasional
        # training session). start()/stop() are cheap here — the ONNX model is
        # already loaded, only the audio stream itself gets torn down and rebuilt.
        self.listener.stop()
        try:
            wav_bytes = self.capture.capture_until_silence()
        finally:
            self.listener.start()

        if not wav_bytes:
            print("Nothing captured.")
            self.broadcast_state("idle")
            return
            
        self.broadcast_state("thinking")
        
        # 1. STT — bias decoding toward real app/file names so short commands resolve correctly
        print("Transcribing...")
        text = self.stt.transcribe(wav_bytes, extra_vocab=self._vocab_hint())
        if not text:
            print("Could not understand audio.")
            self.broadcast_state("idle")
            return
            
        print(f"User: {text}")
        asyncio.run_coroutine_threadsafe(
            self.ws_server.broadcast({"v": 1, "type": "transcript", "payload": text}),
            self.loop
        )
        self.process_text(text)

    def handle_text_command(self, text: str):
        """Entry point for text commands from the UI (no mic involved)."""
        with self.lock:
            if self.state not in ("idle", "speaking"):
                return  # busy with a voice session
        if self.tts.is_playing:
            self.tts.cancel()
        asyncio.run_coroutine_threadsafe(
            self.ws_server.broadcast({"v": 1, "type": "transcript", "payload": text}),
            self.loop
        )
        self._safe_thread(self.process_text, text)

    def ask_confirmation(self, question: str) -> bool:
        """Speak a question, listen for yes/no. Re-asks once on unclear answer."""
        import re
        for _ in range(2):
            self.broadcast_state("speaking")
            self.tts.speak(question)
            self.broadcast_state("listening")
            self.listener.stop()
            try:
                wav = self.capture.capture_until_silence()
            finally:
                self.listener.start()
            answer = (self.stt.transcribe(wav) or "").lower() if wav else ""
            if re.search(r"\b(yes|yeah|yep|sure|do it|go ahead|confirm|ok|okay)\b", answer):
                return True
            if re.search(r"\b(no|nope|stop|cancel|don't|abort)\b", answer):
                return False
            question = "Sorry, I didn't catch that. Please say yes or no."
        return False

    def ask_slot(self, question: str, default: str = None, timeout_s: float = 6.0) -> str:
        """Ask for ONE missing piece of information (not yes/no — see ask_confirmation
        for that). Single ask, bounded listen window, never loops/re-asks. No reply
        within timeout, or nothing understood, returns `default` unchanged — callers
        decide what that means (announce-and-proceed if default is a real value,
        park the task if default is None). This is what makes "automate everything
        possible" actually hold: a slot question can never hang forever."""
        self.broadcast_state("speaking")
        self.tts.speak(question)
        self.broadcast_state("listening")
        self.listener.stop()
        try:
            wav = self.capture.capture_until_silence(no_speech_timeout_s=timeout_s)
        finally:
            self.listener.start()
        if not wav:
            return default
        answer = (self.stt.transcribe(wav, extra_vocab=self._vocab_hint()) or "").strip()
        return answer if answer else default

    _STATIC_FOLDERS = ("desktop", "documents", "downloads", "pictures", "music", "videos")

    def _normalize_command(self, text: str) -> str:
        """Deterministic preprocessor for the static lane only — no AI, no model call.
        Strips politeness/filler so natural phrasings ('could you open my downloads
        folder please') still hit the static table, without touching the AI lane's
        own understanding of genuinely ambiguous or rephrased sentences."""
        import re as _re
        t = _re.sub(r"\b(please|could you|can you|would you|for me|kindly|just|quickly|now)\b", "", text)
        return _re.sub(r"\s+", " ", t).strip()

    def _lookup_indexed_app(self, target: str):
        """Exact/alias match against the real installed-app index — deliberately NOT
        fuzzy (that precision-over-recall rule is what keeps the static lane safe: a
        wrong static match executes instantly, a missed one just costs one AI-lane
        round trip). A hit here means the app is proven to be genuinely installed —
        no AI needed to decide whether to try opening it."""
        import json as _json
        try:
            conn = get_db()
            row = conn.execute("SELECT name FROM app_index WHERE name = ?", (target,)).fetchone()
            if row:
                return row["name"]
            for r in conn.execute("SELECT name, aliases FROM app_index"):
                if target in _json.loads(r["aliases"]):
                    return r["name"]
        except Exception:
            pass
        return None

    def _static_route(self, norm_text: str):
        """The static lane's entire routing table. Exact verb + known-entity match
        only — returns None (never a guess) for anything else, which falls through to
        the AI planner in process_text. Adding a new simple command later means adding
        a row here, not a new code path."""
        import re as _re

        m = _re.match(r"^(?:open|show|launch|start|run|pull up|bring up|fire up)\s+(?:up\s+)?(?:my\s+|the\s+)?(.+?)(?:\s+folder)?$", norm_text)
        if m:
            target = m.group(1).strip()
            if target in self._STATIC_FOLDERS:
                return ("open_folder", target)
            app_name = self._lookup_indexed_app(target)
            if app_name:
                return ("open_app", app_name)
            # Any other "<x> folder" phrasing: don't guess-execute against an
            # unindexed name — the AI lane's screen-context + search_file + ask
            # flow is what's actually suited to resolving it.

        m = _re.match(r"^(?:close|quit)\s+(?:my\s+|the\s+)?(.+)$", norm_text)
        if m:
            return ("close_app", m.group(1).strip())

        return None

    def process_text(self, text: str):
        # Direct intents that bypass the planner (reliability > flexibility for these)
        import re as _re
        low = text.lower()
        if _re.search(r"(turn on|enable|start|activate).{0,12}superhero", low):
            self.feedback_mode = "Guided"
            self.narrate = True
            self.typing_echo.enabled = True
            self.play_superhero_chime()
            self._speak_broadcast("Superhero Mode on. I'll narrate, echo your typing, and give you regular check-ins.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(turn off|disable|stop|deactivate).{0,12}superhero", low):
            self.feedback_mode = "Standard"
            self.narrate = False
            self.typing_echo.enabled = False
            self._speak_broadcast("Superhero Mode off.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(start|enable|begin|turn on).{0,12}narrat|narrat.{0,8}\bon\b", low):
            self.narrate = True
            self.broadcast_state("speaking")
            self.tts.speak("Narration on. I'll announce whenever your focused window changes.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(stop|disable|end|turn off).{0,12}narrat|narrat.{0,8}\boff\b", low):
            self.narrate = False
            self.broadcast_state("speaking")
            self.tts.speak("Narration off.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(train|learn|teach).{0,15}(voice|wake)", low):
            self.train_wake_word()
            return
        if _re.search(r"^(repeat|say (that|it) again|what did you say)\b", low):
            self._speak_broadcast(self.last_spoken or "I haven't said anything yet.")
            self.broadcast_state("idle")
            return
        if _re.search(r"^(continue|resume|keep going|where were we)\b", low):
            parked = self.tasks.get_parked_task()
            if not parked:
                self._speak_broadcast("There's nothing waiting — what would you like me to do?")
                self.broadcast_state("idle")
                return
            if parked["pending_slot"] == "continuation":
                # Ran out of round budget, not waiting on an answer — just pick the
                # original goal back up, no question to re-ask.
                self.tasks.unpark_task(parked["id"])
                self._speak_broadcast(f"Picking that back up — {parked['goal']}.")
                self.process_text(parked["goal"])
                return
            # One-line context restatement, then re-ask exactly what was pending —
            # no re-deriving the question, it's stored verbatim from when it parked.
            answer = self.ask_slot(f"Continuing — {parked['goal']}. {parked['pending_question']}")
            if answer:
                self.tasks.unpark_task(parked["id"])
                self.process_text(f"{parked['goal']} — {parked['pending_slot']}: {answer}")
            else:
                # Still nothing — re-park the SAME task rather than losing it or
                # looping the question again right now.
                self.tasks.park_task(parked["id"], parked["pending_slot"], parked["pending_question"])
                self._speak_broadcast("Still no answer — I'll keep holding onto that. Say \"continue\" whenever you're ready.")
                self.broadcast_state("idle")
            return
        m = _re.search(r"^spell(?: the word)? (.+)", low)
        if m:
            word = _re.sub(r"[^a-z0-9]", "", m.group(1))
            self._speak_broadcast(", ".join(word.upper()) if word else "I didn't catch a word to spell.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(speak|talk|read).{0,10}(faster|quicker)|faster please", low):
            self.tts.speed = min(self.tts.speed + 0.15, 1.6)
            self._speak_broadcast("Speaking faster.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(speak|talk|read).{0,10}(slower)|slower please", low):
            self.tts.speed = max(self.tts.speed - 0.15, 0.6)
            self._speak_broadcast("Speaking slower.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(normal|reset|default).{0,10}speed", low):
            self.tts.speed = 1.0
            self._speak_broadcast("Back to normal speed.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(typing|keystroke).{0,10}echo.{0,10}(on|enable)|echo.{0,10}(what|as).{0,10}(type|typing)", low):
            self.typing_echo.enabled = True
            self._speak_broadcast("Typing echo on. I'll speak letters as you type, and skip password fields.")
            self.broadcast_state("idle")
            return
        if _re.search(r"(typing|keystroke).{0,10}echo.{0,10}(off|disable|stop)", low):
            self.typing_echo.enabled = False
            self._speak_broadcast("Typing echo off.")
            self.broadcast_state("idle")
            return
        if _re.search(r"read (my |the )?(screen|everything|page|window)\b", low):
            self.read_everything()
            return

        # Static Action Lane — deterministic only, NO AI in routing. Precision over
        # recall: a wrong static match executes instantly and wrong; a missed one just
        # falls through to the AI planner below at the cost of ~1s. Never guess here.
        norm = self._normalize_command(low)
        route = self._static_route(norm)
        if route:
            kind, target = route
            if kind == "open_folder":
                self.broadcast_state("acting")
                # Rule: resolve BEFORE speaking — never announce "Opening X" before
                # knowing X actually exists (that used to happen, then the resolver
                # would ask "should I search your whole computer?" AFTER already
                # saying it was opening — backwards conversation order).
                resolved_path = self._resolve_folder(target)
                if resolved_path:
                    self.broadcast_state("speaking")  # audio plays now — must stay barge-in interruptible
                    asyncio.run_coroutine_threadsafe(
                        self.ws_server.broadcast({"v": 1, "type": "feedback",
                                                  "text": f"Opening {target}.", "mode": self.feedback_mode}),
                        self.loop
                    )
                    self.tts.speak_hybrid("prefix_opening.wav", f"{target}.")
                    try:
                        os.startfile(resolved_path)
                    except Exception as e:
                        print(f"Error opening folder {resolved_path}: {e}")
                        self._speak_broadcast(f"I found {target}, but couldn't open it.")
                    self.broadcast_state("idle")
                    return
                # Unresolved: never guess a path or speak a false "opening" — fall
                # through silently to the AI planner below, which can search/ask.

            elif kind == "close_app":
                # Routed through the SAME ToolExecutor path as everything else, so the
                # confirm gate actually applies — close_app is permission_level="confirm"
                # by design; calling tool.execute() directly here used to skip that
                # entirely and force-close apps with zero confirmation.
                self.broadcast_state("acting")
                result, status = self._execute_with_heartbeat("close_app", {"name": target})
                if status == "needs_confirmation":
                    if self.ask_confirmation(f"This will close {target}. Should I continue?"):
                        self.broadcast_state("acting")
                        result, status = self._execute_with_heartbeat("close_app", {"name": target}, user_confirmed=True)
                    else:
                        self._speak_broadcast(f"Okay, leaving {target} open.")
                        self.broadcast_state("idle")
                        return
                if status == "success":
                    self._speak_broadcast(f"Closed {target}.")
                else:
                    self._speak_broadcast(result.get("error") or f"I couldn't close {target}.")
                self.broadcast_state("idle")
                return

            elif kind == "open_app":
                # `target` here is already a proven-installed app name from the index
                # (see _lookup_indexed_app) — no AI needed to decide whether to try.
                # Execute first, speak the real outcome after (no separate "resolve"
                # phase exists for apps the way it does for folders — the OS launch
                # attempt itself IS the resolution).
                self.broadcast_state("acting")
                result, status = self._execute_with_heartbeat("open_app", {"name": target})
                if status == "success":
                    self._speak_broadcast(f"Opening {target}.")
                else:
                    self._speak_broadcast(result.get("error") or f"I couldn't open {target}.")
                self.broadcast_state("idle")
                return

        self.broadcast_state("thinking")
        # 2. Planning
        task_id = self.tasks.create_task(text)
        self.tasks.append_history(task_id, {"role": "user", "content": text})

        from core.planner.prompts import get_system_prompt
        system_prompt = get_system_prompt(feedback_mode=self.feedback_mode)

        # Inject conversation context
        context_str = self.conversation.get_context_string()
        if context_str:
            system_prompt += f"\n\n{context_str}\n\nIMPORTANT: Use the history above to resolve ambiguous references (like 'the second one', 'that file', 'it')."

        # Ambient screen context: what's in front of the user right now. This is the
        # whole point of a screen-aware assistant — "open my email folder" while the
        # Desktop is open on screen should be resolved from the VISIBLE items (the
        # 'emails' folder is right there), not a blind filesystem search.
        #
        # This used to call read_screen() live, right here, on every single command —
        # a synchronous UIA tree walk paid AFTER the user finished speaking. _screen_
        # context_loop now keeps this refreshed in the background on window-focus change,
        # so by the time a command arrives it's normally already ready — this is a cache
        # read, not a walk. Only the very first command of a session (cache still empty,
        # background loop hasn't had a chance to run yet) falls back to a live read.
        try:
            with self._screen_cache_lock:
                screen = dict(self._screen_cache)
            # Verify the cache is actually for the window that's on screen RIGHT NOW,
            # not a stale result — confirmed real bug: rapidly switching Desktop ->
            # another app -> back to Desktop could leave the cache holding the OTHER
            # app's content if its (slower) background walk finished after Desktop's.
            # A cheap foreground-window check catches that; a real mismatch forces a
            # live re-read instead of silently acting on the wrong window's items.
            import win32gui as _win32gui
            current_title = _win32gui.GetWindowText(_win32gui.GetForegroundWindow())
            if not screen or (current_title and screen.get("window") != current_title):
                screen = registry.get_tool("read_screen").execute({})
            if screen.get("success"):
                items = screen.get("controls", [])
                system_prompt += (
                    f"\n\nCURRENT SCREEN (focused window: {screen.get('window')}):\n" + "\n".join(items) +
                    "\nIf the user's request refers to something visible above (a folder, file, button, link), "
                    "act on THAT — click_element with its [N] number — rather than searching blindly."
                )
        except Exception:
            pass

        step_results = self._run_task_loop(task_id, text, system_prompt)
        # _pending_question stays True only when _act_observe just parked the task
        # (an unanswered clarifying question) — must not stomp that 'waiting_input'
        # status back to 'completed' right after setting it.
        if not getattr(self, "_pending_question", False):
            self.tasks.update_task_status(task_id, "completed", str(step_results))
        self.conversation.add_exchange(text, {"speak": self.last_spoken, "plan": []}, step_results)
        plan_steps = step_results  # for the "did anything actually run?" check below

        # NOTE: no separate _narrate_results() call here anymore. _run_task_loop already
        # speaks a summary each round via the planner's own "speak" text as it works —
        # calling _narrate_results afterward on the same step_results caused genuine
        # double-narration (found via real testing: "I see you've already typed X" from
        # the loop, immediately followed by a redundant "Typed into Text editor" from
        # this second pass over the same result) which read as a confusing interruption.

        # The constant "What would you like me to do now?" follow-up is specifically an
        # accessibility feature (continuous feedback for a blind user who can't glance
        # at the screen to know they're done) — it's Superhero Mode only. In normal
        # mode a completed task just completes, silently. A REQUIRED clarifying question
        # (e.g. "what should I name the file, and where?") is a different thing — that's
        # about correctness, not chattiness, and fires in every mode via _pending_question.
        superhero = self.feedback_mode == "Guided"
        if plan_steps and not getattr(self, "_pending_question", False) and superhero:
            self._followup_or_idle()
        else:
            self.broadcast_state("idle")

    # Round budgets raised 10->24->50 (2026-07-27, second raise per direction:
    # "limit only when one step is repeated many times, not for any other kind
    # of execution"). A numbered ceiling should never be the thing that decides
    # whether legitimate, varied work gets to finish — only genuine lack of
    # progress should. That's what stuck detection is for (below): the
    # ORIGINAL loop-detector catches the exact-repeat case, and a NEW
    # complementary one (consecutive_no_progress, driven by the "expectation_met"
    # TVAE field) now catches the case that slipped through it — different
    # actions each round that still never produce the expected effect (this
    # exact pattern burned 20+ rounds on Word: never the same action twice,
    # never real progress either). With THAT as the real stopping signal, this
    # ceiling goes back to being a pure runaway-safety net, sized so it is
    # essentially never hit by legitimate work.
    def _run_task_loop(self, task_id, goal, system_prompt, max_iterations=50):
        """Plan-and-execute: for a complex multi-part goal the planner first returns a
        spoken-language `task_list` breakdown; we persist it, narrate progress ("Step 2
        of 4..."), and run each step through its own act-observe subloop. Single-part
        goals skip straight to the subloop. This is the standard hierarchical agent
        pattern (decompose -> execute-with-replanning per step) — it keeps long jobs
        like "install python" on track without hardcoding any specific workflow."""
        self._pending_question = False
        response = self.planner.prompt(user_text=goal, system_prompt=system_prompt,
                                         schema=registry.get_planner_schema())
        if not response:
            self._speak_broadcast("I'm sorry, I ran into a problem thinking that through.")
            return []

        task_list = [s for s in (response.get("task_list") or []) if s.strip()]
        if len(task_list) > 1:
            import json as _json
            conn = get_db()
            with conn:
                conn.execute("UPDATE tasks SET plan_json = ?, current_step = 0 WHERE id = ?",
                             (_json.dumps(task_list), task_id))
            n = len(task_list)
            # Don't speak the internal step breakdown ("I'll do this in N steps...",
            # "Step 2 of 4...") — that's our own decomposition, not something the
            # user asked to hear. Each sub-goal's own act_observe round already
            # narrates what it's actually doing via its own "speak" text.
            all_results = []
            # Snapshot BEFORE running: if the goal's named file already existed
            # (a re-save of an existing file), its mere existence can't mean
            # "goal complete" — only a file that appears DURING this run can.
            goal_done_at_start = self._goal_file_saved(goal)
            for i, step_goal in enumerate(task_list):
                with conn:
                    conn.execute("UPDATE tasks SET current_step = ? WHERE id = ?", (i, task_id))
                sub_goal = (f"OVERALL GOAL: {goal}\nCURRENT STEP ({i + 1} of {n}): {step_goal}\n"
                            f"COMPLETED SO FAR: {task_list[:i]}\nDo only this step now.")
                results = self._act_observe(task_id, sub_goal, system_prompt, max_rounds=50)
                all_results.extend(results)
                # A step's act-observe loop often (legitimately) completes MORE
                # than its own step — confirmed live: step 1 finished the entire
                # goal, then steps 2-3 re-ran the already-done work (re-typed,
                # re-saved) because nothing checked the overall end state. If the
                # goal's objective end state is now reached, stop here.
                if not goal_done_at_start and self._goal_file_saved(goal):
                    return all_results
                if getattr(self, "_pending_question", False):
                    # This step parked awaiting an answer — stop here instead of
                    # marching on to later steps as if it had resolved.
                    return all_results
                if any(isinstance(r, dict) and r.get("cancelled") for r in results):
                    return all_results
                if results and isinstance(results[-1], dict) and "error" in results[-1]:
                    if not self.ask_confirmation(
                            f"Step {i + 1} hit a problem: {str(results[-1]['error'])[:120]}. Should I continue with the remaining steps?"):
                        self._speak_broadcast("Okay, stopping here.")
                        return all_results
            return all_results

        # Single-part goal: reuse the response we already have as round one.
        return self._act_observe(task_id, goal, system_prompt,
                                  initial_response=response, max_rounds=max_iterations)

    def _verify_saved_file(self, goal: str) -> bool:
        """Objective, code-level check for save/write goals — don't just trust the
        model's self-reported task_step_done. Confirmed live: a click on a save
        dialog's confirm button returned {"success": true} and the model declared
        the goal done immediately after, without ever re-reading the screen to
        confirm the dialog actually closed (the button was oddly labeled "Open",
        a known quirk of Windows' shared common-dialog control) — the file never
        actually landed on disk. Same principle as code-level loop detection:
        the model missing its own mistake is expected sometimes, so verify
        externally rather than trust the self-report. Returns True (nothing to
        check, trust the model) if the goal doesn't name a specific file."""
        import re
        from pathlib import Path
        # For a multi-step sub-goal, only the CURRENT STEP matters here — an
        # earlier step (e.g. "Type the text") legitimately finishes before any
        # file exists, and "save" appearing elsewhere in the embedded OVERALL
        # GOAL text shouldn't block THAT step's real completion.
        m_step = re.search(r'CURRENT STEP \(\d+ of \d+\):\s*(.+)', goal)
        check_text = m_step.group(1) if m_step else goal
        if "save" not in check_text.lower():
            return True
        m = re.search(r'\b([\w\-]+\.\w{2,5})\b', goal)
        if not m:
            return True
        filename = m.group(1).strip()
        return self._file_on_disk(filename)

    def _file_on_disk(self, filename: str) -> bool:
        """Shared disk check for save verification. Includes the OneDrive-
        redirected folders — confirmed live: Word's preview edition saves ONLY
        to OneDrive, and Windows itself commonly redirects Desktop/Documents
        into OneDrive, so a save can be genuinely complete without the file
        ever appearing under the classic local folders."""
        from pathlib import Path
        home = Path.home()
        folders = [home / "Desktop", home / "Documents", home / "Downloads", home,
                   home / "OneDrive" / "Desktop", home / "OneDrive" / "Documents", home / "OneDrive"]
        return any((f / filename).exists() for f in folders)

    def _goal_file_saved(self, goal: str) -> bool:
        """STRICT objective completion check: True ONLY when the goal is a save
        goal naming a specific file AND that file now exists on disk. Unlike
        _verify_saved_file (whose True also means 'nothing to check'), this never
        returns True by default — used to SKIP remaining task-list steps once the
        end state is objectively reached. Confirmed live: step 1's act-observe
        loop completed the entire goal (file saved + verified), then the outer
        task-list loop marched into steps 2 and 3 anyway, re-typing and re-saving
        the already-finished document."""
        import re
        if "save" not in goal.lower():
            return False
        m = re.search(r'\b([\w\-]+\.\w{2,5})\b', goal)
        if not m:
            return False
        return self._file_on_disk(m.group(1).strip())

    def _save_call_missing_info(self, goal: str, steps: list) -> str:
        """Purely code-driven backstop for save_file specifically — confirmed
        live the model can self-report missing_info: "" (its own judgment:
        "nothing's missing") on the very round it calls save_file with a
        completely made-up filename, when the user never gave one at all. The
        missing_info mechanism only intercepts what the model itself flags, so
        a self-report that misses the gap entirely defeats it — same lesson as
        loop detection: don't trust the model to notice its own mistake for
        something this well-defined and already twice-reported as broken.
        Checks the actual goal text in code instead of trusting any judgment
        call. Returns "" if a save_file step isn't being proposed this round,
        or if the goal already contains both a filename and a location."""
        if not any(s.get("tool") == "save_file" for s in steps):
            return ""
        import re
        has_filename = bool(re.search(r'\b[\w\-]+\.\w{2,5}\b', goal))
        has_location = bool(re.search(r'\b(desktop|documents|downloads|pictures|music|videos|folder|drive|[a-zA-Z]:\\)\b', goal, re.I))
        missing = []
        if not has_filename:
            missing.append("file name")
        if not has_location:
            missing.append("save location")
        return " and ".join(missing)

    def _document_identity_gate(self, goal: str, all_results: list):
        """One-time-per-task, code-driven precondition check — confirmed live
        (2026-07-28): Windows 11 / Office apps resume a previous session's
        document even on a genuinely fresh open_app, and the model reasonably
        but wrongly treats that resumed content as the task's own starting
        point — worst when the resumed doc's name happens to coincidentally
        match a 'save it as' target named later in the goal (it did, live:
        goal said "...save it as word_test.docx", Word resumed an unrelated
        stale "word_test" doc from an EARLIER test run, and the model typed
        straight into it instead of starting fresh). Modeled directly on
        Claude Code's own Read-before-Edit gate — Anthropic's stated reason
        for that gate is "prevents editing based on stale memory... if you
        write, whatever was there is gone" — same failure shape, same fix:
        verify identity BEFORE the destructive write, in code, not hoped-for
        via prompt (same lesson as _save_call_missing_info above: the model's
        own judgment isn't trustworthy for something this well-defined).
        Returns None if there's nothing to check yet (no read_screen result
        seen so far this task — caller should NOT count this as having
        checked, and should try again once one exists), otherwise "" (checked,
        fine) or a corrective hint string (checked, found a mismatch). Caller
        decides WHEN to call this (only when this round's proposed steps
        include fill_element)."""
        import re
        window_title = ""
        for r in reversed(all_results):
            if isinstance(r, dict) and "window" in r:
                window_title = (r.get("window") or "").strip()
                break
        if not window_title:
            return None  # haven't read the screen yet this task — can't check yet
        # A document still bearing a generic default name was never given a
        # real identity by anyone (this task or a prior one) — nothing stale
        # to protect, across apps generically (Word/PowerPoint/Notepad++-style
        # "Document1"/"Untitled"/"Book1"/"Presentation1" defaults).
        if re.search(r'^(document\d*|untitled\d*|book\d*|new \d+|presentation\d*)\b', window_title, re.I):
            return ""
        # Explicit "I want whatever this app resumed" intent — the opposite
        # of the bug this gate exists for. Honor it, don't second-guess it.
        # NOTE: bare "resume" is deliberately excluded — "resume.docx" is an
        # extremely common real filename, and \bresume\b matches inside it
        # (word boundary triggers on the '.'), which would misfire "explicit
        # resume intent" on literally any goal that just happens to open a
        # file called resume.docx. Caught in testing before this shipped.
        if re.search(r'\b(continue where|resume (my|the|our)|pick up where|last session|previous (document|file|work)|last document)\b', goal, re.I):
            return ""
        # Does the goal actually ask to open/edit/continue a SPECIFIC existing
        # file (not just "save it as X" — a save target is a destination, not
        # permission to build on whatever's already open, even if the same
        # string happens to appear in both roles).
        open_match = re.search(r'\b(?:open|edit|continue|update)\b[^.]{0,30}?(\b[\w\-]+\.\w{2,5}\b)', goal, re.I)
        if open_match:
            referenced = open_match.group(1).lower().split('.')[0]
            if referenced and referenced in window_title.lower():
                return ""  # resumed doc matches what was actually asked to be opened
            return (
                f"The document currently open ('{window_title}') doesn't match the file you were asked to work "
                f"with ('{open_match.group(1)}'). Don't edit what's shown — verify by opening the correct file "
                "by name before making any changes."
            )
        return (
            f"The document currently open ('{window_title}') isn't blank — it looks like this app resumed a "
            "previous session, not something related to what you were asked to create. Even if its name happens "
            "to resemble something you'll save as later, that's coincidental leftover state, not yours to build "
            "on. Start a genuinely new document first (this app's own 'New'/'Blank document', or a shortcut like "
            "ctrl+n) before typing anything."
        )

    def _act_observe(self, task_id, goal, system_prompt, initial_response=None, max_rounds=50):
        """Reason -> act -> observe -> re-plan loop. Runs whatever the planner proposes,
        feeds it the REAL observed results, asks again until it declares done — because
        a single upfront plan can't know things it hasn't seen yet (element numbers,
        found file paths, dialog contents)."""
        all_results = []
        user_text = goal
        # Loop detection (confirmed via research: this must happen in code, not
        # the model — "the model doesn't recognize the pattern" itself). Standard
        # definition: the exact same tool+params 3 times running. First time it's
        # caught, force a corrective hint instead of executing a 4th time; if it
        # happens AGAIN after that hint, stop trying and park rather than burn the
        # whole round budget looping.
        recent_actions = []
        stuck_hint_given = False
        asked_gaps = set()  # normalized missing_info strings already asked about this task
        identity_checked = False  # see _document_identity_gate — fires at most once per task
        # Confirmed live: even when told exactly what to ask and why, the model's
        # own missing_info_required self-report on the ASKING round is still
        # unreliable — it can reason "I could default this" and then still set
        # required: true. Set when the code-driven save-file backstop (known,
        # structurally, to always be defaultable) forces a question, so the
        # silence-handling branch below can trust this instead of re-asking the
        # model to self-report correctly a second time.
        pending_is_defaultable = False
        last_expected_effect = ""  # carried into next round's context, see below
        # Complementary to the exact-repeat loop-detector above: catches the
        # case it structurally can't — different actions every round that
        # still never produce the expected effect (confirmed live: 20+ rounds
        # on Word, never repeating an action, never actually progressing
        # either — a round-count ceiling was the only thing that eventually
        # stopped it). Driven by the "expectation_met" TVAE field, which is
        # exactly the honest self-report needed for this: a real semantic
        # progress signal instead of a syntactic repeat check.
        consecutive_no_progress = 0
        NO_PROGRESS_HINT_AT = 5
        NO_PROGRESS_PARK_AT = 9
        save_verify_failures = 0  # escalating counter, see the task_done check below
        for iteration in range(max_rounds):
            if initial_response is not None and iteration == 0:
                response = initial_response
            else:
                response = self.planner.prompt(user_text=user_text, system_prompt=system_prompt,
                                                 schema=registry.get_planner_schema())
            if not response:
                self._speak_broadcast("I'm sorry, I ran into a problem thinking that through.")
                break

            expectation_met = (response.get("expectation_met") or "").strip().lower()
            if expectation_met in ("", "yes", "n/a"):
                consecutive_no_progress = 0
            else:
                consecutive_no_progress += 1
                if consecutive_no_progress == NO_PROGRESS_HINT_AT:
                    user_text = (
                        f"GOAL: {goal}\nYou've now gone {NO_PROGRESS_HINT_AT} rounds in a row where your own "
                        "expectation_met said things weren't working out, even though you tried different "
                        "things each time — that's still not real progress. Step back: is there a fundamentally "
                        "different way to approach this (a different tool, a keyboard shortcut instead of "
                        "hunting for buttons, asking the user something), or is this genuinely blocked? If truly "
                        "blocked, say so plainly via 'speak' with an empty plan instead of continuing to try "
                        "variations that keep not working."
                    )
                    continue
                if consecutive_no_progress >= NO_PROGRESS_PARK_AT:
                    self.tasks.park_task(task_id, "continuation", goal)
                    self._pending_question = True
                    self._speak_broadcast(
                        'I\'ve tried several different ways and none of them are working — say "continue" '
                        'and I\'ll take another approach, or let me know what\'s actually happening on screen.'
                    )
                    break

            speak_text = response.get("speak", "")
            if speak_text and speak_text != self.last_spoken:
                self._speak_broadcast(speak_text)

            steps = response.get("plan", [])
            # Explicit completion signal — replaces inferring "done" from an empty
            # plan, which is what let a task declare itself finished right after
            # typing text, without ever attempting to save. The model must commit
            # to this deliberately every round rather than it falling out
            # incidentally from "no more actions occurred to me right now".
            task_done = bool(response.get("task_step_done", False))
            if task_done and not self._verify_saved_file(goal):
                # Escalating, bounded verification failure — confirmed live:
                # Word's preview edition can ONLY save to OneDrive cloud, so no
                # amount of retrying puts the file where the user asked; the old
                # single fixed message looped the model on an unwinnable check.
                # 1st failure: retry guidance. 2nd: name the likely cause and
                # offer the honest-report exit. 3rd: stop and tell the user
                # plainly instead of burning the rest of the round budget.
                save_verify_failures += 1
                if save_verify_failures >= 3:
                    self.tasks.park_task(task_id, "continuation", goal)
                    self._pending_question = True
                    self._speak_broadcast(
                        "I finished the steps, but I can't confirm the file landed where you asked — "
                        "this app may only save to its own cloud storage. Say \"continue\" and I'll try again, "
                        "or check the app's save location yourself."
                    )
                    break
                task_done = False
                if save_verify_failures == 1:
                    user_text = (
                        f"GOAL: {goal}\nYou reported this as done, but the file named in the goal isn't actually "
                        "on disk (checked Desktop, Documents, Downloads, and OneDrive) — the save did NOT really "
                        "complete despite what the last action's result said. Read the screen now to see what's "
                        "actually there (a dialog that didn't close, an error, a different filename) and fix it."
                    )
                else:
                    user_text = (
                        f"GOAL: {goal}\nSecond failed check: the file is STILL not in any expected location. "
                        "Likely cause: this app saved somewhere else entirely (its own cloud storage, or a "
                        "location its dialog defaulted to). Do ONE of: use the save dialog's 'More options' / "
                        "browse control to explicitly reach the exact requested folder — or, if this app "
                        "genuinely can't save there, STOP (empty plan) and tell the user plainly where the "
                        "file actually got saved instead. Do not keep re-saving to the same wrong place."
                    )
                continue
            # Confirmed live: the model can notice info is missing (e.g. no save
            # name/location given) and still choose to act on a silent assumption
            # instead of asking — a prompt rule saying "ask once" isn't a
            # guarantee, same lesson as loop detection. If it's about to act
            # (non-empty plan) on a gap it just told us about, and we haven't
            # already asked about this exact gap this task, force the ask now
            # instead of trusting it to remember to ask on its own.
            missing_info = (response.get("missing_info") or "").strip()
            from_code_fallback = False
            if not missing_info:
                # Model's own self-report missed it — fall back to the
                # deterministic, code-only check for the one case this has
                # been confirmed to matter most for (save_file with no real
                # filename/location ever given).
                missing_info = self._save_call_missing_info(goal, steps)
                from_code_fallback = bool(missing_info)
            if missing_info and steps and missing_info.lower() not in asked_gaps:
                asked_gaps.add(missing_info.lower())
                # Known structurally, not from the model: save_file's filename/
                # location always have a safe default. Don't ask the model to
                # self-report this — it's the exact field confirmed unreliable.
                required = False if from_code_fallback else bool(response.get("missing_info_required", False))
                if from_code_fallback:
                    pending_is_defaultable = True
                user_text = (
                    f"GOAL: {goal}\nBefore proceeding, you noted this is missing: '{missing_info}'."
                    + (" There's no safe default — you must ask the user for it now."
                       if required else
                       " You may use a sensible default if the user doesn't answer, but ask ONCE first.")
                    + " Return this round with an EMPTY plan and a 'speak' that asks for it, ending in '?'."
                )
                continue
            if not steps:
                is_question = speak_text.rstrip().endswith("?")
                if is_question and missing_info:
                    asked_gaps.add(missing_info.lower())
                self._pending_question = is_question and not task_done
                if task_done or not is_question:
                    break
                # This used to just speak the question and go idle, hoping the
                # user's NEXT utterance (a fresh wake-word trigger) happened to
                # answer it — no bounded listen, and if they didn't reply (or
                # said something else entirely), the whole task's context was
                # silently lost. Now: actually listen for the answer right here;
                # no reply within the window parks the task (resumable via
                # "continue") instead of dropping it.
                self.broadcast_state("listening")
                self.listener.stop()
                try:
                    wav = self.capture.capture_until_silence(no_speech_timeout_s=6.0)
                finally:
                    self.listener.start()
                answer = self.stt.transcribe(wav, extra_vocab=self._vocab_hint()) if wav else ""
                if answer:
                    asyncio.run_coroutine_threadsafe(
                        self.ws_server.broadcast({"v": 1, "type": "transcript", "payload": answer}),
                        self.loop
                    )
                    self._pending_question = False
                    pending_is_defaultable = False
                    user_text = f"GOAL: {goal}\nPREVIOUS QUESTION: {speak_text}\nUSER'S ANSWER: {answer}"
                    continue
                # No answer. DEFAULTABLE means silence -> use defaults, per the
                # locked ask-once-then-fallback design — NOT a parked task. Only
                # genuinely REQUIRED questions (recipient etc.) park on silence.
                # Trust pending_is_defaultable (set in code when OUR OWN backstop
                # forced this question, since we know structurally it's a safe-
                # default case) over the model's own missing_info_required
                # self-report — confirmed live that self-report is unreliable
                # even on the asking round itself, right after being told why.
                was_defaultable = pending_is_defaultable or not response.get("missing_info_required", True)
                pending_is_defaultable = False
                if was_defaultable:
                    self._pending_question = False
                    user_text = (f"GOAL: {goal}\nPREVIOUS QUESTION: {speak_text}\nUSER'S ANSWER: (no reply — "
                                 "use the defaults: Desktop, and pick a short sensible filename yourself. "
                                 "Proceed now, do not ask again.)")
                    continue
                self.tasks.park_task(task_id, "clarification", speak_text)
                self._speak_broadcast('I\'ll hold onto that — say "continue" whenever you\'re ready.')
                break

            # Precondition gate, fires at most once per task — see
            # _document_identity_gate. Only relevant when this round is about
            # to type real content; checked here (not earlier) so it always
            # sees the freshest read_screen result, and only once so it can't
            # turn into a repeating nag if the model's correction isn't
            # immediately perfect (the existing expectation_met/stuck-detector
            # machinery already covers continued lack of progress from here).
            if not identity_checked and any(s.get("tool") == "fill_element" for s in steps):
                identity_hint = self._document_identity_gate(goal, all_results)
                if identity_hint is not None:
                    identity_checked = True  # only counts as checked once there was something to check
                    if identity_hint:
                        user_text = f"GOAL: {goal}\n{identity_hint}"
                        continue

            import json as _json2
            stuck = False
            for step in steps:
                tool_name, params = step.get("tool"), step.get("params", {})
                # Drop falsy-default optional keys (e.g. "submit": False) before
                # signing — the model inconsistently includes/omits these between
                # calls, which made byte-identical repeats of the SAME action look
                # like different signatures and let a real stuck-loop slip past
                # this check entirely (confirmed live: fill_element repeated 4x
                # on the same index/value, alternating submit:false vs omitted).
                normalized = {k: v for k, v in params.items() if v not in (False, None, "")}
                sig = (tool_name, _json2.dumps(normalized, sort_keys=True))
                recent_actions.append(sig)
                if len(recent_actions) >= 3 and recent_actions[-1] == recent_actions[-2] == recent_actions[-3]:
                    stuck = True
                    break
                self.broadcast_state("acting")
                asyncio.run_coroutine_threadsafe(
                    self.ws_server.broadcast({"v": 1, "type": "action", "tool": tool_name, "params": params}),
                    self.loop
                )
                print(f"Action: {tool_name}({params})")
                result, status = self._execute_with_heartbeat(tool_name, params)
                if status == "needs_confirmation":
                    if self.ask_confirmation(f"This will run {tool_name.replace('_', ' ')}. Should I continue?"):
                        self.broadcast_state("acting")
                        result, status = self._execute_with_heartbeat(tool_name, params, user_confirmed=True)
                    else:
                        self._speak_broadcast("Okay, cancelled.")
                        all_results.append({"cancelled": True})
                        return all_results
                print(f"Result: {result} (Status: {status})")
                all_results.append(result)
                self.tasks.append_history(task_id, {"role": "tool", "tool": tool_name, "params": params, "result": result})

            if stuck:
                if stuck_hint_given:
                    # Repeated the SAME action 3 times, got an explicit corrective
                    # hint, and immediately did it again anyway — don't keep
                    # feeding it more chances. Park (resumable via "continue") and
                    # say so plainly rather than framing it as failure.
                    self.tasks.park_task(task_id, "continuation", goal)
                    self._pending_question = True
                    self._speak_broadcast('I\'m stuck on part of that — say "continue" and I\'ll try a different way.')
                    break
                stuck_hint_given = True
                recent_actions.clear()
                user_text = (
                    f"GOAL: {goal}\nYou just repeated the EXACT same action ({tool_name} with {params}) three "
                    "times in a row with no progress — repeating it again will not work either. Try a "
                    "genuinely different approach now: a different tool, different parameters, or re-read the "
                    "screen for up-to-date element numbers before acting. If you're truly blocked, say so "
                    "plainly via 'speak' with an empty plan instead of retrying the same thing."
                )
                continue

            import json as _json
            # Confirmed live TWICE now, in opposite directions: (1) a flat 1500-char
            # slice from the front silently dropped the most RECENT results once one
            # verbose read_screen filled the budget — fill_element/send_keys success
            # never reached the model, so it repeated them thinking they hadn't run.
            # (2) switching to "last 4 results only" then dropped an OLDER-but-still-
            # relevant result (the actual "Typed into Text editor" confirmation) once
            # enough later actions piled up — the model concluded the content was
            # NEVER typed and cancelled the save dialog to redo it. Windowing from
            # either end is fragile for a goal whose real length isn't known in
            # advance. Fix: no windowing or char cap at all — keep the FULL history.
            # Each entry is already bounded at generation time (read_screen's own
            # control/text-count limits), so this stays well inside the 32768-token
            # context for any realistic task; the model needs the complete record
            # to reliably track what it's actually done, not a guessed-length slice.
            expectation_line = (
                f"WHAT YOU EXPECTED TO HAPPEN: {last_expected_effect}\n" if last_expected_effect else ""
            )
            last_expected_effect = response.get("expected_effect", "") or ""
            user_text = (
                f"GOAL: {goal}\n{expectation_line}"
                f"ACTIONS YOU (PULSE) JUST PERFORMED AND THEIR RESULTS (most recent last): {_json.dumps(all_results)}\n"
                + ("Compare the REAL results above against what you expected — set expectation_met accordingly "
                   "next round. " if expectation_line else "")
                + "These are things YOU just did, not things the user or anyone else did — narrate them as "
                "'I did X' / 'I've typed X', never as 'I see X is already there'. Continue only if more steps "
                "are genuinely needed — use the REAL results above (actual file paths, actual numbered "
                "elements from a read_screen if one just ran; if you need to interact with something and "
                "haven't read the screen since it last changed, read it again first). If a step failed, "
                "decide whether to retry differently or tell the user what went wrong instead of pretending "
                "it worked. If the goal is fully complete, return an empty plan and a brief closing 'speak' "
                "confirming what you did — do not repeat something you already said this task."
            )
            # Direct, round-specific nudge (confirmed live: the model, after a
            # successful un-submitted fill_element, was re-reading the screen
            # defensively instead of trusting its own "success" result and moving
            # to save). Earlier versions of this nudge dictated raw keystrokes
            # (ctrl+s / enter) and MISFIRED depending on dialog state — now that
            # save_file exists as a single deterministic primitive, just point at
            # it; it's state-independent (works whether or not a dialog is open).
            if (tool_name == "fill_element" and not params.get("submit")
                    and "error" not in result and "save" in goal.lower()):
                already_saved = any(
                    isinstance(r, dict) and str(r.get("message", "")).startswith("Saved to")
                    for r in all_results
                )
                if not already_saved:
                    user_text += (
                        "\nThe typing above already succeeded — its result said so. Do NOT read_screen to "
                        "double check it. Your very next action must be the save_file tool with the filename "
                        "(and folder) from the goal — it handles the entire save dialog itself."
                    )
            if task_done:
                # Model explicitly declared this step done even though it also ran
                # actions this round — trust the explicit signal rather than forcing
                # an extra round just to get an empty-plan confirmation.
                break
        else:
            # Ran out of round budget without an explicit done signal. The old
            # phrasing here ("taking more steps than I expected") read as the app
            # admitting it's incapable — confirmed real complaint. Park it
            # (resumable via "continue", same mechanism as a pending question,
            # just without one to re-ask — see pending_slot=="continuation" above)
            # and describe it as ongoing progress, not defeat.
            self.tasks.park_task(task_id, "continuation", goal)
            self._pending_question = True
            self._speak_broadcast('I\'ll keep working on that — say "continue" and I\'ll pick up where I left off.')
        return all_results

    def _narrate_results(self, step_results) -> str:
        info = [r for r in step_results if isinstance(r, dict)
                and ({'matches', 'focused_window', 'open_windows', 'error', 'message', 'results', 'controls'} & set(r))]
        if not info:
            return ""
        parts = []
        unrecognized = []
        for r in info:
            if "error" in r:
                parts.append(r["error"])
            elif "controls" in r:
                ctrls = r.get("controls", [])
                names = [c.split(": ", 1)[1] if ": " in c else c for c in ctrls[:8]]
                if names:
                    parts.append(f"In {r.get('window', 'this window')}, I found {len(ctrls)} elements: " + ", ".join(names) + (", and more" if len(ctrls) > 8 else "") + ".")
                else:
                    parts.append(f"{r.get('window', 'This window')} has no clickable elements or text I could read.")
            elif "results" in r:
                # Raw search snippets genuinely need synthesis into one natural answer —
                # this is exactly the case that should use the LLM, not a template.
                import html as _html
                r = dict(r)
                r["results"] = [_html.unescape(s) for s in r["results"]]
                unrecognized.append(r)
            elif "matches" in r:
                names = [m["name"] for m in r["matches"][:5]]
                parts.append(f"Found {len(r['matches'])}: " + ", ".join(names) + ".")
            elif "focused_window" in r:
                extra = f" Also open: {', '.join(r['open_windows'][:5])}." if r.get("open_windows") else ""
                parts.append(f"You're in {r['focused_window']}.{extra}")
            elif "message" in r:
                parts.append(str(r["message"]))
            else:
                unrecognized.append(r)
        if unrecognized:
            import json as _json
            from core.planner.prompts import get_system_prompt
            resp = self.planner.prompt(
                user_text="TOOL RESULTS: " + _json.dumps(unrecognized)[:1500] + "\nBriefly tell the user the outcome in natural speech.",
                system_prompt=get_system_prompt(feedback_mode=self.feedback_mode),
                schema=registry.get_planner_schema()
            )
            if resp and resp.get("speak"):
                parts.append(resp["speak"])
        return " ".join(parts)

    def _followup_or_idle(self):
        """Continuous conversation: after finishing a real task, ask what's next and keep
        listening without requiring the wake word again — the standard pattern in
        Alexa/Google-style assistants, and far less friction for accessibility use.
        In Superhero (Guided) mode a blind user has no other way to know where they are,
        so silence is never the answer: ask what to do HERE, and on no reply read every
        element on the current screen to orient them, then listen once more."""
        superhero = self.feedback_mode == "Guided"
        self._speak_broadcast("What would you like me to do here?" if superhero else "What would you like me to do now?")
        self.broadcast_state("listening")
        timeout = 4.0 if superhero else 6.0
        self.listener.stop()
        try:
            wav = self.capture.capture_until_silence(no_speech_timeout_s=timeout)
        finally:
            self.listener.start()
        if self.capture._abort.is_set():
            # Explicitly cancelled (not a natural no-speech timeout) — e.g. a new
            # command arrived and interrupted the wait. Go straight to idle, no
            # "I didn't hear you" / screen-read fallback speech. Found via real testing:
            # without this check, cancel fell through to the read-screen fallback and
            # kept the app busy, causing the next command to arrive mid-flow.
            self.broadcast_state("idle")
            return
        text = self.stt.transcribe(wav, extra_vocab=self._vocab_hint()) if wav else ""
        if not text and superhero:
            # Orient the user instead of going quiet: full read of the current screen.
            self._speak_broadcast("No reply — let me tell you what's on your screen right now.")
            self._read_everything_flow()
            self.broadcast_state("listening")
            self.listener.stop()
            try:
                wav = self.capture.capture_until_silence(no_speech_timeout_s=timeout)
            finally:
                self.listener.start()
            if self.capture._abort.is_set():
                self.broadcast_state("idle")
                return
            text = self.stt.transcribe(wav, extra_vocab=self._vocab_hint()) if wav else ""
        if text:
            asyncio.run_coroutine_threadsafe(
                self.ws_server.broadcast({"v": 1, "type": "transcript", "payload": text}),
                self.loop
            )
            self.process_text(text)
            return
        if superhero:
            self._speak_broadcast(f"I'm here whenever you need me — just say {self.wake_word}.")
            self.broadcast_state("idle")
        else:
            self._speak_broadcast("I didn't hear a reply, so here's what's on your screen.")
            self._read_everything_flow()

    def _resolve_folder(self, target_name: str) -> str | None:
        """Folder resolution for the static lane:
        1. Checks predefined roots (Desktop, Documents, Downloads, Pictures, Music, Videos, User Home).
        2. If not found, asks for confirmation before a full drive search.
        3. Performs a search_file walk if the user confirms.
        Returns None — never a guessed path — if nothing is actually found. Callers
        must treat None as "unresolved", not attempt to open it.
        """
        from pathlib import Path
        target_clean = target_name.strip().lower().replace("my ", "").replace(" folder", "")
        home = Path.home()

        predefined_roots = [
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
            home / "Pictures",
            home / "Music",
            home / "Videos",
            home
        ]
        for root in predefined_roots:
            if root.exists():
                if root.name.lower() == target_clean:
                    return str(root)
                try:
                    for child in root.iterdir():
                        if child.is_dir() and child.name.lower() == target_clean:
                            return str(child)
                except Exception:
                    pass

        should_search_all = self.ask_confirmation(
            f"I couldn't find the {target_name} folder in your main folders. Should I search your whole computer?"
        )
        if should_search_all:
            search_tool = registry.get_tool("search_file")
            res = search_tool.execute({"query": target_clean})
            if res and res.get("matches"):
                return res["matches"][0]["path"]

        return None

    def train_wake_word(self, word: str = None):
        """Voice-guided wake-word personalization: record 5 samples, retrain, hot-swap.
        `word` optionally renames the wake phrase (sanitized, default keeps current)."""
        with self.lock:
            if self.state != "idle":
                return
            # Claim "acting" synchronously, in the SAME lock as the idle check, not just
            # inside the background thread — otherwise a wake-word trigger landing in the
            # gap before the thread starts would see state still "idle" and start a second,
            # overlapping session racing this one.
            self.state = "acting"
        if word:
            import re as _re
            clean = _re.sub(r"[^a-z ]", "", word.strip().lower())[:20]
            if clean:
                self.wake_word = clean
        self._safe_thread(self._train_wake_flow)

    def _play_asset(self, name_or_path: str):
        """Play a pre-baked static WAV — zero AI, zero lag. Accepts either a bare
        filename under models/assets/ or a full path (e.g. from _word_prompt_asset)."""
        import soundfile as sf
        import sounddevice as sd
        if os.path.isabs(name_or_path):
            path = name_or_path
        else:
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            path = os.path.join(root, 'models', 'assets', name_or_path)
        data, fs = sf.read(path, dtype='float32')
        # sd.wait()/blocking playback is a confirmed Windows-specific PortAudio
        # bug (python-sounddevice #283) that cuts audio off slightly before the
        # real end — duration-based waiting on a silence-padded buffer doesn't
        # have this problem (see core/voice/tts.py's _wait_for_playback).
        from core.voice.tts import pad_silence
        data = pad_silence(data, fs)
        sd.stop()
        sd.play(data, fs)
        time.sleep(len(data) / fs)

    _WORD_PROMPT_TEXT = {
        "say": lambda w: f"Say {w} now.",
        "again": lambda w: f"Again, say {w}.",
        "trained": lambda w: f"Training complete. {w} is now your trained wake word.",
    }

    def _word_prompt_asset(self, kind: str) -> str:
        """kind: 'say', 'again', or 'trained'. The default wake word 'pulse' already has a
        pre-baked prompt_{kind}.wav (zero AI). If the user changed the wake word to something
        else, Kokoro is called exactly ONCE per word to synthesize and cache a new asset —
        every training round and every future retrain after that reuses the cached file, so
        Kokoro never runs again for that word."""
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        assets_dir = os.path.join(root, 'models', 'assets')
        word = self.wake_word
        if word == "pulse":
            return os.path.join(assets_dir, f"prompt_{kind}.wav")
        safe = "".join(c for c in word if c.isalnum()) or "word"
        cached = os.path.join(assets_dir, f"prompt_{kind}_{safe}.wav")
        if not os.path.exists(cached):
            import numpy as np
            import scipy.signal
            import scipy.io.wavfile as wf
            text = self._WORD_PROMPT_TEXT[kind](word)
            chunks = [audio for _, _, audio in self.tts.pipeline(text, voice=self.tts.voice, speed=1.0)]
            audio_24k = np.concatenate(chunks).astype(np.float32)
            audio_16k = scipy.signal.resample_poly(audio_24k, up=2, down=3).astype(np.float32)
            pcm = (np.clip(audio_16k, -1, 1) * 32767).astype(np.int16)
            wf.write(cached, 16000, pcm)
        return cached

    def _calibrate_ambient(self) -> float:
        import numpy as np
        import sounddevice as sd
        buf = sd.rec(int(16000 * 1.0), samplerate=16000, channels=1, dtype='int16')
        sd.wait()
        ambient = float(np.sqrt(np.mean(buf.flatten().astype(np.float64) ** 2)))
        return max(ambient * 4.0, 80)

    def _record_until_silence_lite(self, threshold: float):
        """VAD-lite (32ms RMS chunks, no Silero) — fast enough to feel instant.
        Returns None if no speech was detected at all (caller retries)."""
        import numpy as np
        import sounddevice as sd
        SR, CHUNK = 16000, 512
        chunks, has_spoken, silent_since, started = [], False, None, time.time()
        with sd.InputStream(samplerate=SR, channels=1, dtype='int16', blocksize=CHUNK) as stream:
            while True:
                chunk, _ = stream.read(CHUNK)
                flat = chunk.flatten()
                chunks.append(flat)
                energy = float(np.sqrt(np.mean(flat.astype(np.float64) ** 2)))
                if energy >= threshold:
                    has_spoken = True
                    silent_since = None
                elif has_spoken:
                    if silent_since is None:
                        silent_since = time.time()
                    elif (time.time() - silent_since) * 1000 >= 600:
                        break
                if time.time() - started >= 2.5:
                    break
        if not has_spoken:
            return None
        return np.concatenate(chunks)

    def _train_wake_flow(self):
        import glob, subprocess, sys, gc
        import scipy.io.wavfile as wavf
        word = self.wake_word
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        outdir = os.path.join(root, 'models', 'user_samples')
        os.makedirs(outdir, exist_ok=True)

        # Instant UI feedback the moment the button is clicked — before any audio work.
        self.broadcast_state("acting")
        asyncio.run_coroutine_threadsafe(
            self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                      "status": "started", "text": "Preparing microphone…"}),
            self.loop
        )

        # WakeListener keeps its own live mic InputStream running in the background at all
        # times. Recording samples below opens ANOTHER InputStream on the same device while
        # that one is still active — two concurrent PortAudio streams on the same device can
        # hang or crash the whole process on Windows (this is what forced a run.bat restart).
        # Stop it now, before any recording, and guarantee it comes back in `finally` below.
        self.listener.stop()

        # Only wipe samples when the wake word itself changed (old-word audio would poison
        # training). Otherwise ACCUMULATE — each retrain adds 5 more real samples on top of
        # prior ones instead of discarding them, capped so training time stays bounded.
        marker = os.path.join(outdir, '_word.txt')
        prev_word = open(marker, encoding='utf-8').read().strip() if os.path.exists(marker) else None
        if prev_word != word:
            for f in glob.glob(os.path.join(outdir, '*.wav')):
                os.remove(f)
        MAX_SAMPLES = 25
        existing = len(glob.glob(os.path.join(outdir, '*.wav')))

        threshold = self._calibrate_ambient()

        say_asset = self._word_prompt_asset("say")      # Kokoro only runs here, once, if word != "pulse"
        again_asset = self._word_prompt_asset("again")  # cached to disk — every future call is pre-baked

        NUM_SAMPLES, MAX_RETRIES = 5, 3
        collected = 0
        while collected < NUM_SAMPLES:
            asyncio.run_coroutine_threadsafe(
                self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                          "status": "running",
                                          "text": f"Sample {collected + 1} of {NUM_SAMPLES}…"}),
                self.loop
            )
            attempt, saved = 0, False
            while attempt < MAX_RETRIES and not saved:
                self._play_asset(say_asset if attempt == 0 else again_asset)
                # beep_start.wav is a full 180ms tone — audible where the tiny wake-ack
                # earcon (two 80ms beeps) was reported too quiet to notice.
                self._play_asset("beep_start.wav")
                audio = self._record_until_silence_lite(threshold)
                # _record_until_silence_lite already confirms a chunk cleared the energy
                # threshold before returning non-None — re-checking RMS over the WHOLE clip
                # here (including leading/trailing silence padding) dilutes the average and
                # was rejecting genuine speech, forcing 10+ retries for 5 samples.
                if audio is None:
                    attempt += 1
                    self._play_asset("beep_bad.wav")
                    continue
                idx = ((existing + collected) % MAX_SAMPLES) + 1  # cycles out the oldest once at the cap
                wavf.write(os.path.join(outdir, f'sample_{idx}.wav'), 16000, audio)
                self._play_asset("beep_ok.wav")
                saved = True
            if not saved:
                # Never hang on a bad mic/silence — skip and keep going with what we have.
                asyncio.run_coroutine_threadsafe(
                    self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                              "status": "running",
                                              "text": f"Sample {collected + 1} skipped — too quiet"}),
                    self.loop
                )
            collected += 1

        with open(marker, 'w', encoding='utf-8') as f:
            f.write(word)
        # NOT prompt_done.wav — that asset says "Training complete", which is false here:
        # the 5 samples are collected but the ~10 min model training below hasn't run yet.
        self._play_asset("ack.wav")
        # Deliberately staying "acting" (not idle) through the whole training subprocess —
        # this blocks wake-word triggers and typed commands for the duration instead of
        # letting them run concurrently. Training is CPU-heavy (feature extraction + 120+
        # training epochs) and competing with LLM planning/TTS for the same CPU, or a
        # wake-word trigger starting a session mid-training, was unnecessary risk for a
        # feature that only needs to run occasionally. Simpler and more reliable to just
        # make training block until it's done.
        asyncio.run_coroutine_threadsafe(
            self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                      "status": "running", "text": "Samples collected. Training model…"}),
            self.loop
        )

        for f in ('pulse_v2.onnx', 'pulse_v2.onnx.data'):
            p = os.path.join(root, 'models', f)
            if os.path.exists(p):
                os.remove(p)
        env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1', PULSE_WAKE_WORD=word)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(root, 'scripts', 'train_pulse_v2.py')],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, cwd=root, bufsize=1
        )
        stdout_lines = []
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            stdout_lines.append(line)
            print(line)  # still visible in the console
            # forward each progress line to the UI
            asyncio.run_coroutine_threadsafe(
                self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                          "status": "running", "text": line}),
                self.loop
            )
        exit_code = proc.wait()
        full_output = "\n".join(stdout_lines)
        # BUG FIXED: this used to check ONLY whether "Exported" appeared anywhere in the
        # output — but the auto-retrain path (pass_rate < 80%) prints "Exported" for the
        # FIRST attempt, then may crash partway through re-exporting the SECOND, improved
        # attempt. That crash left the original, worse model's "Exported" text still in
        # full_output, so this would have reported success and swapped in a model that
        # actually scored 0% on its own validation. The exit code is the authoritative
        # signal — a Python traceback always exits non-zero, a real completion is 0.
        training_ok = exit_code == 0 and ('Exported' in full_output or 'exported' in full_output)

        try:
            if not training_ok:
                # BUG FIXED: this used to fall through into the swap block below
                # regardless, which tried onnx.load() on a pulse_v2.onnx that was never
                # written (training never got that far), hit FileNotFoundError, and then
                # told the user "Restart to activate new model" — a misleading message
                # implying a fix exists when there's nothing to activate at all.
                print("Wake training failed output:", full_output[-500:])
                self._play_asset("beep_bad.wav")
                asyncio.run_coroutine_threadsafe(
                    self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                              "status": "failed", "text": "Training failed — model unchanged"}),
                    self.loop
                )
            else:
                self.listener.owwModel = None
                gc.collect()
                time.sleep(1)
                import onnx, shutil
                mdl = os.path.join(root, 'models')
                m = onnx.load(os.path.join(mdl, 'pulse_v2.onnx'), load_external_data=False)
                for t in m.graph.initializer:
                    for e in t.external_data:
                        if e.key == 'location' and 'pulse_v2' in e.value:
                            e.value = 'pulse.onnx.data'
                os.remove(os.path.join(mdl, 'pulse.onnx'))
                os.remove(os.path.join(mdl, 'pulse.onnx.data'))
                onnx.save(m, os.path.join(mdl, 'pulse.onnx'))
                shutil.move(os.path.join(mdl, 'pulse_v2.onnx.data'), os.path.join(mdl, 'pulse.onnx.data'))
                os.remove(os.path.join(mdl, 'pulse_v2.onnx'))
                with get_db() as conn:
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wake_word', ?)", (word,))
                # Real completion — properly announce it (pre-baked for "pulse", cached
                # per-word Kokoro synthesis otherwise), not just a beep.
                self._play_asset(self._word_prompt_asset("trained"))
                asyncio.run_coroutine_threadsafe(
                    self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                              "status": "done", "text": f"✓ Wake word trained: {word}"}),
                    self.loop
                )
                # Two-stage verification (the same pattern Alexa/Google Voice Match use):
                # train a lightweight per-user verifier on these exact real enrollment
                # recordings vs. synthesized non-wake-word speech. openWakeWord re-scores
                # any candidate the base model flags through this instead of trusting the
                # base model alone — this is what actually tells "this enrolled voice
                # saying the wake word" apart from "anyone/anything that merely sounds
                # similar," which a single shared base model can't do on its own. Best-
                # effort: a failure here must never undo the wake-word training success
                # that already completed and was already announced above.
                try:
                    self._train_verifier(outdir, root)
                except Exception as e:
                    print(f"Verifier training skipped (non-fatal): {e}")
        except Exception as e:
            # Only reachable now for a genuine swap-step failure on a model that DID
            # export successfully (e.g. a corrupt/partial onnx write) — "restart to
            # activate" is an accurate message in this case, unlike before.
            print("Wake model swap failed:", e)
            self._play_asset("beep_bad.wav")
            asyncio.run_coroutine_threadsafe(
                self.ws_server.broadcast({"v": 1, "type": "training_progress",
                                          "status": "done", "text": "Restart to activate new model"}),
                self.loop
            )
        finally:
            # ALWAYS resume wake detection, whether training succeeded, failed, or the
            # swap itself threw — otherwise wake-word listening stays silently off.
            self.listener.start()
        self.broadcast_state("idle")

    def _train_verifier(self, sample_dir, root):
        """Trains openWakeWord's per-user logistic-regression verifier on the real
        enrollment recordings just collected (positives) vs. a handful of synthesized
        non-wake-word phrases (negatives), and saves it as models/pulse_verifier.joblib.
        WakeListener picks it up automatically on its next (re)start if present."""
        import glob, tempfile, shutil
        import scipy.io.wavfile as wavf
        from scipy.signal import resample_poly
        import numpy as np
        import openwakeword

        positive_clips = glob.glob(os.path.join(sample_dir, '*.wav'))
        if len(positive_clips) < 3:
            print("Verifier: not enough enrollment samples yet, skipping.")
            return

        # Ordinary speech, deliberately NOT close sound-alikes of the wake word (those
        # belong in the base model's own negative training set) — this just needs to
        # sound like "a person talking", so the verifier learns the enrolled voice's
        # actual vocal characteristics rather than re-learning wake-word discrimination
        # the base model already handles.
        NEG_PHRASES = ["okay", "hello there", "thank you very much", "good morning",
                       "what time is it", "turn off the lights", "can you help me"]
        tmpdir = tempfile.mkdtemp(prefix="pulse_verifier_neg_")
        try:
            negative_clips = []
            for i, phrase in enumerate(NEG_PHRASES):
                pcm24 = self.tts._synth_sentence(phrase)
                if len(pcm24) == 0:
                    continue
                pcm16 = resample_poly(pcm24, 2, 3)  # Kokoro's 24kHz -> openWakeWord's 16kHz
                pcm16_int = np.clip(pcm16 * 32767, -32768, 32767).astype(np.int16)
                path = os.path.join(tmpdir, f'neg_{i}.wav')
                wavf.write(path, 16000, pcm16_int)
                negative_clips.append(path)

            if len(negative_clips) < 3:
                print("Verifier: negative-clip synthesis failed, skipping.")
                return

            tmp_out = os.path.join(tmpdir, 'candidate_verifier.joblib')
            openwakeword.train_custom_verifier(
                positive_reference_clips=positive_clips,
                negative_reference_clips=negative_clips,
                output_path=tmp_out,
                model_name=os.path.join(root, 'models', 'pulse.onnx'),
                inference_framework="onnx",
            )

            # Safety gate: measured directly — a verifier trained when the base model's
            # own confidence on these clips is inconsistent can score a GENUINE positive
            # near zero (0.00007 in testing), which is worse than having no verifier at
            # all. Never activate one without proving it actually recognizes the voice
            # it was just trained on.
            passed = self._validate_verifier(tmp_out, positive_clips, root)
            required = max(1, len(positive_clips) - 1)  # tolerate one bad clip, not more
            if passed < required:
                print(f"Verifier validation failed ({passed}/{len(positive_clips)} positives recognized, "
                      f"need {required}) — discarding. Wake detection keeps using the base model alone.")
                return

            out_path = os.path.join(root, 'models', 'pulse_verifier.joblib')
            shutil.move(tmp_out, out_path)
            print(f"Verifier trained and validated: {out_path} "
                  f"({passed}/{len(positive_clips)} positives, {len(negative_clips)} negatives)")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _validate_verifier(self, verifier_path, positive_clips, root) -> int:
        """Re-scores the same real enrollment clips through base-model+verifier and
        counts how many are still recognized (score > 0.5). Isolated Model instance —
        never touches self.listener.owwModel."""
        import openwakeword
        import soundfile as sf
        model = openwakeword.Model(
            wakeword_models=[os.path.join(root, 'models', 'pulse.onnx')],
            inference_framework="onnx",
            custom_verifier_models={"pulse": verifier_path},
            custom_verifier_threshold=0.1,
        )
        passed = 0
        for clip in positive_clips:
            model.reset()
            audio, sr = sf.read(clip, dtype='int16')
            best = 0.0
            for start in range(0, max(0, len(audio) - 1280), 1280):
                pred = model.predict(audio[start:start + 1280])
                for k, v in pred.items():
                    if "pulse" in k:
                        best = max(best, v)
            if best > 0.5:
                passed += 1
        return passed

    def _refresh_screen_cache(self, generation):
        """Runs the actual UIA walk (the expensive part) off the polling loop's own
        thread, so a slow window never delays the next foreground-change check.
        Guarded by `generation`: a real, confirmed race — switching Desktop -> another
        app -> back to Desktop quickly could spawn a slow walk for the middle app that
        finishes AFTER the newer Desktop walk and clobbers the cache with the wrong
        window's content. Only the walk started by the MOST RECENT foreground change
        is allowed to actually write the cache; older ones are discarded even if they
        finish later."""
        try:
            # _update_index=False: this runs continuously on its own 0.5s
            # polling schedule, independent of any active task — confirmed
            # live it can fire mid-task and silently corrupt the element
            # indices an in-progress fill_element/click_element is about to
            # use (see the matching note in ReadScreenTool.execute).
            screen = registry.get_tool("read_screen").execute({"_update_index": False})
            if screen.get("success"):
                with self._screen_cache_lock:
                    if generation == self._screen_cache_generation:
                        self._screen_cache = screen
        except Exception:
            pass

    def _screen_context_loop(self):
        """Polls the foreground window (a near-free Win32 call, not a UIA walk) and only
        re-reads the screen when it actually changes — so by the time a command arrives,
        context is normally already cached instead of being read synchronously after the
        fact. Same shape as _narration_loop's polling, just unconditional and UIA-backed."""
        import win32gui
        last_hwnd = None
        while True:
            time.sleep(0.5)
            try:
                hwnd = win32gui.GetForegroundWindow()
            except Exception:
                continue
            if hwnd and hwnd != last_hwnd:
                last_hwnd = hwnd
                with self._screen_cache_lock:
                    self._screen_cache_generation += 1
                    gen = self._screen_cache_generation
                threading.Thread(target=self._refresh_screen_cache, args=(gen,), daemon=True).start()

    def _narration_loop(self):
        import win32gui
        last = None
        while True:
            time.sleep(1.0)
            if not self.narrate:
                last = None
                continue
            try:
                title = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            except Exception:
                continue
            if title and title != last:
                if last is not None and self.state == "idle" and not self.tts.is_playing:
                    spoken = title.split(" - ")[-1] if " - " in title else title
                    asyncio.run_coroutine_threadsafe(
                        self.ws_server.broadcast({"v": 1, "type": "feedback", "text": f"Now in {spoken}", "mode": self.feedback_mode}),
                        self.loop
                    )
                    self.tts.speak(f"Now in {spoken}.")
                last = title

    def list_input_devices(self):
        import sounddevice as sd
        devs = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["hostapi"] == 0:
                devs.append({"id": i, "name": d["name"]})
        return devs

    def set_input_device(self, device_id: int):
        import sounddevice as sd
        sd.default.device = (device_id, sd.default.device[1])
        self.listener.stop()
        self.listener.start()

    def run_onboarding(self):
        """A5: voice-guided first run — mic check + tutorial, zero vision required."""
        import numpy as np
        import sounddevice as sd
        from core.db import get_db
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key='onboarded'").fetchone()
        if row:
            return
        self.broadcast_state("speaking")
        self.tts.speak("Welcome to Pulse, your voice assistant. Everything runs on your computer and stays private. Let me check your microphone. Please say anything after the beep.")
        self.capture.play_earcon()
        audio = sd.rec(int(2.5 * 16000), samplerate=16000, channels=1, dtype='int16')
        sd.wait()
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        if rms < 60:
            self.tts.speak("I couldn't hear you. Your microphone may be muted or too far away. You can still type commands, and we can retry any time.")
        else:
            w = self.wake_word
            self.tts.speak(f"Your microphone works. To talk to me, say {w}, wait for the short beep, then speak. Try: {w}, open notepad. To make me recognize you better, say: {w}, train my voice. To hear what's on your screen, say: {w}, what's on my screen. I'm ready.")
        with conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('onboarded', '1')")
        self.broadcast_state("idle")

    def play_superhero_chime(self):
        try:
            import soundfile as sf
            import sounddevice as sd
            path = os.path.join(os.path.dirname(__file__), '..', '..', 'models', 'superhero_on.wav')
            data, fs = sf.read(os.path.abspath(path))
            # See _play_asset — sd.wait() cuts off early on Windows (confirmed
            # PortAudio bug, python-sounddevice #283); duration-based wait on
            # padded audio doesn't have this problem.
            from core.voice.tts import pad_silence
            data = pad_silence(data, fs)
            sd.play(data, fs)
            time.sleep(len(data) / fs)
        except Exception as e:
            print(f"Superhero chime failed: {e}")

    # Tools that read/act on a specific window's UI elements — these are the
    # ones that need the "remembered target" window pulled to front first.
    _FOCUS_SENSITIVE_TOOLS = frozenset({"read_screen", "fill_element", "click_element", "send_keys", "save_file"})

    def _execute_with_heartbeat(self, tool_name, params, user_confirmed=False):
        """Continuous-feedback rule: no silence >4s while busy. Speaks a short filler
        if a tool takes longer than that (page loads, slow lookups).

        Also the sole enforcement point for ensure_target_focused(): this method
        is exclusively AI-driven task execution (every _act_observe tool call and
        the static open/close routes go through it), unlike the passive
        background screen-context cache which calls read_screen directly and
        must NEVER force a window to front. Confirmed live this was a real risk
        — bringing the app forward belongs here, "on top only when actually in
        use", not inside the tools themselves where a passive caller could
        trigger it too."""
        if tool_name in self._FOCUS_SENSITIVE_TOOLS:
            ensure_target_focused()
        def filler():
            if not self.tts.is_playing:
                self.tts.speak_now("Still working on it.")
        timer = threading.Timer(4.0, filler)
        timer.start()
        try:
            return self.executor.execute(tool_name, params, user_confirmed=user_confirmed)
        finally:
            timer.cancel()

    def _speak_broadcast(self, text):
        self.last_spoken = text
        self.broadcast_state("speaking")
        asyncio.run_coroutine_threadsafe(
            self.ws_server.broadcast({"v": 1, "type": "feedback", "text": text, "mode": self.feedback_mode}),
            self.loop
        )
        self.tts.speak(text)

    def read_everything(self):
        """A4/A5 continuous screen reading: context -> (ask if Guided) -> tabs -> content.
        Continuous by default; say the wake word any time to interrupt (existing barge-in)."""
        with self.lock:
            if self.state != "idle":
                return
        self._safe_thread(self._read_everything_flow)

    def _read_everything_flow(self):
        desc_tool = registry.get_tool("describe_screen")
        read_tool = registry.get_tool("read_screen")
        self.broadcast_state("acting")
        desc = desc_tool.execute({})
        self._speak_broadcast(f"You're in {desc.get('focused_window', 'your screen')}.")

        if self.feedback_mode == "Guided":
            if not self.ask_confirmation("Should I continue and read the tabs and content?"):
                self.broadcast_state("idle")
                return

        self.broadcast_state("acting")
        detail = read_tool.execute({})
        controls = detail.get("controls", [])
        tabs = [c.split(": ", 1)[1] for c in controls if c.startswith("TabItem:")]
        texts = detail.get("visible_text", [])
        parts = []
        if tabs:
            parts.append(f"There are {len(tabs)} tabs: " + ", ".join(tabs[:8]) + ".")
        if texts:
            parts.append("Content: " + " ".join(texts[:5])[:500])
        self._speak_broadcast(" ".join(parts) if parts else "I didn't find any readable tabs or text content here.")
        self.broadcast_state("idle")

    def start(self):
        try:
            self.listener.start()
        except Exception as e:
            print(f"Microphone unavailable: {e}")
            self.tts.speak("I couldn't access a microphone, so voice is off. You can still type commands in the Pulse window.")
        try:
            self.typing_echo.start()  # hooked but no-op until .enabled is set True
        except Exception as e:
            print(f"Typing echo hook unavailable (may need admin rights): {e}")
        threading.Thread(target=self._narration_loop, daemon=True).start()
        threading.Thread(target=self._screen_context_loop, daemon=True).start()
        threading.Thread(target=self.run_onboarding, daemon=True).start()
        print("Voice controller started. Say 'pulse' to trigger.")

    def stop(self):
        self.listener.stop()
        self.tts.cancel()
