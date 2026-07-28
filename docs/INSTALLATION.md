# Installation Guide

There's no packaged installer yet (see the [Roadmap](../README.md#roadmap) and [`docs/BRANCH_PROTECTION.md`](BRANCH_PROTECTION.md) for what's coming) — this walks through running Pulse from source on Windows.

## Requirements

- **Windows 10 or 11.** Pulse automates the desktop via Windows UI Automation — it doesn't run on macOS/Linux.
- **Python 3.11+**
- **~7GB free disk space** for local models (the planner/vision model is the bulk of it — see [Models](#models) below).
- **A GPU is strongly recommended, not strictly required.** `llama-server` is launched with `-ngl 99` (offload all model layers to GPU) for responsiveness — an NVIDIA GPU (CUDA) or a Vulkan-capable GPU (AMD/Intel/NVIDIA) both work; the bundled `llama-server.exe` ships with both backends. Without a supported GPU it'll still run on CPU, just noticeably slower to respond.
- **A microphone**, for voice interaction (Pulse can also be driven via text commands over its WebSocket API without one — see [`docs/api.md`](api.md)).

## 1. Clone the repository

```bash
git clone https://github.com/aman2003s/pulse-ai.git
cd pulse-ai
```

## 2. Set up the Python environment

```bash
python -m venv venv
venv\Scripts\activate
pip install -e .
```

This installs the dependencies listed in `pyproject.toml` (speech recognition, TTS, wake word, and core web/data libraries).

## 3. Fetch local models

```bash
python scripts/fetch_models.py
```

This downloads, in order:

1. **Gemma 4 E4B** (~5GB, quantized) — the local model used for both task planning and on-screen visual understanding.
2. **The vision projector** (`mmproj`, ~950MB) — needed for Pulse's `look_at_screen` capability (used as a fallback when the accessibility tree alone is ambiguous). Pulse runs fine without it — those specific tools just won't work.
3. **`llama-server.exe`** — the latest Windows build of [llama.cpp](https://github.com/ggml-org/llama.cpp), fetched automatically from its GitHub releases.
4. **Whisper (speech-to-text), Kokoro (text-to-speech), Silero (voice activity detection), and openWakeWord (wake word) models** — smaller, fetched via their own Python packages.

This step downloads several gigabytes — expect it to take a while depending on your connection. If it's interrupted, re-running the script picks up only what's missing (already-downloaded files are skipped).

## 4. Run Pulse

```bash
run.bat
```

This starts the Python core (`pulse.py`), waits for the local model server to report healthy, then launches the UI overlay (`ui\pulse-ui.exe`). The first launch is slower (model loading); subsequent launches reuse the already-running backend if you run `run.bat` again while it's still up.

You should see a small overlay window appear. Say **"Pulse"** to wake it — see [`docs/QUICKSTART.md`](QUICKSTART.md) for what to try next.

## Troubleshooting

If something doesn't work, check [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) before filing an issue — several common setup problems (GPU not detected, port conflicts, mic permissions) are already documented there.

## Models

For reference, here's what lives in `models/` after setup (this folder is gitignored — never committed):

| File | Approx. size | Purpose |
|---|---|---|
| `gemma-4-E4B-it-Q4_K_M.gguf` | ~5GB | Planning + vision (text and image understanding) |
| `mmproj.gguf` | ~950MB | Vision projector for the above |
| `llama-server.exe` + DLLs | ~1.5GB | Local inference server (llama.cpp) |
| Whisper / Kokoro / Silero / openWakeWord | a few hundred MB total | STT, TTS, VAD, wake word |

Everything runs locally — see the [Privacy](../README.md#privacy) section of the README.
