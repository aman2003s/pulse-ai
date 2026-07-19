"""Record 5 samples of you saying 'pulse' for personalized wake-word training.
Run:  venv\\Scripts\\python.exe scripts\\record_pulse_samples.py
"""
import os, time
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wf

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models', 'user_samples'))
os.makedirs(OUT, exist_ok=True)

print("You'll record 5 clips. Say 'pulse' naturally once per clip, at your normal distance from the mic.")
for i in range(1, 6):
    input(f"\n[{i}/5] Press Enter, then say 'pulse'...")
    audio = sd.rec(int(2.0 * 16000), samplerate=16000, channels=1, dtype='int16')
    sd.wait()
    a = audio.flatten()
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    path = os.path.join(OUT, f"pulse_{i}.wav")
    wf.write(path, 16000, a)
    print(f"  saved {path} (rms={rms:.0f}{' — very quiet, move closer?' if rms < 100 else ''})")
print("\nDone. Now tell Claude to retrain.")
