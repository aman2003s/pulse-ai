# Quick Start

Assumes you've already followed [`docs/INSTALLATION.md`](INSTALLATION.md) and Pulse is running (`run.bat`).

## Your first command

1. Say **"Pulse"** and wait — you'll hear or see a short acknowledgment (e.g. "I'm listening").
2. Say what you want, in plain language:

   > "Pulse, open Notepad and write 'how are you all?'"

3. Watch (or listen to) the overlay narrate each step: opening the app, reading the screen, typing the text.
4. Pulse tells you when it's done, or asks a question if it's missing something it needs.

## More things to try

| Say this | What happens |
|---|---|
| "Pulse, open my Downloads folder" | Opens File Explorer to that folder |
| "Pulse, find my resume" | Searches the local filesystem |
| "Pulse, open Word, write a short note, and save it as notes.docx" | A full multi-step task: open → type → save → verify it landed on disk |
| "Pulse, enable Superhero Mode" | Switches to continuous narration — see [Superhero Mode](../README.md#-superhero-mode) |
| "Pulse, close Chrome" | Closes an app by name |

## What to expect when something's ambiguous

If Pulse is missing a detail it genuinely needs (e.g. you asked it to save something but never said a filename), it'll ask once and wait for your answer — you don't need to repeat the wake word to reply. If there's a safe default (like saving to your Desktop with a sensible name), it'll tell you what it's defaulting to rather than blocking on the question forever.

## If something goes wrong

Pulse is built to notice when an action didn't produce the result it expected and try a different approach, rather than silently pretending it worked. If it gets genuinely stuck, it'll say so plainly (e.g. *"I'm stuck on part of that — say 'continue' and I'll try a different way"*) instead of looping forever. See [Known Limitations](../README.md#known-limitations) for the situations most likely to trip it up right now, and [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md) for common fixes.

## Text commands (no mic needed)

Pulse's WebSocket API accepts a `text_command` message as an alternative to voice — useful for testing, or if you'd rather type. See [`docs/api.md`](api.md) for the full event contract.
