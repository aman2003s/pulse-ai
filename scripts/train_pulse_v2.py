"""Train the 'pulse' wake-word model (v2).

Fixes v1's fatal bug: v1 labeled every window of a padded positive clip as
positive — including pure silence — so the model learned nothing.
v2 only labels windows that overlap actual speech, uses more voices/speeds,
noise/gain augmentation, many more negatives, and validates on held-out voices.
"""
import os
import sys
import types
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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

# The embedder produces FEWER frames than CLIP_LEN's nominal 3s would suggest (measured: 28,
# not 37). place() below must never let a clip's END land past this — otherwise the labeling
# functions either silently drop the example (the crash we chased earlier) or, worse, mislabel
# whatever frames ARE available as "the word just ended" even when that's untrue for THIS clip
# (this collapsed a trained model into firing 100% confidence on pure silence). Measuring it
# directly here instead of hardcoding a number keeps this correct if the embedder ever changes.
F = AudioFeatures(device='cpu', ncpu=4)
FRAMES_PER_CLIP = F.embed_clips(np.zeros((1, CLIP_LEN), dtype=np.int16), batch_size=1)[0].shape[0]

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
    buf = np.zeros(CLIP_LEN, dtype=np.float32)
    clip = clip[:CLIP_LEN]
    # OpenWakeWord's Google Speech Embedding model requires 76 melspec frames (760ms = 12160 samples)
    # of audio context before emitting each 80ms feature frame.
    RECEPTIVE_FIELD_SAMPLES = 12160
    max_off = max(0, CLIP_LEN - len(clip))
    off = rng.integers(0, max_off + 1)
    buf[off:off + len(clip)] = clip
    # f_end is the feature frame index corresponding to when the spoken word actually finishes
    f_end = max(16, (off + len(clip) - RECEPTIVE_FIELD_SAMPLES) // 1280)
    f_start = max(0, (off - RECEPTIVE_FIELD_SAMPLES) // 1280)
    return (buf * 32767).astype(np.int16), f_start, f_end

def positive_windows(feats, f_start, f_end):
    """Yield 16-frame windows whose END lands just after the speech ends (like streaming detection).
    place() now guarantees f_end is always within feats.shape[0] — no clamping needed here."""
    X = []
    for end in range(max(f_end - 1, 16), min(f_end + 3, feats.shape[0]) + 1):
        w = feats[end - 16:end, :]
        if w.shape == (16, 96):
            X.append(w)
    return X

def all_windows(feats, step=2):
    return [feats[i:i + 16, :] for i in range(0, feats.shape[0] - 15, step)]

import time
t0 = time.time()

def elapsed():
    s = int(time.time() - t0)
    return f"{s//60}m{s%60:02d}s"

print(f"[{elapsed()}] Synthesising positive clips ({len(TRAIN_VOICES)} voices x 5 speeds)...")
pos_raw, neg_raw = [], []  # (int16 buffer, f_start, f_end)
for vi, v in enumerate(TRAIN_VOICES, 1):
    print(f"  [{elapsed()}] voice {vi}/{len(TRAIN_VOICES)}: {v}")
    for speed in [0.8, 0.9, 1.0, 1.1, 1.25]:
        for text in [WAKE_WORD, WAKE_WORD + "."]:
            base = synth(text, v, speed)
            for _ in range(3):
                pos_raw.append(place(augment(base)))

# user-recorded real samples — weighted heavily (40x each)
import glob
import scipy.io.wavfile as wf
user_wavs = [w for w in glob.glob(os.path.join(MODELS_DIR, 'user_samples', '*.wav'))
             if not os.path.basename(w).startswith('_')]
USER_WEIGHT = 40
print(f"[{elapsed()}] Loading {len(user_wavs)} user voice samples (x{USER_WEIGHT} augmentations each)...")
for wi, w in enumerate(user_wavs, 1):
    sr, a = wf.read(w)
    a = a.astype(np.float32) / 32767.0
    e = np.abs(a) > 0.02
    if e.any():
        a = a[max(np.argmax(e) - 800, 0):len(a) - np.argmax(e[::-1]) + 800]
    for _ in range(USER_WEIGHT):
        pos_raw.append(place(augment(a)))
    print(f"  [{elapsed()}] user sample {wi}/{len(user_wavs)}: {os.path.basename(w)}")

print(f"[{elapsed()}] Synthesising negative clips ({len(TRAIN_VOICES[::2])} voices x {len(NEG_WORDS)} words)...")
for vi, v in enumerate(TRAIN_VOICES[::2], 1):
    print(f"  [{elapsed()}] neg voice {vi}/{len(TRAIN_VOICES[::2])}: {v}")
    for w in NEG_WORDS:
        base = synth(w, v, 1.0)
        neg_raw.append(place(augment(base)))
# pure noise / silence negatives
for _ in range(40):
    lvl = rng.uniform(0.0, 0.05)
    neg_raw.append(((rng.normal(0, lvl, CLIP_LEN) * 32767).clip(-32767, 32767).astype(np.int16), 0, 0))
print(f"[{elapsed()}] Synthesis done — pos={len(pos_raw)} neg={len(neg_raw)}")

print(f"[{elapsed()}] Extracting audio features ({len(pos_raw) + len(neg_raw)} clips)...")
# F was already created near the top of the file (needed there for the FRAMES_PER_CLIP probe) — reused, not recreated.

def embed_with_progress(label, raw_list, chunk=128):
    # embed_clips() has no progress callback and this dataset can be 1000+ clips once
    # real samples accumulate (40x augmentation each) — chunking it just for periodic
    # prints keeps the UI updating instead of sitting frozen on one line for minutes.
    bufs = np.array([b for b, _, _ in raw_list])
    if len(bufs) == 0:
        return []
    out = []
    for i in range(0, len(bufs), chunk):
        out.extend(F.embed_clips(bufs[i:i + chunk], batch_size=32))
        done = min(i + chunk, len(bufs))
        print(f"  [{elapsed()}] {label}: {done}/{len(bufs)} clips")
    return out

pos_feats = embed_with_progress("positive features", pos_raw)
neg_feats = embed_with_progress("negative features", neg_raw)

X, Y = [], []
for feats, (_, fs, fe) in zip(pos_feats, pos_raw):
    for w in positive_windows(feats, fs, fe):
        X.append(w); Y.append(1)
    # silence regions of positive clips are negatives. fs (frame-start) comes from the
    # RAW buffer's random placement offset, not from the embedder's actual output frame
    # count — for a short clip placed late in the 3s buffer, fs can exceed feats.shape[0],
    # making this slice come back short of 16 rows and breaking np.array(X)'s uniform-shape
    # assumption. positive_windows() above already guards this; this loop just hadn't.
    for i in range(0, max(fs - 16, 0), 4):
        w = feats[i:i + 16, :]
        if w.shape[0] == 16:
            X.append(w); Y.append(0)
for feats in neg_feats:
    for w in all_windows(feats):
        X.append(w); Y.append(0)

X = np.array(X, dtype=np.float32); Y = np.array(Y, dtype=np.float32)
idx = rng.permutation(len(X)); X, Y = X[idx], Y[idx]
print(f"windows: {len(X)} ({int(Y.sum())} positive)")

# ── weighted BCE loss: correct for class imbalance automatically ─────────────
n_pos = int(Y.sum())
n_neg = len(Y) - n_pos
pos_weight_val = n_neg / max(n_pos, 1)   # e.g. 8.0 if negatives outnumber positives 8:1
print(f"Class balance: {n_pos} positive, {n_neg} negative  ->  pos_weight={pos_weight_val:.2f}")

def run_training(lr=0.001, epochs=120):
    m = Model(n_classes=1, input_shape=(16, 96), model_type="dnn", layer_dim=128, n_blocks=1)
    opt = torch.optim.Adam(m.model.parameters(), lr=lr)
    pw = torch.tensor([pos_weight_val])
    crit = torch.nn.BCEWithLogitsLoss(pos_weight=pw)
    Xt = torch.tensor(X); Yt = torch.tensor(Y).unsqueeze(1)
    n = len(Xt)

    best_loss = float('inf')
    best_state = None

    for epoch in range(epochs):
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, 512):
            b = perm[i:i + 512]
            opt.zero_grad()
            # BCEWithLogitsLoss needs raw logits, so we call the linear layer directly
            logits = m.model(Xt[b])
            loss = crit(logits, Yt[b])
            loss.backward(); opt.step(); tot += loss.item()
        avg = tot / (n // 512 + 1)
        if avg < best_loss:
            best_loss = avg
            best_state = {k: v.clone() for k, v in m.model.state_dict().items()}
        if epoch % 10 == 0:
            print(f"  [{elapsed()}] epoch {epoch:3d}/{epochs}  loss {avg:.4f}  best {best_loss:.4f}")

    # restore best checkpoint found during training
    m.model.load_state_dict(best_state)
    print(f"[{elapsed()}] Restored best checkpoint (loss={best_loss:.4f})")
    return m

print(f"[{elapsed()}] Training (pass 1) — 120 epochs...")
model = run_training(lr=0.001, epochs=120)

out = os.path.join(MODELS_DIR, 'pulse_v2.onnx')
model.export_to_onnx(out, class_mapping="pulse")
print(f"[{elapsed()}] Exported -> {out}")

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
passes = 0
total = 0
for v in HELDOUT_VOICES:
    for speed in [0.9, 1.1]:
        buf, _, _ = place(synth(WAKE_WORD, v, speed))
        pk = stream_peak(buf)
        p = pk > 0.5
        print(f"  POS {v} x{speed}: {pk:.3f} {'PASS' if p else 'FAIL'}")
        passes += int(p); total += 1
for v in HELDOUT_VOICES:
    for w in ["impulse", "pause", "hello there"]:
        buf, _, _ = place(synth(w, v, 1.0))
        pk = stream_peak(buf)
        p = pk < 0.4
        print(f"  NEG '{w}' {v}: {pk:.3f} {'PASS' if p else 'FAIL'}")
        passes += int(p); total += 1
sil = stream_peak(np.zeros(CLIP_LEN, dtype=np.int16))
p = sil < 0.3
print(f"  NEG silence: {sil:.3f} {'PASS' if p else 'FAIL'}")
passes += int(p); total += 1

pass_rate = passes / total
print(f"\nRESULT: {passes}/{total} passed ({pass_rate*100:.0f}%)")

if pass_rate < 0.80:
    print("Pass rate below 80% — auto-retraining with lower learning rate...")
    model = run_training(lr=0.0003, epochs=200)
    # `oww` (still open from validating the FIRST export above) holds pulse_v2.onnx.data
    # memory-mapped — Windows refuses to let export_to_onnx overwrite it while that
    # handle is alive, which crashed here with OSError: [Errno 22] Invalid argument.
    # Release it before writing to the same path again.
    del oww
    import gc as _gc
    _gc.collect()
    model.export_to_onnx(out, class_mapping="pulse")
    print(f"Re-exported {out}")
    # re-validate
    oww = OWWModel(wakeword_models=[out], inference_framework='onnx')
    passes2 = 0
    for v in HELDOUT_VOICES:
        for speed in [0.9, 1.1]:
            buf, _, _ = place(synth(WAKE_WORD, v, speed))
            passes2 += int(stream_peak(buf) > 0.5)
    print(f"After retrain: {passes2}/{len(HELDOUT_VOICES)*2} POS passed")
else:
    print("Model looks good — ready to use.")
