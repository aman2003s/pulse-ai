# Pulse — Technical Architecture Document (TAD) v1.0

## 1. System Overview
Build a local AI computer interaction platform: users speak natural language, Pulse converts intent into safe, observable computer actions.
Core principle: AI decides *what* needs to happen. Software decides *how* it happens.

## 2. High-Level Architecture
User → Voice Interaction Layer (Speech Recognition / Text Input) → Conversation Manager → Intent Understanding → AI Planner → Task Manager → Execution Controller → (Skill System + Safety Layer) → Tool Engine → Platform Adapters → OS.

## 3. Voice Interaction Layer
- **Wake Word Engine** — mic stream in, activation event out. Local, low CPU, always-on.
- **Speech-to-Text** — voice → text (e.g. Whisper variants or other local STT). Output: `{"text": "..."}`.
- **Text-to-Speech** — response → voice. Fast, natural, interruptible. Chosen model: **Kokoro-82M** (Apache 2.0, ~327MB, CPU-capable, 24kHz output, 54 voices/8 languages, no cloud needed) — fits Local First principle.

## 4. Conversation Manager
Maintains interaction state: current conversation, user preferences, context, interruptions. Handles multi-turn clarification (e.g. "Find my bill" → "Which bill?" → "Electricity").

## 5. AI Planner
Converts intent into next executable action. Does NOT execute actions itself.
Input: `{"request": "Open my April electricity bill"}`
Output: `{"goal": "...", "next_action": "search_file", "parameters": {"query": "April electricity bill"}}`

## 6. Task Manager
Backbone of long-running tasks. Task object: id, goal, status, current_step, history, result.
Responsibilities: create, save progress, resume, handle failures, maintain state. Storage: local DB (SQLite).

## 7. Execution Controller
Flow: Receive Action → Validate → Request Tool → Execute → Return Result. One action at a time.

## 8. Skill Architecture
Skills = logical capability groups (file/, system/, terminal/), each with tools + prompts + validators.
Skill definition: `{"name", "tools": [...], "description"}`.

## 9. Tool System
Tools are the actual actions (e.g. open_file). Each tool defines: name, description, input schema, output schema, permissions, platform support.

## 10. Platform Adapter Layer
Keeps Pulse platform-independent: Tool → Adapter Interface → Windows/Linux/Android implementation.
E.g. universal `open_application("Chrome")` → Windows: subprocess/start API; Linux: desktop entry; Android: Intent API.

## 11. Observer System
After every action, checks: did it succeed, did state change, did an error occur.

## 12. Safety Layer
Checks dangerous commands, delete operations, permission requests, unknown actions. E.g. confirms before permanently deleting files.

## 13. Memory System
Operational memory only (not full conversational memory) — e.g. user prefers detailed feedback, common apps, default folders. Stored locally.

## 14. MVP Architecture
Wake Word → Gemma 4 E4B (audio in, function-call out) → Task Manager → File/App Tools → Windows Adapter → Kokoro-82M TTS.
No vision, browser, or complex automation in MVP.

## 15. UI Layer — Core/UI Separation
**Principle:** UI is a thin, disposable presentation layer. All logic (voice, planning, tasks, tools, adapters) lives in the Core Engine, which runs headless as a local background service exposing a local API (IPC/WebSocket/gRPC — TBD in SDD).

- **Core Engine** — platform-independent, runs with or without any UI open. Owns Voice Layer, Planner, Task Manager, Executor, Skills, Tools, Adapters.
- **UI Layer** — a translucent, minimal overlay (single design, works identically on Windows/Linux/Android later) that connects to the Core Engine over the local API. Shows: listening state, live transcript, current action, feedback text. Contains zero business logic — pure rendering + input forwarding.
- **Why:** decouples UI framework choice from core (can reskin/rebuild UI per platform without touching core logic); lets Core Engine run standalone (e.g. headless/background) if user wants voice-only, no screen.
- Cross-platform UI toolkit choice (e.g. Tauri/Flutter) deferred to SDD — constraint is just: one core API, thin UI, no logic leakage into UI.

## 16. Future Expansion
Browser automation, IDE control, document intelligence, accessibility vision, smart home, wearables — without changing the core.

**Final Architecture Principle:** Pulse is not an AI that controls a computer. Pulse is a local operating layer that translates human intent into safe computer actions.
