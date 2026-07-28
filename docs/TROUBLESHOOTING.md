# Troubleshooting

## Where to look first

`%APPDATA%\Pulse\pulse.log` has the full record of what Pulse's planner reasoned, what tools it called, and what actually happened. Most "why did it do that" questions are answered here before anywhere else — and it's the single most useful thing to attach to a bug report.

## Setup issues

### `python scripts/fetch_models.py` fails or hangs

- It downloads several gigabytes — a slow or interrupted connection is the most common cause. Re-running the script is safe; it skips files that already downloaded successfully.
- If a specific download consistently fails, check that the URL in `scripts/fetch_models.py` still resolves (Hugging Face occasionally reorganizes model repos) and [open an issue](https://github.com/aman2003s/pulse-ai/issues/new/choose) if so.

### `llama-server not found` / Pulse won't start

- Confirm `models/llama-server.exe` exists — it's fetched by `scripts/fetch_models.py`, step 3. If missing, re-run that script.
- If `llama-server.exe` exists but crashes immediately, check `pulse.log` for the actual error — a common cause is a corrupted/partial model download; delete `models/gemma-4-E4B-it-Q4_K_M.gguf` and re-run `fetch_models.py`.

### Pulse is very slow to respond

- Confirm a GPU is actually being used: `llama-server` is launched with `-ngl 99` (offload everything to GPU). If you don't have a CUDA or Vulkan-capable GPU, Pulse falls back to CPU inference, which is meaningfully slower. This is expected, not a bug — see [Requirements](INSTALLATION.md#requirements).
- The very first response after starting is slower than the rest — the model is loading and the prompt cache is cold. Subsequent responses in the same session are faster.

### "Address already in use" / port conflicts

- Pulse's backend listens on `127.0.0.1:7550` (WebSocket) and tries `8081` → `8082` → `8083` for the local model server. If another process is already using these, either close it or wait — Pulse's `run.bat` checks whether a backend is already running on `7550` and reuses it instead of starting a second one.

## Runtime issues

### Pulse says it "couldn't detect the focused window" / "screen may be locked"

This is Pulse correctly detecting that Windows is withholding foreground-window access — usually because the workstation is genuinely locked. This is a real OS security boundary, not something retrying fixes. Unlock the screen and try again.

### Pulse keeps re-reading the screen without acting

Usually means the current screen state is genuinely ambiguous from Pulse's point of view — check `pulse.log` for what `read_screen` actually returned at that point. If it looks like Pulse *should* have been able to act on what's shown, that's a good, concrete bug report — see [`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Pulse says "I'm stuck on part of that"

This means Pulse's own loop/no-progress detection fired — it tried the same thing repeatedly (or several different things) without making real progress, and stopped rather than looping forever. Say "continue" to have it try a different approach, or check what's actually on screen yourself — see [Known Limitations](KNOWN_LIMITATIONS.md) for the app behaviors most likely to cause this.

### It typed into / affected the wrong document

Please report this — see [Known Limitations → Application compatibility](KNOWN_LIMITATIONS.md#application-compatibility) for the known session-resume case, and include the exact goal phrasing you used plus the relevant `pulse.log` section, since the detection here is heuristic and improving it needs real examples of phrasing it missed.

### Microphone isn't picking up "Pulse"

- Check Windows microphone privacy settings (Settings → Privacy → Microphone) allow desktop apps to access it.
- Confirm the correct input device is selected — Pulse's settings include a microphone device selector.
- As a workaround while diagnosing, Pulse's WebSocket API accepts text commands without a mic — see [`docs/api.md`](api.md).

## Still stuck?

[Open an issue](https://github.com/aman2003s/pulse-ai/issues/new/choose) with the bug report template — include your Windows version, the exact command you gave Pulse, and the relevant `pulse.log` excerpt.
