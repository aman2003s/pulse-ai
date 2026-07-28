# Windows Installer — Status & Plan

Honest status, not a "done" claim — see [What's still rough](#whats-still-rough) below before assuming this is a polished, finished experience.

## What's done and verified

The installer is real: it packages the full app — Tauri UI **and** the Python backend (voice, planning, task execution) — not just the UI shell. This was built, installed on a clean profile, and launched end-to-end with no manual setup: double-click the shortcut, the backend spawns itself, and on first run it fetches its own model weights automatically.

**How it fits together:**

- `ui/src-tauri/src/main.rs` — on launch, checks whether a Pulse core is already running (the same port-based lock `pulse.py`'s `ensure_single_instance()` uses). If not, it spawns the bundled `pulse-core.exe` with `PULSE_MODELS_DIR` pointed at the installed resource location, then shows the window as normal. Running from source is unaffected — this only activates in the packaged build.
- `pulse-core.spec` (project root) — the PyInstaller spec that freezes the Python backend (`pulse.py` and everything it imports) into a standalone `pulse-core.exe`, ~1.1GB unpacked. Rebuild it with:
  ```bash
  venv\Scripts\pyinstaller pulse-core.spec
  ```
- `core/paths.py` — a single `models_dir()` helper, now used everywhere the codebase locates `models/` (`pulse.py`, `capture.py`, `tts.py`, `wake_listener.py`, `controller.py`, `fetch_models.py`). Honors `PULSE_MODELS_DIR` when set; falls back to the original relative-to-source path when it isn't. This exists because a PyInstaller onedir build nests package files under an internal subfolder, so `__file__`-relative math doesn't reliably land next to a sibling `models/` folder the way it does running from source.
- `ui/src-tauri/tauri.conf.json`'s `bundle.resources` bundles the frozen `pulse-core/` folder plus the small, non-redownloadable assets (`pulse.onnx`/`pulse_v2.onnx` wake-word models, `assets/*.wav`, a couple of standalone prompt `.wav` files) — **not** `models/*.gguf`, not the llama.cpp engine binaries, not the Whisper cache. Those are multi-gigabyte and downloadable, so bundling them would make the installer itself huge for no benefit.
- `pulse.py`'s `start_llama()` now calls the same fetch logic `python scripts/fetch_models.py` already ran manually — if `llama-server.exe` or the Gemma weights aren't found next to a fresh install, it fetches them (Gemma GGUF ~5GB, mmproj ~950MB, the llama.cpp engine + DLLs) before starting the server. Running from source with `models/` already populated skips straight past this, unchanged.
- **GPU/CPU robustness**: `start_llama()` used to hardcode `-ngl 99` (force GPU offload) with no fallback — a machine with no GPU, an unsupported one, or broken drivers could fail to start at all. It now tries GPU first, then retries CPU-only on every port if that fails, so the same installer works on GPU and GPU-less machines instead of assuming the dev machine's hardware.
- The installer itself: **~320MB** (NSIS/LZMA-compressed from the ~1.1GB unpacked core), matching the goal of keeping the download small while deferring the actual model weights to first run.

**Build + test loop that produced this** (useful if you're touching packaging again):
```bash
venv\Scripts\pyinstaller pulse-core.spec        # rebuild the frozen backend
cd ui/src-tauri && cargo tauri build            # produces target/release/bundle/nsis/Pulse_<ver>_x64-setup.exe
```
Then actually install it (`Pulse_<ver>_x64-setup.exe /S` for silent) and launch `pulse-ui.exe` from the install directory — don't trust "the build succeeded" alone. Three real bugs only surfaced this way: missing phonemizer/`language_tags` data files, a missing `core/schema.sql`, and misaki's spaCy dependency (`en_core_web_sm`) not being bundled — each one built cleanly and only crashed at actual runtime.

## What's still rough

- **First run is slow and silent.** Fetching ~6GB of models has no progress UI yet — right now it's a log line (`pulse.log`) and a blank/disconnected overlay until it finishes. A real first-run experience needs a visible download-progress state, not just "wait an unspecified number of minutes."
- **Wake-word retraining is source-only.** The "train a custom wake word" flow shells out to `scripts/train_pulse_v2.py` via `sys.executable` — in a frozen build, `sys.executable` is `pulse-core.exe` itself, not a general Python interpreter, so this specific flow doesn't work from the packaged app yet. Everything else (voice interaction, planning, task execution) does.
- **Not yet auto-updating.** No update-check mechanism; a new version means re-downloading and re-running the installer.
- **Auto-startup not implemented.** Still needs [`tauri-plugin-autostart`](https://v2.tauri.app/plugin/autostart/) wired into `main.rs` plus a settings toggle — straightforward now that the Rust side owns the real startup sequence, just not done yet.
- **Only tested on one machine so far.** Verified end-to-end on the dev machine (GPU present); the CPU fallback path is implemented and reasoned through but hasn't been confirmed on genuinely GPU-less hardware.

## Publishing a release

This environment has no `gh` CLI or GitHub credentials configured, so cutting the actual GitHub Release has to happen from a machine that does:

1. Build it: `cd ui/src-tauri && cargo tauri build`
2. The installer lands at `ui/src-tauri/target/release/bundle/nsis/Pulse_<version>_x64-setup.exe`
3. Create a GitHub Release (tag it, e.g., `v0.1.0`) and upload that `.exe` as a release asset
4. Update the README's Installation section to link the release asset directly
