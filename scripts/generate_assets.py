"""
Generate all static audio assets used by the Pulse training flow.

This runs ONCE and produces pre-baked WAV files in models/assets/.
Spoken prompts are synthesised using Kokoro (same voice as the whole app)
so the training experience is 100% consistent with the live assistant.
Nothing in the recording or training scripts ever calls TTS at runtime.

Assets generated:
  models/assets/beep_start.wav   — 440 Hz tone (180ms)  "start recording"
  models/assets/beep_ok.wav      — ascending 880->1047 Hz "sample accepted"
  models/assets/beep_bad.wav     — descending 660->440 Hz "sample rejected"
  models/assets/ack.wav          — two quick beeps (live-app wake ack)
  models/assets/prompt_say.wav   — Kokoro: "Say pulse now"
  models/assets/prompt_again.wav — Kokoro: "Again, say pulse"
  models/assets/prompt_done.wav  — Kokoro: "Training complete. All samples collected."

Run once (or whenever prompts need refreshing):
  venv\\Scripts\\python.exe scripts\\generate_assets.py
"""
import os
import shutil
import numpy as np
import scipy.io.wavfile as wf
import scipy.signal
import soundfile as sf

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ASSETS = os.path.join(ROOT, 'models', 'assets')
os.makedirs(ASSETS, exist_ok=True)

SR = 16000  # all assets at 16 kHz — sounddevice never needs to switch rates


# ── pure-math helpers ─────────────────────────────────────────────────────────
def sine(freq, duration_s, amplitude=0.6):
    t = np.linspace(0, duration_s, int(SR * duration_s), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.float32)


def fade(audio, fade_ms=10):
    n = int(SR * fade_ms / 1000)
    ramp = np.linspace(0, 1, n)
    out = audio.copy()
    out[:n]  *= ramp
    out[-n:] *= ramp[::-1]
    return out


def gap(duration_s):
    return np.zeros(int(SR * duration_s), dtype=np.float32)


def save_pcm(name, audio_f32):
    path = os.path.join(ASSETS, name)
    pcm = (np.clip(audio_f32, -1, 1) * 32767).astype(np.int16)
    wf.write(path, SR, pcm)
    print(f"  wrote  {path}  ({len(audio_f32) / SR * 1000:.0f} ms)")


# ── 1. Beep tones (pure math, instant) ───────────────────────────────────────
print("Generating beep tones...")

save_pcm("beep_start.wav", fade(sine(440, 0.18)))

save_pcm("beep_ok.wav", np.concatenate([
    fade(sine(880,  0.12)), gap(0.03),
    fade(sine(1047, 0.15)),
]))

save_pcm("beep_bad.wav", np.concatenate([
    fade(sine(660, 0.12)), gap(0.03),
    fade(sine(440, 0.15)),
]))

# Live-app wake acknowledgement — the one sound users hear on every single wake,
# so it needs to actually be heard. The old version (two identical 80ms beeps at
# amplitude 0.6) was reported too quiet to notice. This is a louder ascending
# two-note chime (like Google/Alexa's "I heard you" tone — a rising interval reads
# as confirmation) at near-max amplitude, still short enough to feel instant.
save_pcm("ack.wav", np.concatenate([
    fade(sine(784,  0.13, amplitude=0.9)), gap(0.02),
    fade(sine(1047, 0.16, amplitude=0.9)),
]))


# ── 2. Spoken prompts via Kokoro (same voice as rest of app) ─────────────────
print("\nLoading Kokoro TTS (same voice used throughout the app)...")
from kokoro import KPipeline

pipeline = KPipeline(lang_code='a')
VOICE = 'af_heart'   # must match TTSService default in core/voice/tts.py
SPEED = 1.0


def kokoro_to_wav(text, filename):
    """Synthesise `text` with Kokoro, resample to 16 kHz, save as asset."""
    # Kokoro outputs at 24 kHz
    chunks = [audio for _, _, audio in pipeline(text, voice=VOICE, speed=SPEED)]
    audio_24k = np.concatenate(chunks).astype(np.float32)

    # Resample 24000 -> 16000 (ratio 2/3)
    audio_16k = scipy.signal.resample_poly(audio_24k, up=2, down=3).astype(np.float32)

    save_pcm(filename, audio_16k)


print("Synthesising spoken prompts with Kokoro...")
kokoro_to_wav("Say pulse now.",
              "prompt_say.wav")

kokoro_to_wav("Again, say pulse.",
              "prompt_again.wav")

kokoro_to_wav("Training complete. All samples collected.",
              "prompt_done.wav")

kokoro_to_wav("Training complete. Pulse is now your trained wake word.",
              "prompt_trained.wav")

print("\nSynthesising fast-path prefix assets with Kokoro...")
kokoro_to_wav("Opening ", "prefix_opening.wav")
kokoro_to_wav("I've opened ", "prefix_opened.wav")
kokoro_to_wav("Searching for ", "prefix_searching.wav")
kokoro_to_wav("I found ", "prefix_found.wav")
kokoro_to_wav("Closing ", "prefix_closing.wav")
kokoro_to_wav("Reading ", "prefix_reading.wav")


# ── 3. Copy ack.wav to models/ack.wav for backward compat with capture.py ─────
src = os.path.join(ASSETS, "ack.wav")
dst = os.path.join(ROOT, "models", "ack.wav")
shutil.copy2(src, dst)
print(f"\n  also copied -> {dst}  (capture.py backward compat)")

print("\nDone. All assets are pre-baked — no AI runs during training or recording.")
