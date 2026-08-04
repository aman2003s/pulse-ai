import time
import threading
import sounddevice as sd
import numpy as np
import openwakeword
from openwakeword.model import Model
import os
import traceback

from core.paths import models_dir

class WakeListener:
    def __init__(self, callback, model_name="pulse", is_speaking_fn=None):
        self.model_name = model_name
        self.callback = callback
        self.is_running = False
        self.stream = None
        self.owwModel = None
        self._last_callback_time = None
        self.model_path = os.path.join(models_dir(), f'{model_name}.onnx')
        # AEC-lite: we have no real echo cancellation, so Pulse's own TTS can bleed into
        # the mic and false-trigger the wake word. Require much higher confidence while
        # speaking — a genuine "Pulse" said over playback still scores near 1.0, but
        # bleed-through of Kokoro's own voice saying other words does not.
        self.is_speaking_fn = is_speaking_fn or (lambda: False)
        
    def _audio_callback(self, indata, frames, time_info, status):
        self._last_callback_time = time.time()
        if not self.is_running:
            return
        try:
            # openwakeword expects 16kHz, 1-channel, int16
            audio = indata.flatten()
            prediction = self.owwModel.predict(audio)

            # Predict returns a dict: {"hey_jarvis_v0.1": score}
            # Match the prefix or exact name
            raw_score = 0.0
            for key, val in prediction.items():
                if self.model_name in key:
                    raw_score = val
                    break

            # Sustain check: require 3 TRULY CONSECUTIVE raw 80ms frames (~240ms) above
            # threshold, not just "2 hits somewhere in the last 4 frames" (the previous
            # rule, which didn't actually require adjacency). Tightened 2026-08-01 after a
            # real user-reported false-accept ("waking up again and again" from video
            # audio containing no wake word) was measured, not guessed: replaying ordinary
            # non-wake-word speech through this exact model found a genuine isolated
            # single-frame spike (raw=0.905) that the OLD "any 2 of last 4" rule would NOT
            # have caught either (it never got a second hit in that window) — but a
            # position-agnostic rule leaves real exposure for two separate near-threshold
            # moments landing in the same short window by chance on louder/longer real-world
            # audio (a full video has far more raw exposure than any offline test can
            # cover). Measured real "Pulse" utterances for comparison: a clean 4-frame
            # consecutive run (scores ~1.00/0.93/0.99/1.00) — comfortably clears a
            # true-consecutive requirement of 3, so this can only ever reduce false accepts,
            # never cost a real detection under measured conditions. Same rule, same
            # threshold values, in both idle and speaking states.
            speaking = self.is_speaking_fn()
            # Idle threshold raised 0.3->0.5 (2026-07-31): openWakeWord's own docs
            # state 0.5 is the library's recommended default for a single-frame
            # prediction — Pulse's own sustain check (stricter than the library's
            # default single-frame check) was still stacked on top of a threshold
            # notably BELOW that vetted baseline. Not the ~0.9+ "production-tuned"
            # figure from a real noise soak (that still needs 1.7's dedicated
            # measurement) — this is the library's own tested default, a safe,
            # evidence-backed step up rather than a guess.
            threshold = 0.93 if speaking else 0.5
            consecutive = getattr(self, '_consecutive_high', 0)
            consecutive = consecutive + 1 if raw_score > threshold else 0
            self._consecutive_high = consecutive

            # TEMPORARY diagnostic — same "watch it live" approach that found the training
            # bugs. Reusing this SAME callback/stream (not a second parallel InputStream,
            # which would risk the exact concurrent-stream conflict fixed earlier) to show
            # both raw mic energy (proves audio is actually reaching Pulse at all) and the
            # model's score, so we can tell "no audio" apart from "model isn't detecting it".
            rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
            now_hb = time.time()
            if now_hb - getattr(self, '_last_heartbeat', 0) > 1.0:
                self._last_heartbeat = now_hb
                print(f"[mic heartbeat] rms={rms:.1f}")
            if raw_score > 0.05 or rms > 300:
                print(f"[wake score] raw={raw_score:.3f} consecutive={consecutive}/3 "
                      f"threshold={threshold:.2f} speaking={speaking} rms={rms:.1f}")

            if consecutive >= 3:
                now = time.time()
                if now - getattr(self, '_last_trigger', 0) > 2.0:  # debounce
                    self._last_trigger = now
                    self._consecutive_high = 0
                    self.callback()
        except Exception as e:
            # Never crash the audio thread on a bad frame, but don't go fully silent
            # either — a callback that's erroring every frame would otherwise look
            # identical to "detection just isn't triggering," with zero evidence either way.
            now = time.time()
            if now - getattr(self, '_last_err_print', 0) > 5.0:
                self._last_err_print = now
                print(f"WakeListener callback error: {e}")

    def start(self):
        if self.is_running:
            return
        
        if not self.owwModel:
            print(f"Loading openWakeWord model '{self.model_name}'...")
            # Per-user verifier (trained in controller._train_verifier from real
            # enrollment recordings): openWakeWord re-scores any candidate the base
            # model flags through this instead of trusting the base model alone. Optional
            # — falls back to base-model-only scoring until the user has trained once.
            verifier_path = os.path.join(os.path.dirname(self.model_path), f'{self.model_name}_verifier.joblib')
            custom_verifiers = {self.model_name: verifier_path} if os.path.exists(verifier_path) else {}
            if custom_verifiers:
                print(f"Loading per-user verifier: {verifier_path}")
            # vad_threshold gates predictions on openWakeWord's own bundled Silero VAD
            # (ONNX, already local — no extra download): it looks at the ~0.4-0.56s of
            # VAD history before the current frame and zeroes the wake-word score outright
            # if that window wasn't classified as speech. This is what actually rejects
            # non-speech noise (air, fans, typing) — the raw/sustain-window logic in
            # _audio_callback only ever sees genuine speech-gated scores once this is on.
            self.owwModel = Model(wakeword_models=[self.model_path], inference_framework="onnx",
                                   vad_threshold=0.5, custom_verifier_models=custom_verifiers,
                                   custom_verifier_threshold=0.1)

        self.is_running = True
        self.stream = sd.InputStream(
            samplerate=16000,
            blocksize=1280, # 80ms chunks
            channels=1,
            dtype='int16',
            callback=self._audio_callback
        )
        self.stream.start()
        print(f"WakeListener started. Listening for '{self.model_name}' (as a stand-in for 'pulse')...")

    def stop(self):
        self.is_running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        print("WakeListener stopped.")

if __name__ == "__main__":
    def on_wake():
        print("Wake word detected!")
        time.sleep(1) # debounce

    listener = WakeListener(on_wake)
    listener.start()
    try:
        print("Press Ctrl+C to exit.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.stop()
