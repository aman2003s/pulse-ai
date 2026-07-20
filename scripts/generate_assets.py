"""
Generate all static audio assets used by the Pulse training flow.

This runs ONCE (or after updates) and produces pre-baked WAV files in
models/assets/ that are committed to the repo. Nothing in the training
or recording scripts will ever call TTS or AI at runtime.

Assets generated:
  models/assets/beep_start.wav   — 440 Hz tone (180ms)  "start recording"
  models/assets/beep_ok.wav      — ascending 880→1047 Hz "sample accepted"
  models/assets/beep_bad.wav     — descending 660→440 Hz "sample rejected"
  models/assets/prompt_say.wav   — spoken "Say pulse now" (pre-recorded text)
  models/assets/prompt_again.wav — spoken "Again, say pulse" 
  models/assets/prompt_done.wav  — spoken "Training samples collected"
  models/assets/ack.wav          — wake ack tone (two quick beeps, replaces Kokoro ack)

Run:
  venv\\Scripts\\python.exe scripts\\generate_assets.py
"""
import os
import numpy as np
import scipy.io.wavfile as wf

ASSETS = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models', 'assets'))
os.makedirs(ASSETS, exist_ok=True)

SR = 16000  # all assets at 16kHz so sounddevice never needs to switch rates


def sine(freq, duration_s, sr=SR, amplitude=0.6):
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.float32)


def fade(audio, fade_ms=10, sr=SR):
    """Apply linear fade-in and fade-out to avoid clicks."""
    n = int(sr * fade_ms / 1000)
    ramp = np.linspace(0, 1, n)
    out = audio.copy()
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def save(name, audio, sr=SR):
    path = os.path.join(ASSETS, name)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    wf.write(path, sr, pcm)
    print(f"  wrote {path}  ({len(audio)/sr*1000:.0f} ms)")


def silence(duration_s, sr=SR):
    return np.zeros(int(sr * duration_s), dtype=np.float32)


# ── beep_start.wav ────────────────────────────────────────────────────────────
# Single 440 Hz tone, 180 ms — "get ready to speak"
beep_start = fade(sine(440, 0.18))
save("beep_start.wav", beep_start)

# ── beep_ok.wav ───────────────────────────────────────────────────────────────
# Ascending two-tone: 880 Hz → 1047 Hz (musical interval, positive feel)
beep_ok = np.concatenate([
    fade(sine(880, 0.12)),
    silence(0.03),
    fade(sine(1047, 0.15)),
])
save("beep_ok.wav", beep_ok)

# ── beep_bad.wav ──────────────────────────────────────────────────────────────
# Descending two-tone: 660 Hz → 440 Hz (negative feel, "try again")
beep_bad = np.concatenate([
    fade(sine(660, 0.12)),
    silence(0.03),
    fade(sine(440, 0.15)),
])
save("beep_bad.wav", beep_bad)

# ── ack.wav ───────────────────────────────────────────────────────────────────
# Two quick high beeps — the main-app "I heard you" acknowledgement.
# Replaces the Kokoro-generated ack so the live app has no TTS startup lag.
ack = np.concatenate([
    fade(sine(1047, 0.08)),
    silence(0.05),
    fade(sine(1047, 0.08)),
])
save("ack.wav", ack)

# ── prompt_say.wav ────────────────────────────────────────────────────────────
# "Say pulse now" — uses pyttsx3 (offline TTS, ships with Windows/macOS/Linux,
# no model download required) if available; otherwise falls back to a
# pre-synthesised melody pattern that is still clearly distinct from the beeps.
def make_spoken_prompt(text, filename):
    """Try pyttsx3 (offline, zero-download TTS). Falls back to a pattern tone."""
    try:
        import pyttsx3, tempfile, soundfile as sf
        engine = pyttsx3.init()
        engine.setProperty('rate', 140)
        engine.setProperty('volume', 0.95)
        tmp = tempfile.mktemp(suffix=".wav")
        engine.save_to_file(text, tmp)
        engine.runAndWait()
        data, sr_in = sf.read(tmp)
        os.unlink(tmp)
        if sr_in != SR:
            import scipy.signal
            data = scipy.signal.resample_poly(
                data.astype(np.float32),
                SR, sr_in
            )
        save(filename, data.astype(np.float32))
        return True
    except Exception as e:
        print(f"  pyttsx3 unavailable ({e}), using tone pattern for {filename}")
        return False

spoken_ok = make_spoken_prompt("Say pulse now", "prompt_say.wav")
if not spoken_ok:
    # Fallback: three rising tones that clearly mean "your turn"
    fallback = np.concatenate([
        fade(sine(523, 0.10)), silence(0.04),   # C5
        fade(sine(659, 0.10)), silence(0.04),   # E5
        fade(sine(784, 0.14)),                   # G5
    ])
    save("prompt_say.wav", fallback)

spoken_ok = make_spoken_prompt("Again, say pulse", "prompt_again.wav")
if not spoken_ok:
    fallback = np.concatenate([
        fade(sine(440, 0.10)), silence(0.04),
        fade(sine(523, 0.10)), silence(0.04),
        fade(sine(659, 0.14)),
    ])
    save("prompt_again.wav", fallback)

spoken_ok = make_spoken_prompt("Training complete. All samples collected.", "prompt_done.wav")
if not spoken_ok:
    # Long ascending arpeggio — celebratory finish
    fallback = np.concatenate([
        fade(sine(523, 0.10)), silence(0.03),
        fade(sine(659, 0.10)), silence(0.03),
        fade(sine(784, 0.10)), silence(0.03),
        fade(sine(1047, 0.20)),
    ])
    save("prompt_done.wav", fallback)

# ── Copy ack.wav to models/ack.wav for backward compat with capture.py ────────
import shutil
src = os.path.join(ASSETS, "ack.wav")
dst = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models', 'ack.wav'))
shutil.copy2(src, dst)
print(f"  also wrote {dst} (capture.py compat)")

print("\nAll assets generated. Commit models/assets/ to the repo.")
