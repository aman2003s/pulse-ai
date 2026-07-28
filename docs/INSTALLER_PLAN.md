# Windows Installer — Status & Plan

Honest status, not a "done" claim — see [Constraints](#whats-not-done-yet) below before assuming the installer is ready to ship.

## What's done

`ui/src-tauri/tauri.conf.json`'s bundler is now enabled and configured:

- `bundle.active: true`, target `nsis` (Windows-only, matching Pulse itself)
- `installMode: "currentUser"` — installs to the user's own profile, **no administrator privileges required**, matching "let people install it and try it" over a heavier enterprise-style install
- Publisher/copyright/description metadata set, so the installer and "Apps & Features" entry look intentional, not blank
- Uses the icon already present at `ui/src-tauri/icons/icon.ico`

Tauri's **default NSIS template already provides**, with no further config needed: a standard installation wizard, a Desktop shortcut, a Start Menu shortcut, a "launch after installation" option, and proper uninstall support (registered in Windows' Add/Remove Programs). This matches the installer requirements in the release plan for the *UI shell itself*.

## How to build and test it

Not yet run in this pass — do this before treating it as verified:

```bash
cargo install tauri-cli --version "^2"   # one-time; not yet installed in this environment
cd ui
cargo tauri build
```

This produces an NSIS installer under `ui/src-tauri/target/release/bundle/nsis/`. Install it on a clean-ish machine (or at least a fresh user profile) and confirm: desktop/Start Menu shortcuts appear, the app launches, and uninstall via Windows Settings actually removes it cleanly.

## What's NOT done yet

This is the important part. **The installer as configured only packages the Tauri UI overlay** (`ui/src-tauri` → `pulse-ui.exe`). It does **not** package or launch:

- The Python core (`pulse.py`) and its dependencies
- `llama-server.exe` and the local models

Today, `run.bat` is what starts the Python backend, waits for it to be healthy, *then* launches the UI — someone who installs only the NSIS-built UI installer and double-clicks the Start Menu shortcut would get a UI with nothing to connect to.

### Recommended next step (separate, focused PR)

1. **Freeze the Python backend** into a standalone `.exe` with [PyInstaller](https://pyinstaller.org/) — the real risk here is the heavier native dependencies (`sounddevice`, `onnxruntime`, `faster-whisper`, CUDA DLLs already in `models/`) packaging correctly; this needs its own testing pass, not a rushed one.
2. **Add it as a [Tauri sidecar](https://v2.tauri.app/develop/sidecar/)** — Tauri's mechanism for bundling and managing an external binary's lifecycle alongside the main app.
3. **Move `run.bat`'s startup sequence into Rust** (`ui/src-tauri/src/main.rs` currently does nothing but launch the window — see [`docs/ARCHITECTURE.md#startup-flow`](ARCHITECTURE.md#startup-flow) for the sequence it needs to replicate: spawn the backend sidecar, poll `/health`, then show the window).
4. **Keep the multi-gigabyte model files OUT of the installer itself** — bundling ~7GB of models into an NSIS installer would make it enormous and slow to download/update. Better: have first-run trigger the same download `scripts/fetch_models.py` already does (with a progress UI), so the installer itself stays small and updates don't re-download unchanged models.
5. Only then should [auto-startup](#auto-startup-not-yet-implemented) be wired up (below) — it depends on the Rust side already owning the full startup sequence.

## Auto-startup — not yet implemented

The release plan asks for optional "start Pulse when Windows starts," toggleable from settings. This needs [`tauri-plugin-autostart`](https://v2.tauri.app/plugin/autostart/) added to `ui/src-tauri/Cargo.toml` and registered in `main.rs`, plus a settings-UI toggle wired to it. Not done in this pass — it depends on the Rust side owning the full startup sequence (item 3 above) to be genuinely useful; toggling autostart for a UI-only exe that then has nothing to talk to isn't a real feature.

## Why this was scoped this way

This release's stated priority is letting people install, try, and report bugs — and per the release plan's own framing, harder fixes are meant to land "one by one... with proper release notes," not all at once. Shipping a config that's real and verifiable now, with an honest, concrete plan for the harder remaining piece, is more useful than claiming a fully-bundled one-click installer exists when it hasn't actually been built or tested.
