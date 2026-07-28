# Developer Setup

For running Pulse as a user, see [`docs/INSTALLATION.md`](INSTALLATION.md) instead — this is for working on Pulse's code.

## Prerequisites

Same as [Installation Requirements](INSTALLATION.md#requirements), plus:
- **Git**
- **Rust + Cargo**, only if you're working on the UI (`ui/src-tauri`) — see [UI development](#ui-development) below. Not needed for Core-only changes. The frontend itself (`ui/src`) is plain, unbundled HTML/JS/CSS with no Node.js build step.

## Environment setup

```bash
git clone https://github.com/aman2003s/pulse-ai.git
cd pulse-ai
python -m venv venv
venv\Scripts\activate
pip install -e .
python scripts/fetch_models.py
```

## Repository layout

```
core/               Core Engine — all the actual logic
  voice/            Wake word, capture, STT, TTS, and the main controller/loop
  planner/          Planner client (talks to llama-server) + system prompt
  tools/            Every tool Pulse can call (open_app, read_screen, save_file, ...)
  executor/         Runs a tool call with timeout + confirmation gating
  adapters/win/     Windows-specific: UI Automation focus, app/file indexing
  task_manager.py   Persists task state to SQLite
  conversation.py   Conversation/context tracking
  db.py             SQLite setup
  config.py         User config load/save
  api/ws_server.py  WebSocket server (Core <-> UI contract, docs/api.md)
docs/               Documentation (this file and its siblings)
scripts/            Setup, model fetching, dataset/training utilities
tests/              Standalone integration scripts (see Testing below)
ui/                 Tauri UI overlay (Rust + web)
  src/              Frontend (HTML/CSS/JS)
  src-tauri/        Rust shell, tauri.conf.json, installer bundling config
models/             Downloaded local models (gitignored, never committed)
pulse.py            Entry point — launches llama-server, then the Core
run.bat             Convenience launcher — starts Core, waits for it, launches UI
product_bible.md    Product philosophy and vision
tad.md              Original technical architecture document
FLOW_PLAN.md        Running decision log of real bugs found and fixed — worth
                     reading before touching the voice controller/planner loop
```

## Running Pulse during development

```bash
run.bat
```

or, to run just the Core without the UI (useful when iterating on backend logic):

```bash
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
venv\Scripts\python.exe pulse.py
```

You can then drive it via `text_command` over the WebSocket (`ws://127.0.0.1:7550`) without needing the UI or a mic — see [`docs/api.md`](api.md).

## Testing

There isn't a full automated test suite yet — see [`CONTRIBUTING.md`](../CONTRIBUTING.md#testing). Run the relevant script under `tests/` directly (`python tests/test_brain.py`), and for anything touching the planner loop or tool execution, run Pulse live against a real scenario — this project's history (`FLOW_PLAN.md`) has repeatedly found that assumptions about a fix, without a live run, turn out wrong.

## UI development

The UI is a [Tauri](https://tauri.app) app (`ui/src-tauri`) with a plain HTML/JS frontend (`ui/src`) — no framework, no bundler, no Node.js involved. It contains no business logic; it renders `state`/`transcript`/`feedback` events from the Core's WebSocket and sends `text_command`/`set_config` back. See [`docs/ARCHITECTURE.md#ui-architecture`](ARCHITECTURE.md#ui-architecture).

```bash
cargo install tauri-cli --version "^2"   # one-time
cd ui
cargo tauri dev
```

This launches the overlay pointed at `ui/src` directly (`frontendDist` in `tauri.conf.json`) and connects to whatever Core is already running on `ws://127.0.0.1:7550` — start the Core separately (see [Running Pulse during development](#running-pulse-during-development)) before launching the UI this way.

## Adding a new tool

Tools live in `core/tools/`. Each one is a small class: a name, description, JSON input schema, and an `execute()` method. Look at an existing simple tool (e.g. `DescribeScreenTool` in `core/tools/system_tools.py`) as a template, then register it in the same file's `registry.register(...)` calls near the bottom. The planner automatically gets access to any registered tool — no separate wiring needed.

## Code style

See [`CONTRIBUTING.md#code-style`](../CONTRIBUTING.md#code-style).
