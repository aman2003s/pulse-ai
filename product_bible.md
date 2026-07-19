# Pulse — Product Bible v1.0

## 1. Product Identity
**Name:** Pulse
**Tagline:** Your local AI computer companion.

**Core Vision:** Pulse is a privacy-first, local AI assistant that allows people to control and understand their computer naturally through voice conversation. Primary focus is accessibility (visually impaired, mobility-limited, users who struggle with traditional computer interaction). General users benefit too, but accessibility drives design.

## 2. The Problem
Modern computers require understanding menus, buttons, file structures, commands, shortcuts, workflows. For many users, especially disabled users, the interface itself is the barrier. Pulse lets users express intent instead of commands.

## 3. Product Philosophy
- **Local First** — AI runs locally, data stays on device, no mandatory cloud.
- **Voice First** — voice primary, keyboard/chat secondary.
- **Human Control** — Pulse assists, doesn't silently take control.
- **Explainable Actions** — user always knows: what Pulse understood, what it's doing, what happened.
- **Goal Based Interaction** — "Find my April electricity bill," not "search folder X for file Y."

## 4. Core UX
Wake word: "Pulse" → varied natural responses ("I'm here.", "Ready when you are.", "I'm listening.").

## 5. Voice System
Flow: Mic → Wake Word Detection → Speech Recognition → Intent Understanding → Task Planning → Execution → Voice Feedback.
Requirements: local processing, interruptible, natural conversation, adjustable feedback level.

## 6. Feedback System
- Started: "I'll help you with that."
- Progress: "Searching your files." / "Opening the application."
- Completion: "Done. The file is open."
- Failure: "I couldn't complete that. I need your input."

## 7. Feedback Modes
Minimal / Standard (default) / Guided (accessibility detail).

## 8. Core Architecture
User → Voice Layer → Planner → Task Manager → Executor → Tool Engine → Platform Adapter → Computer.

## 9. Main Components
- **Planner** — intent understanding, skill selection, next-action decision. Local LLM.
- **Task Manager** — persistent memory of goal/steps/state/progress/errors/results; pause/resume/recovery.
- **Executor** — runs one action, reports result. AI never directly controls the computer.
- **Observer** — reports success/failure/changed state.

## 10. Tool System
Everything is a tool (open_app, open_file, search_file, create_folder, run_command, read_screen). Each tool: name, description, inputs, permissions, output format, platform support.

## 11. Skill System
Skills group tools: Computer, File, Terminal, Accessibility. Future: Browser, Documents, Developer, Email.

## 12. Multi-Agent Strategy
Not required initially — one model + multiple skills, each with own tools/instructions/validation. Specialized agents later if needed.

## 13. Development Roadmap
- **Phase 1** — Voice + basic actions (wake word, STT, TTS, open app/file/folder).
- **Phase 2** — Tool Framework (Tool SDK, Executor, Observer, Permissions).
- **Phase 3** — Planning (multi-step tasks, task state, recovery).
- **Phase 4** — Accessibility Intelligence (screen understanding, UI assistance, guidance mode).
- **Phase 5** — Expansion (plugins, more platforms, dev tools, advanced automation).

## 14. What Pulse Will Never Become
Not cloud-only, not a simple chatbot, not a command launcher, not a silent autonomous agent, not a replacement for user decisions.

## 15. UI Philosophy
The interface is a thin, translucent, voice-first overlay — not the product. All intelligence lives in a core engine that runs the same on every device; the UI is a swappable skin with no logic of its own, so the same experience feels consistent whether on desktop, mobile, or headless voice-only use.

## 16. Success Definition
User says "Pulse, help me" and accomplishes tasks they previously struggled with.
