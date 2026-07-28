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

def pad_note(freq, duration_s, amplitude=0.6, attack_ms=30, release_ms=110):
    """A warm, held 'synth pad' note — soft swell-in, brief sustain, smooth cosine
    release to true zero. Siri's actual chime is described (per published sound-
    design analysis) as 'a soft two-note rising melody, somewhere between a doorbell
    and a synth pad', using lower-mid/upper-midbass frequencies, not bright high
    ones — the earlier attempts were pitched too high and too percussive."""
    n = int(SR * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    # Fundamental + gentle upper warmth + a touch of sub-octave for body — kept
    # soft/rounded, not bright (no 3rd harmonic, low weights throughout).
    signal = (np.sin(2 * np.pi * freq * t)
              + 0.12 * np.sin(2 * np.pi * freq * 2 * t)
              + 0.05 * np.sin(2 * np.pi * freq * 0.5 * t))
    env = np.ones(n)
    n_att = int(SR * attack_ms / 1000)
    env[:n_att] = np.linspace(0, 1, n_att) ** 1.3  # gentle swell-in, not a pluck
    n_rel = int(SR * release_ms / 1000)
    env[-n_rel:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, n_rel)))  # smooth to true 0
    return (signal * env * amplitude).astype(np.float32)

# Soft two-note rising chime, doorbell/pad character: G4 -> C5 (lower-mid register),
# each note held and released gently rather than struck and cut short. A small
# clean silent gap between them, NOT an overlap — overlapping two different pitches
# sums into audible beating/interference (two sine waves at different frequencies
# added together produce a warbling amplitude artifact), which is exactly the
# "stretched/breaking" sound just reported. A brief real gap has no such artifact.
save_pcm("ack.wav", np.concatenate([
    pad_note(392.00, 0.20, amplitude=0.62, attack_ms=30, release_ms=90),
    gap(0.03),
    pad_note(523.25, 0.24, amplitude=0.58, attack_ms=25, release_ms=120),
]))


# ── 2. Spoken prompts via Kokoro (same voice as rest of app) ─────────────────
print("\nLoading Kokoro TTS (same voice used throughout the app)...")
from kokoro import KPipeline

pipeline = KPipeline(lang_code='a')
VOICE = 'af_heart'   # must match TTSService default in core/voice/tts.py
SPEED = 1.0


def kokoro_to_wav(text, filename):
    """Synthesise `text` with Kokoro and save at its NATIVE 24kHz — no downsample.
    These assets (especially the prefix_*.wav ones) get spliced directly against
    live Kokoro synthesis in speak_hybrid(); resampling them to 16kHz cuts
    everything above 8kHz and made the pre-baked prefix sound audibly duller/
    muffled than the dynamic part spoken right after it in the same sentence —
    a real, confirmed tone mismatch, not just perception. Playback already reads
    the rate from the file itself (sf.read + sd.play(data, fs)), so writing 24kHz
    here needs no other code changes."""
    chunks = [audio for _, _, audio in pipeline(text, voice=VOICE, speed=SPEED)]
    audio_24k = np.concatenate(chunks).astype(np.float32)
    path = os.path.join(ASSETS, filename)
    pcm = (np.clip(audio_24k, -1, 1) * 32767).astype(np.int16)
    wf.write(path, 24000, pcm)
    print(f"  wrote  {path}  ({len(audio_24k) / 24000 * 1000:.0f} ms @ 24kHz)")


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
