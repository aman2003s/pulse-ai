"""Train the 'pulse' wake-word model (v2).

Fixes v1's fatal bug: v1 labeled every window of a padded positive clip as
positive — including pure silence — so the model learned nothing.
v2 only labels windows that overlap actual speech, uses more voices/speeds,
noise/gain augmentation, many more negatives, and validates on held-out voices.
"""
import os
import numpy as np
import scipy.signal
import torch
from kokoro import KPipeline
from openwakeword.utils import AudioFeatures

import sys, types
sys.modules['openwakeword.data'] = types.ModuleType('openwakeword.data')
sys.modules['openwakeword.data'].generate_adversarial_texts = lambda *a, **k: []
sys.modules['openwakeword.data'].augment_clips = lambda *a, **k: []
sys.modules['openwakeword.data'].mmap_batch_generator = lambda *a, **k: []
from openwakeword.train import Model

MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models'))
CLIP_LEN = 48000  # 3.0s at 16k — must exceed 16 feature frames after the embedder's context window
rng = np.random.default_rng(42)

# WAKE_WORD is the phrase being trained. Internal class label always stays "pulse"
# (wake_listener.py matches on that fixed name) — this only changes what audio is positive.
WAKE_WORD = os.environ.get('PULSE_WAKE_WORD', 'pulse').strip().lower() or 'pulse'

TRAIN_VOICES = ['af_alloy', 'af_bella', 'af_nicole', 'af_sarah', 'am_michael', 'am_onyx', 'am_eric', 'am_echo', 'am_liam', 'am_puck', 'bf_emma', 'bm_george', 'bm_lewis']
HELDOUT_VOICES = ['af_heart', 'am_adam']
NEG_WORDS = [w for w in ["false", "impulse", "pulls", "pause", "plus", "pearls", "pillows", "purse",
             "jarvis", "computer", "hello", "okay", "please", "the", "and", "is",
             "open the file", "what time is it", "turn it up", "no thanks", "stop it"] if w != WAKE_WORD]

pipeline = KPipeline(lang_code='a')

def synth(text, voice, speed):
    audio = np.concatenate([a for _, _, a in pipeline(text, voice=voice, speed=speed)])
    a16 = scipy.signal.resample_poly(audio, up=2, down=3)
    return np.clip(a16, -1, 1).astype(np.float32)

def augment(clip):
    out = clip
    # pitch/speed jitter via resampling
    f = rng.uniform(0.93, 1.07)
    out = scipy.signal.resample_poly(out, int(1000 * f), 1000).astype(np.float32)
    out = out * rng.uniform(0.05, 0.9)                       # wide gain range — real mics are quiet
    out = out + rng.normal(0, rng.uniform(0.0005, 0.02), len(out))  # noise
    return np.clip(out, -1, 1).astype(np.float32)

def place(clip):
    """Place clip at a random offset inside a CLIP_LEN buffer; return buffer + speech frame range."""
    buf = np.zeros(CLIP_LEN, dtype=np.float32)
    clip = clip[:CLIP_LEN]
    off = rng.integers(0, CLIP_LEN - len(clip) + 1)
    buf[off:off + len(clip)] = clip
    # oww features: 1 frame per 1280 samples (80ms)
    f_start, f_end = off // 1280, (off + len(clip)) // 1280
    return (buf * 32767).astype(np.int16), f_start, f_end

def positive_windows(feats, f_start, f_end):
    """Yield 16-frame windows whose END lands just after the speech ends (like streaming detection)."""
    X = []
    for end in range(max(f_end - 1, 16), min(f_end + 3, feats.shape[0]) + 1):
        w = feats[end - 16:end, :]
        if w.shape == (16, 96):
            X.append(w)
    return X

def all_windows(feats, step=2):
    return [feats[i:i + 16, :] for i in range(0, feats.shape[0] - 15, step)]

print("Synthesizing clips...")
pos_raw, neg_raw = [], []  # (int16 buffer, f_start, f_end)
for v in TRAIN_VOICES:
    for speed in [0.8, 0.9, 1.0, 1.1, 1.25]:
        for text in [WAKE_WORD, WAKE_WORD + "."]:
            base = synth(text, v, speed)
            for _ in range(3):
                pos_raw.append(place(augment(base)))

# user-recorded real samples (from record_pulse_samples.py) — weighted heavily
import glob
import scipy.io.wavfile as wf
user_wavs = glob.glob(os.path.join(MODELS_DIR, 'user_samples', '*.wav'))
for w in user_wavs:
    sr, a = wf.read(w)
    a = a.astype(np.float32) / 32767.0
    # trim leading/trailing silence (simple energy gate)
    e = np.abs(a) > 0.02
    if e.any():
        a = a[max(np.argmax(e) - 800, 0):len(a) - np.argmax(e[::-1]) + 800]
    for _ in range(25):
        pos_raw.append(place(augment(a)))
print(f"user samples: {len(user_wavs)}")
for v in TRAIN_VOICES[::2]:
    for w in NEG_WORDS:
        base = synth(w, v, 1.0)
        neg_raw.append(place(augment(base)))
# pure noise / silence negatives
for _ in range(40):
    lvl = rng.uniform(0.0, 0.05)
    neg_raw.append(((rng.normal(0, lvl, CLIP_LEN) * 32767).clip(-32767, 32767).astype(np.int16), 0, 0))

print(f"pos={len(pos_raw)} neg={len(neg_raw)}; extracting features...")
F = AudioFeatures(device='cpu', ncpu=4)
pos_feats = F.embed_clips(np.array([b for b, _, _ in pos_raw]), batch_size=32)
neg_feats = F.embed_clips(np.array([b for b, _, _ in neg_raw]), batch_size=32)

X, Y = [], []
for feats, (_, fs, fe) in zip(pos_feats, pos_raw):
    for w in positive_windows(feats, fs, fe):
        X.append(w); Y.append(1)
    # silence regions of positive clips are negatives
    for i in range(0, max(fs - 16, 0), 4):
        X.append(feats[i:i + 16, :]); Y.append(0)
for feats in neg_feats:
    for w in all_windows(feats):
        X.append(w); Y.append(0)

X = np.array(X, dtype=np.float32); Y = np.array(Y, dtype=np.float32)
idx = rng.permutation(len(X)); X, Y = X[idx], Y[idx]
print(f"windows: {len(X)} ({int(Y.sum())} positive)")

model = Model(n_classes=1, input_shape=(16, 96), model_type="dnn", layer_dim=128, n_blocks=1)
opt = torch.optim.Adam(model.model.parameters(), lr=0.001)
# weight positives higher since they're the minority
crit = torch.nn.BCELoss()
Xt = torch.tensor(X); Yt = torch.tensor(Y).unsqueeze(1)
n = len(Xt)
for epoch in range(100):
    perm = torch.randperm(n)
    tot = 0.0
    for i in range(0, n, 512):
        b = perm[i:i + 512]
        opt.zero_grad()
        loss = crit(model.model(Xt[b]), Yt[b])
        loss.backward(); opt.step(); tot += loss.item()
    if epoch % 10 == 0:
        print(f"epoch {epoch} loss {tot / (n // 512 + 1):.4f}")

out = os.path.join(MODELS_DIR, 'pulse_v2.onnx')
model.export_to_onnx(out, class_mapping="pulse")
print(f"exported {out}")

# ---- validation on held-out voices (streaming, like the live listener) ----
from openwakeword.model import Model as OWWModel
oww = OWWModel(wakeword_models=[out], inference_framework='onnx')

def stream_peak(int16buf):
    oww.reset()
    peak = 0.0
    for i in range(0, len(int16buf) - 1280, 1280):
        peak = max(peak, float(max(oww.predict(int16buf[i:i + 1280]).values())))
    return peak

print("\nVALIDATION (held-out voices):")
ok = True
for v in HELDOUT_VOICES:
    for speed in [0.9, 1.1]:
        buf, _, _ = place(synth(WAKE_WORD, v, speed))
        pk = stream_peak(buf)
        print(f"  POS {v} x{speed}: {pk:.3f} {'PASS' if pk > 0.5 else 'FAIL'}")
        ok = ok and pk > 0.5
for v in HELDOUT_VOICES:
    for w in ["impulse", "pause", "hello there"]:
        buf, _, _ = place(synth(w, v, 1.0))
        pk = stream_peak(buf)
        print(f"  NEG '{w}' {v}: {pk:.3f} {'PASS' if pk < 0.4 else 'FAIL'}")
        ok = ok and pk < 0.4
sil = stream_peak(np.zeros(CLIP_LEN, dtype=np.int16))
print(f"  NEG silence: {sil:.3f} {'PASS' if sil < 0.3 else 'FAIL'}")
print("\nRESULT:", "ALL PASS" if ok and sil < 0.3 else "SOME FAILED — tune before shipping")
