"""Live wake-word debug: shows mic level and 'pulse' model score in real time.
Run:  venv\\Scripts\\python.exe scripts\\wake_debug.py   then say "pulse" a few times.
"""
import os, sys, time
import numpy as np
import sounddevice as sd
from openwakeword.model import Model

MODEL = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models', 'pulse.onnx'))

print("Default input device:", sd.query_devices(kind='input')['name'])
oww = Model(wakeword_models=[MODEL], inference_framework="onnx")
print("Model keys:", list(oww.models.keys()))
peak = 0.0

def cb(indata, frames, t, status):
    global peak
    audio = indata.flatten()
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    score = max(oww.predict(audio).values())
    peak = max(peak, score)
    bar = "#" * int(score * 40)
    print(f"\rmic_rms={rms:6.0f}  score={score:.3f} peak={peak:.3f} {bar:<40}", end="")

with sd.InputStream(samplerate=16000, blocksize=1280, channels=1, dtype='int16', callback=cb):
    print("Say 'pulse' now. Ctrl+C to quit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\nPeak score this session: {peak:.3f}  (listener triggers at >0.5)")
