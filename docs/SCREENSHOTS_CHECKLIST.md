# Screenshots & GIF Checklist

Exactly what's needed to fill in the [README's Screenshots section](../README.md#screenshots) and give the docs real visuals instead of descriptions. If you'd like to help and don't want to write code, this is a great way to contribute.

## Ground rules for every capture (matches the [demo video](DEMO_VIDEO_STORYBOARD.md) standard — this is a public, professional release)

- **Clean desktop.** No personal files, no unrelated desktop icons, no browser tabs with personal accounts logged in, no unrelated notification toasts.
- **A neutral, uncluttered background app** where Pulse is shown acting on something (Notepad with placeholder text is a safe default; avoid anything with real personal content).
- **Consistent OS theme** across all captures (pick either Windows light or dark mode and use it for every screenshot in the set — mixing looks unpolished).
- **Native resolution, no upscaling.** 1920×1080 or higher.
- PNG for static screenshots, GIF or short MP4 (then convert to GIF) for the animated ones — keep GIFs under ~5MB by trimming and reducing frame rate if needed.

## Required captures

| # | What | State to capture | Used in |
|---|---|---|---|
| 1 | **Idle overlay** | The Pulse overlay sitting idle, before the wake word | README hero, `docs/INTERFACE.md` (if added later) |
| 2 | **Listening state** | Overlay right after saying "Pulse," actively listening | README, QUICKSTART.md |
| 3 | **Thinking state** | Overlay while the planner is reasoning about a request | README |
| 4 | **Acting state + live transcript** | Overlay mid-task, showing what it's currently doing | README |
| 5 | **Speaking / feedback state** | Overlay narrating a completed step | README |
| 6 | **Settings popup** | The settings panel open (feedback mode, mic device, etc.) | INSTALLATION.md or a future SETTINGS.md |
| 7 | **Superhero Mode example** | Overlay narrating continuously, mid-task | README's Superhero Mode section |
| 8 | **Full task GIF** | Animated: say "Pulse, open Notepad and write 'how are you all?'" through completion — the same command used in the [demo video](DEMO_VIDEO_STORYBOARD.md) | README hero |
| 9 | **Disconnected/error state** | What the overlay shows if the backend isn't reachable | TROUBLESHOOTING.md |
| 10 | **A multi-step task in progress** | Something like the Word open→type→save flow, mid-step | ARCHITECTURE.md's command processing flow section |

## Nice-to-have (not blocking)

- Collapsible controls panel open vs. collapsed (before/after)
- Light mode vs. dark mode side-by-side, if the overlay supports both
- Accessibility-focused capture: Superhero Mode narrating a folder-contents summary (matches the README's example interaction)

## How to capture cleanly

- Windows' built-in **Snipping Tool** (`Win+Shift+S`) for static screenshots.
- **ScreenToGif** (free, open source) or Windows' built-in **Xbox Game Bar** (`Win+G`) for the animated captures, then trim tightly around just the relevant moment.
- For the overlay itself: since it's a transparent, always-on-top window, make sure the capture tool isn't clipping the transparency oddly against your desktop background — a plain, uncluttered wallpaper avoids this.

## Submitting

Add captures under `docs/assets/screenshots/` (create the folder if it doesn't exist) with descriptive filenames (`idle-state.png`, `full-task-demo.gif`), then open a PR replacing the relevant placeholder/description with the real image — see [`CONTRIBUTING.md`](../CONTRIBUTING.md).
