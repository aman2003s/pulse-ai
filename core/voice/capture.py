import numpy as np
import sounddevice as sd
import torch
import time
import io
import wave
import os
import threading
import warnings

from core.paths import models_dir

# Suppress warnings from torch hub
warnings.filterwarnings("ignore", category=UserWarning)

class CapturePipeline:
    def __init__(self, sample_rate=16000, is_speaking_fn=None):
        self.sample_rate = sample_rate
        print("Loading Silero VAD...")
        self.model, self.utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False
        )
        # Prefer pre-baked asset (generate_assets.py), fall back to legacy Kokoro-generated ack.wav
        assets_ack = os.path.join(models_dir(), 'assets', 'ack.wav')
        legacy_ack = os.path.join(models_dir(), 'ack.wav')
        self.earcon_path = assets_ack if os.path.exists(assets_ack) else legacy_ack
        self._abort = threading.Event()
        # AEC-lite: same pattern already proven for WakeListener's own self-echo
        # guard (is_speaking_fn combining "still playing" + a short settle window
        # after) — reused here because it's the SAME underlying problem, just for
        # the "listen for a reply" capture instead of wake-word detection. Local
        # voice-assistant projects (ESPHome, Rhasspy/Home Assistant satellites)
        # converge on this exact pattern for exactly this reason: no real signal-
        # level echo cancellation, just mute-during-and-briefly-after playback,
        # since there's no separate reference/loopback signal available to do
        # real AEC against. Confirmed live (2026-07-31) this gap is real: an
        # exact prior TTS line of Pulse's own showed up transcribed as a "user
        # answer" to an unrelated pending question.
        self.is_speaking_fn = is_speaking_fn or (lambda: False)

    def cancel_capture(self):
        """Aborts an in-progress capture_until_silence() call immediately. Fixes the bug
        where 'cancel' stopped TTS but a blocking mic recording kept running regardless,
        letting it race a subsequent command."""
        self._abort.set()
        sd.stop()

    def play_earcon(self):
        # Never start the "now listening" chime — and by extension never start
        # capturing — while Pulse is still speaking or within the brief settle
        # window right after (acoustic energy can still be resonating in the
        # room even once the digital signal stops). Bounded so a stuck flag
        # can't hang this forever.
        settle_deadline = time.time() + 5.0
        while self.is_speaking_fn() and time.time() < settle_deadline:
            time.sleep(0.05)
        # Rapidly switching output sample rate (Kokoro plays at 24kHz, this at 16kHz)
        # can glitch some Windows audio drivers, especially back-to-back in the wake-word
        # training loop. sd.stop() + one retry makes it reliable.
        # Cached after first read — re-reading a WAV file from disk on every single
        # wake-word trigger was pure avoidable latency between "wake detected" and
        # "chime actually starts playing".
        if getattr(self, '_earcon_cache', None) is None:
            import soundfile as sf
            from core.voice.tts import pad_silence
            data, fs = sf.read(self.earcon_path)
            self._earcon_cache = (pad_silence(data, fs), fs)
        data, fs = self._earcon_cache
        for attempt in range(2):
            try:
                sd.stop()
                sd.play(data, fs)
                # sd.wait() cuts playback off early on Windows (confirmed
                # PortAudio bug, python-sounddevice #283) — duration-based wait
                # on the padded buffer above doesn't have this problem.
                time.sleep(len(data) / fs)
                return
            except Exception as e:
                print(f"Earcon failed to play (attempt {attempt + 1}): {e}")
                time.sleep(0.15)

    def capture_until_silence(self, silence_threshold_ms=900, max_duration_s=25.0, no_speech_timeout_s=6.0):
        """Thin, bounded wrapper around _capture_loop. Confirmed live: the loop's
        own timeout checks only run BETWEEN iterations, so if the blocking
        stream.read() call inside it ever stalls (observed: one call sat for
        ~9 minutes, another never returned at all — likely a device-release
        race with the wake-listener's stream on the same input device, though
        the exact driver behavior wasn't fully pinned down), max_duration_s is
        never actually enforced — the caller can hang indefinitely regardless
        of what's configured. Same fix as the tool-executor's per-tool timeout
        earlier this session: don't trust the inner loop to bound itself: run
        it on its own thread and join with a hard ceiling from OUTSIDE, so
        this method is guaranteed to return in bounded time no matter what the
        blocking read underneath is doing."""
        self._abort.clear()
        self.play_earcon()
        if self._abort.is_set():  # cancelled during the earcon itself
            return None

        result = {"audio": None}

        def run():
            result["audio"] = self._capture_loop(silence_threshold_ms, max_duration_s, no_speech_timeout_s)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        hard_ceiling = max_duration_s + 5.0
        thread.join(timeout=hard_ceiling)
        if thread.is_alive():
            print(f"Capture exceeded hard ceiling ({hard_ceiling:.0f}s) — aborting.")
            # Reuses cancel_capture's own mechanism (abort flag + sd.stop()) —
            # sd.stop() can force the stuck stream.read() to unblock/raise,
            # letting the orphaned thread actually exit soon after, rather than
            # only setting a flag it might never get to check.
            self.cancel_capture()
            return None
        return result["audio"]

    def _capture_loop(self, silence_threshold_ms, max_duration_s, no_speech_timeout_s):
        print("Listening for user speech...")
        audio_buffer = []
        silence_start_time = None
        has_spoken = False
        start_time = time.time()

        # 512 samples at 16kHz = 32ms
        chunk_size = 512

        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='float32') as stream:
            while True:
                if self._abort.is_set():
                    print("Capture aborted (cancel).")
                    return None
                if time.time() - start_time > max_duration_s:
                    print("Max recording duration reached.")
                    break

                chunk, overflow = stream.read(chunk_size)
                audio_buffer.append(chunk.copy())

                # Check VAD
                audio_tensor = torch.from_numpy(chunk.flatten())
                speech_prob = self.model(audio_tensor, self.sample_rate).item()

                if speech_prob > 0.5:
                    has_spoken = True
                    silence_start_time = None
                else:
                    if has_spoken:
                        if silence_start_time is None:
                            silence_start_time = time.time()
                        elif (time.time() - silence_start_time) * 1000 > silence_threshold_ms:
                            print("Silence detected, ending capture.")
                            break
                    else:
                        if time.time() - start_time > no_speech_timeout_s:
                            print(f"No speech detected ({no_speech_timeout_s}s timeout).")
                            break

        if not audio_buffer:
            return None

        full_audio = np.concatenate(audio_buffer, axis=0)
        full_audio_int16 = (full_audio * 32767).astype(np.int16)

        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(full_audio_int16.tobytes())

        return wav_io.getvalue()
