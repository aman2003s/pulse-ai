from kokoro import KPipeline
import sounddevice as sd
import soundfile as sf
import numpy as np
import threading
import time
import re
import os
import warnings
from concurrent.futures import ThreadPoolExecutor

from core.paths import models_dir

warnings.filterwarnings("ignore")

def pad_silence(pcm: np.ndarray, samplerate: int, pad_s: float = 0.25) -> np.ndarray:
    """Appends real trailing silence so any playback-end timing imprecision
    (confirmed: Windows-specific PortAudio early-cutoff, device cold-start
    latency, Python scheduling jitter) can only ever eat into silence, never
    real speech — 0.25s is well past typical Windows audio latency (order
    100-200ms) while staying short enough not to feel like a pause between
    sentences."""
    if pcm is None or len(pcm) == 0:
        return pcm
    pad = np.zeros(int(samplerate * pad_s), dtype=pcm.dtype)
    return np.concatenate([pcm, pad])

def trim_silence(pcm: np.ndarray, threshold: float = 0.01) -> np.ndarray:
    """Trim leading and trailing digital zero-padding/silence from generated PCM chunk,
    eliminating artificial punctuation pauses."""
    if pcm is None or len(pcm) == 0:
        return pcm
    abs_pcm = np.abs(pcm)
    non_silent = np.where(abs_pcm > threshold)[0]
    if len(non_silent) == 0:
        return pcm
    # Keep 10ms (240 samples at 24kHz) soft margin
    start = max(0, non_silent[0] - 240)
    end = min(len(pcm), non_silent[-1] + 240)
    return pcm[start:end]

class TTSService:
    def __init__(self):
        print("Loading Kokoro TTS Service...")
        # 'a' for American English
        self.pipeline = KPipeline(lang_code='a')
        # Confirmed live (2026-07-30, Windows Event Viewer): python.exe crashed with
        # an access violation (0xc0000005) inside torch_python.dll right after a burst
        # of multi-sentence replies. Root cause: speak()'s ThreadPoolExecutor lets up
        # to 3 sentences call into this SAME KPipeline instance concurrently — PyTorch
        # releases the GIL during real inference, so genuinely concurrent calls into
        # one stateful pipeline object from multiple threads can race on its internal
        # buffers. This lock serializes every call into the pipeline (also used by
        # speak_hybrid's background synth thread) — the first sentence is unaffected
        # (nothing else holds the lock yet), later ones just queue instead of running
        # in parallel, which also cuts peak CPU/GPU load from 3x concurrent inference
        # down to 1x.
        self._pipeline_lock = threading.Lock()
        self.voice = 'af_heart'
        self.speed = 1.0
        self.cancel_event = threading.Event()
        self.is_playing = False
        self.last_active = 0.0  # for AEC-lite: audio can still be resonating briefly after playback ends
        self.assets_dir = os.path.join(models_dir(), 'assets')

    # Researched (2026-07-27): sd.wait()/blocking=True is a KNOWN Windows bug —
    # python-sounddevice issue #283 confirms it cuts playback off slightly
    # before the real end on Windows specifically, and that manual duration-
    # based waiting (sd.play() + time.sleep()) does NOT have this problem. So
    # duration-based waiting isn't a workaround to replace — it's the correct
    # approach here; switching to sd.wait() would make clipping WORSE, not
    # better. What actually needs fixing is precision: any timing slop (device
    # cold-start latency, this same Windows early-cutoff behavior, scheduling
    # jitter) must never be able to reach into real speech. Solved at the
    # source instead of guessed at here — see _pad_silence — every buffer
    # this waits on already has real trailing silence baked in, so an
    # early-by-a-few-hundred-ms cutoff can only ever eat silence.
    def _wait_for_playback(self, audio, samplerate):
        deadline = time.time() + len(audio) / samplerate
        while time.time() < deadline:
            if self.cancel_event.is_set():
                sd.stop()
                return
            self.last_active = time.time()
            time.sleep(0.02)

    # Common abbreviations whose period isn't a real sentence end — without this,
    # "Dr. Smith called" or "e.g. this one" split into a fragment + micro-pause
    # right after the abbreviation, since a period followed by a space looked
    # identical to a genuine sentence boundary.
    _ABBREVIATIONS = ("Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "St", "vs",
                       "etc", "e.g", "i.e", "approx", "vol", "no", "pg", "ch", "fig")
    _SENTENCE_SPLIT_RE = re.compile(
        r"(?<=[.!?])" + "".join(rf"(?<!\b{re.escape(a)}\.)" for a in _ABBREVIATIONS) + r" +"
    )

    def _chunk_text(self, text):
        # Split on sentence boundaries to allow streaming
        sentences = self._SENTENCE_SPLIT_RE.split(text)
        return [s.strip() for s in sentences if s.strip()]

    def _synth_sentence(self, sentence: str) -> np.ndarray:
        try:
            with self._pipeline_lock:
                chunks = [audio for _, _, audio in self.pipeline(sentence, voice=self.voice, speed=self.speed)]
            if not chunks:
                return np.zeros(0, dtype=np.float32)
            raw_pcm = np.concatenate(chunks).astype(np.float32)
            return pad_silence(trim_silence(raw_pcm), 24000)
        except Exception as e:
            print(f"Sentence synth error ('{sentence}'): {e}")
            return np.zeros(0, dtype=np.float32)

    def speak(self, text):
        """Blocking call to speak text. Multi-sentence outputs use parallel worker threads
        so Sentence 2 & 3 synthesize in background while Sentence 1 starts playing instantly.
        Interruptible via self.cancel_event."""
        import queue
        self.cancel_event.clear()
        self.is_playing = True
        sentences = self._chunk_text(text)
        if not sentences:
            self.is_playing = False
            return

        q = queue.Queue(maxsize=4)

        def producer():
            try:
                if len(sentences) > 1:
                    with ThreadPoolExecutor(max_workers=min(len(sentences), 3)) as executor:
                        futures = [executor.submit(self._synth_sentence, s) for s in sentences]
                        for f in futures:
                            if self.cancel_event.is_set():
                                break
                            audio = f.result()
                            if len(audio) > 0:
                                q.put(audio)
                else:
                    audio = self._synth_sentence(sentences[0])
                    if len(audio) > 0 and not self.cancel_event.is_set():
                        q.put(audio)
            except Exception as e:
                print(f"TTS generation error: {e}")
            finally:
                q.put(None)

        threading.Thread(target=producer, daemon=True).start()
        try:
            while not self.cancel_event.is_set():
                audio = q.get()
                if audio is None:
                    break
                sd.play(audio, 24000)
                self._wait_for_playback(audio, 24000)
        except Exception as e:
            print(f"TTS Error: {e}")
        finally:
            self.is_playing = False
            self.last_active = time.time()
            sd.stop()

    def speak_hybrid(self, prefix_asset_name: str, dynamic_text: str):
        """Instant playback: plays pre-baked static audio prefix from models/assets/ in 0ms,
        while dynamic_text generates in parallel and crossfades seamlessly into PCM playback."""
        self.cancel_event.clear()
        self.is_playing = True

        prefix_path = os.path.join(self.assets_dir, prefix_asset_name)
        if not os.path.exists(prefix_path):
            self.speak(dynamic_text)
            return

        try:
            # 1. Load pre-baked prefix audio (16kHz WAV from generate_assets)
            prefix_data, fs_prefix = sf.read(prefix_path, dtype='float32')
            prefix_data = pad_silence(prefix_data, fs_prefix)

            # Start dynamic text synthesis in background thread
            dynamic_audio_container = []
            def synth_dynamic():
                if dynamic_text and dynamic_text.strip():
                    dynamic_audio_container.append(self._synth_sentence(dynamic_text))

            synth_thread = threading.Thread(target=synth_dynamic, daemon=True)
            synth_thread.start()

            # 2. Play pre-baked prefix instantly (16kHz)
            sd.play(prefix_data, fs_prefix)
            self._wait_for_playback(prefix_data, fs_prefix)

            # Confirmed live (2026-07-30): under real system load (e.g. a concurrent
            # LLM call or wake-word training competing for the same GPU/CPU), Kokoro
            # synthesis of even a short dynamic phrase can exceed 3s — the old fixed
            # timeout then fell straight to step 3 with dynamic_audio_container still
            # empty, silently dropping the ENTIRE dynamic half of the sentence (e.g.
            # "Opening" played, "Desktop" never did) rather than just being briefly
            # late. 8s gives real headroom over normal synthesis time (well under 1s)
            # without blocking forever if the pipeline is ever genuinely stuck.
            synth_thread.join(timeout=8.0)
            if synth_thread.is_alive():
                print("speak_hybrid: dynamic synthesis still running after 8s, waiting for it rather than dropping the content")
                synth_thread.join()

            # 3. Play synthesized dynamic text (24kHz)
            if dynamic_audio_container and len(dynamic_audio_container[0]) > 0 and not self.cancel_event.is_set():
                dyn_pcm = dynamic_audio_container[0]
                sd.play(dyn_pcm, 24000)
                self._wait_for_playback(dyn_pcm, 24000)
        except Exception as e:
            print(f"speak_hybrid error: {e}")
        finally:
            self.is_playing = False
            self.last_active = time.time()
            sd.stop()

    def cancel(self):
        """Immediately stops playback."""
        print("TTS Cancelled.")
        self.cancel_event.set()
        sd.stop()
        self.is_playing = False

    def speak_now(self, text, speed=None):
        """Fire-and-forget single short utterance (typing echo, heartbeat filler).
        Docstring always said this shouldn't fight main assistant speech, but only
        callers (e.g. _execute_with_heartbeat's timer) ever checked is_playing —
        never this function itself, leaving a narrow race where real speech could
        start in the gap between that check and this call. Checked here directly
        now so every caller gets the guarantee, not just the ones that remember to."""
        if self.is_playing:
            return
        try:
            sd.stop()
            pcm = self._synth_sentence(text)
            if len(pcm) > 0:
                sd.play(pcm, 24000)
                self._wait_for_playback(pcm, 24000)
        except Exception as e:
            print(f"speak_now error: {e}")
