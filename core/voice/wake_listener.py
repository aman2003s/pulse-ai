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

            # Decide off the PEAK score over a short sliding window (~240ms), not a single
            # 80ms frame in isolation. A genuine wake word's confidence ramps up over a
            # couple of frames — a single frame can dip just under threshold by chance and
            # cause a missed detection even though the word was said correctly. This is the
            # same smoothing production keyword-spotters (Porcupine, Alexa) use instead of
            # frame-by-frame decisions.
            window = getattr(self, '_score_window', None)
            if window is None:
                from collections import deque
                window = self._score_window = deque(maxlen=3)
            window.append(raw_score)
            score = max(window)

            speaking = self.is_speaking_fn()
            threshold = 0.93 if speaking else 0.3

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
                print(f"[wake score] raw={raw_score:.3f} smoothed={score:.3f} "
                      f"threshold={threshold:.2f} speaking={speaking} rms={rms:.1f}")

            if score > threshold:
                # While Pulse is talking, require the score to stay high for 2 consecutive
                # 80ms frames (not just one spike) before treating it as a real interrupt —
                # cuts down on ambient-noise/echo false-triggers without adding perceptible
                # delay to a genuine "Pulse" said over playback.
                self._high_streak = getattr(self, '_high_streak', 0) + 1
                sustained = (not speaking) or self._high_streak >= 2
                if sustained:
                    now = time.time()
                    if now - getattr(self, '_last_trigger', 0) > 2.0:  # debounce
                        self._last_trigger = now
                        self._high_streak = 0
                        self.callback()
            else:
                self._high_streak = 0
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
            self.owwModel = Model(wakeword_models=[self.model_path], inference_framework="onnx")

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
