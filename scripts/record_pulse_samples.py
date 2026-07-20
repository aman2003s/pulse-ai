"""
record_pulse_samples.py — Collect 5+ voice samples for wake word training.

DESIGN (same pattern as Google "Hey Google" / Alexa onboarding):
  • All audio prompts are pre-baked static WAV files — zero AI, zero lag.
  • Each round:
      1. Play "prompt_say.wav"  →  play "beep_start.wav"
      2. Record until silence OR timeout (max 3 s)
      3. Energy check (adaptive threshold calibrated against ambient noise)
         ✓ enough energy  →  play beep_ok + save sample
         ✗ too quiet      →  play beep_bad + retry (up to MAX_RETRIES)
  • If a sample fails MAX_RETRIES times, the session skips it with a warning
    and continues — a partial dataset is still better than hanging forever.

Run:
  venv\\Scripts\\python.exe scripts\\record_pulse_samples.py
"""
import os
import sys
import time
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
import scipy.io.wavfile as wf

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ASSETS = os.path.join(ROOT, 'models', 'assets')
SAMPLES_DIR = os.path.join(ROOT, 'models', 'user_samples')
os.makedirs(SAMPLES_DIR, exist_ok=True)

# ── settings ──────────────────────────────────────────────────────────────────
SR = 16000
NUM_SAMPLES = 5          # how many good samples to collect
MAX_RETRIES = 3          # retries per sample before skipping
RECORD_MAX_S = 2.5       # max recording length per attempt
SILENCE_MS = 600         # ms of silence after speech ends recording
VAD_CHUNK = 512          # 32 ms at 16kHz  (fast, no Silero needed here)
ENERGY_MULTIPLIER = 4.0  # ambient_rms × this = accept threshold


# ── audio helpers ─────────────────────────────────────────────────────────────
def _load(name):
    """Load a pre-baked asset WAV. Auto-generate assets if missing."""
    path = os.path.join(ASSETS, name)
    if not os.path.exists(path):
        print(f"  [!] Asset '{name}' not found — running generate_assets.py ...")
        import subprocess
        subprocess.run(
            [sys.executable, os.path.join(ROOT, 'scripts', 'generate_assets.py')],
            check=True
        )
    data, sr = sf.read(path, dtype='float32')
    return data, sr


def play(name):
    """Play a pre-baked asset synchronously. Never generates audio on the fly."""
    data, sr = _load(name)
    sd.stop()
    sd.play(data, sr)
    sd.wait()


def _rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))


# ── ambient calibration ───────────────────────────────────────────────────────
def calibrate_ambient(duration_s=1.0) -> float:
    """
    Record room noise for 1 second to set an adaptive energy threshold.
    Same approach as Google/Alexa onboarding: measure ambient before prompting.
    """
    print("  Calibrating microphone — please stay quiet for 1 second...")
    buf = sd.rec(int(SR * duration_s), samplerate=SR, channels=1, dtype='int16')
    sd.wait()
    ambient = _rms(buf.flatten())
    threshold = max(ambient * ENERGY_MULTIPLIER, 80)  # floor of 80 RMS
    print(f"  Ambient RMS = {ambient:.1f}  |  Accept threshold = {threshold:.1f}")
    return threshold


# ── VAD-lite recording ─────────────────────────────────────────────────────────
def record_until_silence(threshold: float) -> np.ndarray | None:
    """
    Record from mic in 32ms chunks.
    - Starts buffering immediately after the beep.
    - Returns the int16 numpy array once:
        (a) speech detected then silence >= SILENCE_MS, or
        (b) RECORD_MAX_S elapsed.
    - Returns None if NO speech was detected at all (silent sample → retry).
    """
    chunks = []
    has_spoken = False
    silent_since = None
    started = time.time()

    with sd.InputStream(samplerate=SR, channels=1, dtype='int16',
                        blocksize=VAD_CHUNK) as stream:
        while True:
            chunk, _ = stream.read(VAD_CHUNK)
            flat = chunk.flatten()
            chunks.append(flat)
            energy = _rms(flat)

            if energy >= threshold:
                has_spoken = True
                silent_since = None
            else:
                if has_spoken:
                    if silent_since is None:
                        silent_since = time.time()
                    elif (time.time() - silent_since) * 1000 >= SILENCE_MS:
                        break  # normal end-of-speech

            if time.time() - started >= RECORD_MAX_S:
                break

    if not has_spoken:
        return None  # completely silent — caller will retry

    return np.concatenate(chunks)


# ── find next available sample index ─────────────────────────────────────────
def next_sample_index() -> int:
    existing = [
        f for f in os.listdir(SAMPLES_DIR)
        if f.startswith('sample_') and f.endswith('.wav')
    ]
    if not existing:
        return 1
    nums = []
    for f in existing:
        try:
            nums.append(int(f.replace('sample_', '').replace('.wav', '')))
        except ValueError:
            pass
    return max(nums) + 1 if nums else 1


# ── main collection loop ──────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 60)
    print("  Pulse  —  Wake Word Sample Collection")
    print("=" * 60)
    print(f"\nWe'll record {NUM_SAMPLES} samples of you saying 'pulse'.")
    print("Speak naturally at your normal distance from the mic.")
    print("A beep will signal when to start. No button pressing needed.\n")

    input("Press Enter when you're ready to begin...")
    print()

    # calibrate once at the start
    threshold = calibrate_ambient()
    print()
    time.sleep(0.3)

    collected = 0
    sample_idx = next_sample_index()

    while collected < NUM_SAMPLES:
        sample_num = collected + 1
        print(f"[{sample_num}/{NUM_SAMPLES}]  Say 'pulse' after the beep...")

        attempt = 0
        saved = False

        while attempt < MAX_RETRIES and not saved:
            if attempt > 0:
                play("beep_bad.wav")
                time.sleep(0.15)
                print(f"         Too quiet — try again (attempt {attempt + 1}/{MAX_RETRIES})")

            # play the "say it now" prompt then the start beep
            play("prompt_say.wav" if attempt == 0 else "prompt_again.wav")
            time.sleep(0.05)
            play("beep_start.wav")

            audio = record_until_silence(threshold)

            if audio is None:
                attempt += 1
                continue  # nothing detected — retry

            peak_rms = _rms(audio)
            if peak_rms < threshold:
                attempt += 1
                continue  # too quiet — retry

            # ✓ good sample
            path = os.path.join(SAMPLES_DIR, f'sample_{sample_idx}.wav')
            wf.write(path, SR, audio)
            play("beep_ok.wav")
            print(f"         ✓  Saved  {os.path.basename(path)}  (RMS={peak_rms:.0f})")
            collected += 1
            sample_idx += 1
            saved = True

        if not saved:
            print(f"         ⚠  Skipped sample {sample_num} after {MAX_RETRIES} failed attempts.")
            print("            (Move closer to your microphone and try again later.)")
            collected += 1  # count as done to avoid infinite loop

        time.sleep(0.4)

    print()
    play("prompt_done.wav")
    print("=" * 60)
    print(f"  Done!  {len([f for f in os.listdir(SAMPLES_DIR) if f.endswith('.wav')])} "
          f"total samples in  {SAMPLES_DIR}")
    print("  Run  venv\\Scripts\\python.exe scripts\\train_pulse_v2.py  to retrain.")
    print("=" * 60)


if __name__ == '__main__':
    main()
