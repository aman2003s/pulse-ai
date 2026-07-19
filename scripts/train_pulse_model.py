import os
import torch
import numpy as np
import scipy.signal
from kokoro import KPipeline
from openwakeword.utils import AudioFeatures

import sys, types
sys.modules['openwakeword.data'] = types.ModuleType('openwakeword.data')
sys.modules['openwakeword.data'].generate_adversarial_texts = lambda *args, **kwargs: []
sys.modules['openwakeword.data'].augment_clips = lambda *args, **kwargs: []
sys.modules['openwakeword.data'].mmap_batch_generator = lambda *args, **kwargs: []
from openwakeword.train import Model

MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models'))
os.makedirs(MODELS_DIR, exist_ok=True)

print("Initializing Kokoro TTS...")
pipeline = KPipeline(lang_code='a')
voices = ['af_heart', 'af_alloy', 'am_michael', 'am_onyx']

positive_clips = []
negative_clips = []

def resample_and_convert(audio):
    # resample from 24000 to 16000
    audio_16k = scipy.signal.resample_poly(audio, up=2, down=3)
    return (audio_16k * 32767).astype(np.int16)

print("Generating synthetic positive clips (Pulse)...")
for i in range(5):
    for v in voices:
        generator = pipeline("pulse.", voice=v, speed=0.9 + i*0.05)
        for _, _, audio in generator:
            positive_clips.append(resample_and_convert(audio))

print("Generating synthetic negative clips...")
negative_words = ["false", "impulse", "pull", "plus", "jarvis", "computer", "hello", "the", "and", "is"]
for word in negative_words:
    for v in voices:
        generator = pipeline(word + ".", voice=v, speed=1.0)
        for _, _, audio in generator:
            negative_clips.append(resample_and_convert(audio))

def pad_clip(clip, length=32000): # 2.0s
    if len(clip) < length:
        return np.pad(clip, (0, length - len(clip)))
    return clip[:length]

positive_clips = [pad_clip(c) for c in positive_clips]
negative_clips = [pad_clip(c) for c in negative_clips]

print(f"Extracting features for {len(positive_clips)} pos and {len(negative_clips)} neg clips...")
F = AudioFeatures(device='cpu', ncpu=1)
pos_features = F.embed_clips(np.array(positive_clips), batch_size=16)
neg_features = F.embed_clips(np.array(negative_clips), batch_size=16)

def get_windows(features, label):
    X = []
    Y = []
    for f in features:
        # features shape is (frames, 96). We need (16, 96).
        if len(f) < 16:
            # pad frames
            f = np.pad(f, ((0, 16 - len(f)), (0, 0)))
        for i in range(0, f.shape[0] - 15, 2):
            X.append(f[i:i+16, :])
            Y.append(label)
    return np.array(X), np.array(Y)

pos_x, pos_y = get_windows(pos_features, 1)
neg_x, neg_y = get_windows(neg_features, 0)

if len(pos_x) == 0 or len(neg_x) == 0:
    print(f"pos_features shape: {pos_features.shape}")
    raise ValueError("No windows generated!")

X = np.concatenate([pos_x, neg_x])
Y = np.concatenate([pos_y, neg_y])

indices = np.arange(len(X))
np.random.shuffle(indices)
X = X[indices]
Y = Y[indices]

print(f"Training openWakeWord model on {len(X)} windows...")
model = Model(n_classes=1, input_shape=(16, 96), model_type="dnn", layer_dim=128, n_blocks=1)

optimizer = torch.optim.Adam(model.model.parameters(), lr=0.005)
criterion = torch.nn.BCELoss()

X_tensor = torch.tensor(X, dtype=torch.float32)
Y_tensor = torch.tensor(Y, dtype=torch.float32).unsqueeze(1)

for epoch in range(40):
    optimizer.zero_grad()
    preds = model.model(X_tensor)
    loss = criterion(preds, Y_tensor)
    loss.backward()
    optimizer.step()
    if epoch % 10 == 0:
        print(f"Epoch {epoch}, Loss: {loss.item():.4f}")

out_path = os.path.join(MODELS_DIR, 'pulse.onnx')
model.export_to_onnx(out_path, class_mapping="pulse")
print(f"Model exported to {out_path}")
