import time
import threading
import sounddevice as sd
import numpy as np
import openwakeword
from openwakeword.model import Model
import os
import traceback

class WakeListener:
    def __init__(self, callback, model_name="pulse", is_speaking_fn=None):
        self.model_name = model_name
        self.callback = callback
        self.is_running = False
        self.stream = None
        self.owwModel = None
        self.model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'models', f'{model_name}.onnx'))
        # AEC-lite: we have no real echo cancellation, so Pulse's own TTS can bleed into
        # the mic and false-trigger the wake word. Require much higher confidence while
        # speaking — a genuine "Pulse" said over playback still scores near 1.0, but
        # bleed-through of Kokoro's own voice saying other words does not.
        self.is_speaking_fn = is_speaking_fn or (lambda: False)
        
    def _audio_callback(self, indata, frames, time_info, status):
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

            # Sustain check: require at least 2 of the last 4 raw 80ms frames (~320ms) to
            # clear threshold, in BOTH idle and speaking states — not any single frame in
            # isolation. A genuine "Pulse" naturally spans several consecutive high-confidence
            # frames, so this still tolerates one momentary mid-word dip. This replaces an
            # older max()-over-window ("smoothed score") approach that had two real bugs:
            # (1) idle state had no sustain check at all — one noisy ambient frame crossing
            # 0.3 triggered instantly; (2) the speaking-mode anti-echo guard counted the
            # max-of-window score rather than raw per-frame scores, so a single spike stayed
            # "high" for 2 extra frames after the fact and could satisfy the "2 consecutive
            # frames" requirement on its own — defeating the guard it was meant to enforce.
            # Counting raw threshold-crossings within a short rolling window instead requires
            # genuinely independent high-confidence frames in both states.
            window = getattr(self, '_score_window', None)
            if window is None:
                from collections import deque
                window = self._score_window = deque(maxlen=4)
            window.append(raw_score)

            speaking = self.is_speaking_fn()
            threshold = 0.93 if speaking else 0.3
            high_count = sum(1 for s in window if s > threshold)

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
                print(f"[wake score] raw={raw_score:.3f} high_count={high_count}/{len(window)} "
                      f"threshold={threshold:.2f} speaking={speaking} rms={rms:.1f}")

            if high_count >= 2:
                now = time.time()
                if now - getattr(self, '_last_trigger', 0) > 2.0:  # debounce
                    self._last_trigger = now
                    window.clear()
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
