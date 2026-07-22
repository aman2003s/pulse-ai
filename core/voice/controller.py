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
import core.tools.system_tools  # noqa: F401 — registers tools
from core.task_manager import TaskManager
from core.conversation import ConversationManager
from core.db import get_db
from core.voice.typing_echo import TypingEcho
import asyncio

class VoiceController:
    def __init__(self, ws_server, planner_port=8081):
        self.tts = TTSService()
        self.capture = CapturePipeline()
        self.stt = STTService()
        self.planner = PlannerClient(port=planner_port)
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
            if self.state == "speaking":
                print("Interrupting TTS (Barge-in)...")
                self.tts.cancel()

        self.broadcast_state("listening")
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
        # Capture from mic until silence
        wav_bytes = self.capture.capture_until_silence()
        
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
            wav = self.capture.capture_until_silence()
            answer = (self.stt.transcribe(wav) or "").lower() if wav else ""
            if re.search(r"\b(yes|yeah|yep|sure|do it|go ahead|confirm|ok|okay)\b", answer):
                return True
            if re.search(r"\b(no|nope|stop|cancel|don't|abort)\b", answer):
                return False
            question = "Sorry, I didn't catch that. Please say yes or no."
        return False

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
            if not screen:
                screen = registry.get_tool("read_screen").execute({})
            if screen.get("success"):
                items = screen.get("controls", [])[:20]
                system_prompt += (
                    f"\n\nCURRENT SCREEN (focused window: {screen.get('window')}):\n" + "\n".join(items) +
                    "\nIf the user's request refers to something visible above (a folder, file, button, link), "
                    "act on THAT — click_element with its [N] number — rather than searching blindly."
                )
        except Exception:
            pass

        step_results = self._run_task_loop(task_id, text, system_prompt)
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

    def _run_task_loop(self, task_id, goal, system_prompt, max_iterations=6):
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
            self._speak_broadcast(f"I'll do this in {n} steps: " + ". ".join(f"{i+1}, {s}" for i, s in enumerate(task_list)) + ".")
            all_results = []
            for i, step_goal in enumerate(task_list):
                with conn:
                    conn.execute("UPDATE tasks SET current_step = ? WHERE id = ?", (i, task_id))
                self._speak_broadcast(f"Step {i + 1} of {n}: {step_goal}.")
                sub_goal = (f"OVERALL GOAL: {goal}\nCURRENT STEP ({i + 1} of {n}): {step_goal}\n"
                            f"COMPLETED SO FAR: {task_list[:i]}\nDo only this step now.")
                results = self._act_observe(task_id, sub_goal, system_prompt, max_rounds=4)
                all_results.extend(results)
                if any(isinstance(r, dict) and r.get("cancelled") for r in results):
                    return all_results
                if results and isinstance(results[-1], dict) and "error" in results[-1]:
                    if not self.ask_confirmation(
                            f"Step {i + 1} hit a problem: {str(results[-1]['error'])[:120]}. Should I continue with the remaining steps?"):
                        self._speak_broadcast("Okay, stopping here.")
                        return all_results
            self._speak_broadcast("All steps finished.")
            return all_results

        # Single-part goal: reuse the response we already have as round one.
        return self._act_observe(task_id, goal, system_prompt,
                                  initial_response=response, max_rounds=max_iterations)

    def _act_observe(self, task_id, goal, system_prompt, initial_response=None, max_rounds=6):
        """Reason -> act -> observe -> re-plan loop. Runs whatever the planner proposes,
        feeds it the REAL observed results, asks again until it declares done — because
        a single upfront plan can't know things it hasn't seen yet (element numbers,
        found file paths, dialog contents)."""
        all_results = []
        user_text = goal
        for iteration in range(max_rounds):
            if initial_response is not None and iteration == 0:
                response = initial_response
            else:
                response = self.planner.prompt(user_text=user_text, system_prompt=system_prompt,
                                                 schema=registry.get_planner_schema())
            if not response:
                self._speak_broadcast("I'm sorry, I ran into a problem thinking that through.")
                break

            speak_text = response.get("speak", "")
            if speak_text and speak_text != self.last_spoken:
                self._speak_broadcast(speak_text)

            steps = response.get("plan", [])
            if not steps:
                # Ended with no further action. If the last thing said was a question,
                # that already invites a reply — don't stack another prompt on it.
                self._pending_question = speak_text.rstrip().endswith("?")
                break

            for step in steps:
                tool_name, params = step.get("tool"), step.get("params", {})
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

            import json as _json
            user_text = (
                f"GOAL: {goal}\nACTIONS YOU (PULSE) JUST PERFORMED AND THEIR RESULTS: {_json.dumps(all_results)[:1500]}\n"
                "These are things YOU just did, not things the user or anyone else did — narrate them as "
                "'I did X' / 'I've typed X', never as 'I see X is already there'. Continue only if more steps "
                "are genuinely needed — use the REAL results above (actual file paths, actual numbered "
                "elements from a read_screen if one just ran; if you need to interact with something and "
                "haven't read the screen since it last changed, read it again first). If a step failed, "
                "decide whether to retry differently or tell the user what went wrong instead of pretending "
                "it worked. If the goal is fully complete, return an empty plan and a brief closing 'speak' "
                "confirming what you did — do not repeat something you already said this task."
            )
        else:
            self._speak_broadcast("This is taking more steps than I expected — let me know if you'd like me to keep going.")
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
        wav = self.capture.capture_until_silence(no_speech_timeout_s=timeout)
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
            wav = self.capture.capture_until_silence(no_speech_timeout_s=timeout)
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
        sd.stop()
        sd.play(data, fs)
        sd.wait()

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

    def _refresh_screen_cache(self):
        """Runs the actual UIA walk (the expensive part) off the polling loop's own
        thread, so a slow window never delays the next foreground-change check."""
        try:
            screen = registry.get_tool("read_screen").execute({})
            if screen.get("success"):
                with self._screen_cache_lock:
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
                threading.Thread(target=self._refresh_screen_cache, daemon=True).start()

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
            sd.play(data, fs)
            sd.wait()
        except Exception as e:
            print(f"Superhero chime failed: {e}")

    def _execute_with_heartbeat(self, tool_name, params, user_confirmed=False):
        """Continuous-feedback rule: no silence >4s while busy. Speaks a short filler
        if a tool takes longer than that (page loads, slow lookups)."""
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
