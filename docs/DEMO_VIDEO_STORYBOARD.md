# Demo Video Storyboard & Shot List

A 20–30 second LinkedIn launch video for Pulse going open source. Goal: feel like a polished product launch, not a screen recording with text slapped on it.

## Technical specs (LinkedIn, 2026)

- **Aspect ratio: 16:9, 1920×1080**, native resolution — this is a desktop app demo, so showing the real UI at readable scale matters more than a square feed crop. If you have time for a second export, a 1:1 (1080×1080) crop of the same footage is worth posting too — it's the safest-performing format across LinkedIn's desktop and mobile feed.
- **MP4, H.264, 60fps or lower, under 200MB.**
- **Burn in captions (open captions), not just an uploaded transcript.** ~85% of LinkedIn video is watched muted — if the words aren't visible on-screen from frame one, most viewers never hear the voice command at all.
- Length: **20–30 seconds total.** Don't pad it to hit 30 — a tight 22-second cut beats a dragging 30-second one.

## Before recording — environment prep

This matters as much as the edit. Nothing on screen should distract from Pulse.

- Clean desktop: no personal files, no unrelated icons, generic/neutral wallpaper (or none — just the demo app, maximized or centered with dark chrome around it).
- Close every other app, browser tab, and notification source. Turn on Windows Focus Assist so nothing toasts mid-recording.
- Pick **one** target app for the demo and keep it visually simple — Notepad (as in the user's example command) is genuinely a good choice: instantly recognizable, zero clutter, the text appearing is the whole story.
- Use a clean, empty, or intentionally-styled cursor — the default Windows arrow is fine; avoid custom flashy cursor themes, they read as amateur.
- Record at native resolution, uncompressed if possible (compress during export, not capture) — OBS Studio (free) with a lossless or high-bitrate preset is a solid choice.

## Shot list

| Time | Shot | Visual | Audio / Caption |
|---|---|---|---|
| 0:00–0:03 | **Title card** | Clean, centered text on a plain dark background (not a screen recording) — Pulse's logo/wordmark if available, otherwise typography only. Subtle fade or scale-in, not a hard cut. | Caption: **"Built in 48 hours. Now becoming open source."** No voice needed here — let it read as a beat, not narration. |
| 0:03–0:08 | **Launch Pulse** | Cut to desktop. Show Pulse's overlay appearing — a quick, deliberate reveal (e.g. the overlay animating in), not just "it's already there." Hold 1–2 sec on the clean idle interface so viewers register what it looks like before anything happens. | Optional light UI sound (the app's own wake chime, if it has one) — no music yet, or music starts very quiet/low here and swells slightly once the task starts. |
| 0:08–0:20 | **The command** | Cursor-free — hands-off. Show the overlay's listening state, then the live transcript appearing as the user speaks: *"Pulse, open Notepad and write 'how are you all?'"* Cut to Notepad opening, text appearing (typing animation, not instant paste — the fact that it's *actually typing* is the proof). If a second command is stable enough to include, cut to it here (e.g. "and save it as notes.txt") — only add it if it's rock solid; one clean command beats two shaky ones. | Real captured audio of the voice command as burned-in captions, synced to when each word is actually heard/shown. Keep any background music low enough that the command is legible even with sound off (captions carry it either way). |
| 0:20–0:25 | **Closing card** | Cut back to a clean title-card style screen (not a busy screenshot) with the feature bullets appearing one at a time, fast (staggered, ~0.15s apart, not simultaneous) rather than all at once: | Caption/on-screen text: <br>⭐ Open Source <br>🖥️ Runs locally <br>🔒 Privacy-first <br>♿ Built with accessibility in mind <br><br>**Contributors welcome** |
| 0:25–0:28 | **End card / CTA** | Repo link and/or landing page URL, clean typography, static or very subtle motion (slow zoom, no spin/flashy transitions). | Caption: GitHub URL + landing page URL, large enough to read paused on a phone screen. |

## Camera movement & pacing

- **No camera movement in the literal sense (it's screen capture), but simulate intentionality**: use slow, subtle zooms (Ken Burns-style, 2–5% scale over a few seconds) on static moments like the title card and closing card, rather than a completely static frame — this is what separates "edited" from "recorded."
- **Cut on action**, not on a timer — e.g. cut to Notepad right as the spoken command finishes, not a beat later. Dead air (even 0.3s) between the command and the result reads as sluggish.
- **Every cut should be a hard cut or a fast (≤0.2s) crossfade** — no slide transitions, no spins, no wipes. Those read as template/stock, not premium.
- Overall pacing should accelerate slightly toward the middle (the command executing) and settle for the closing card — don't let the closing bullets linger long enough to feel like a slide deck.

## Captions

- **Burn in every spoken word**, synced tightly (word-level or short-phrase-level, not one giant caption block sitting on screen for 8 seconds).
- Style: clean sans-serif, high contrast (white text, subtle dark backing box or drop shadow) so it reads over any background.
- Keep caption text physically separate from the on-screen UI — don't let captions overlap the Pulse overlay or the Notepad window; use the letterboxed top/bottom margin if the recording doesn't fill the full frame.

## Editing checklist before publishing

- [ ] Nothing personal or unrelated visible in any frame (re-check every second, not just the obvious shots)
- [ ] Captions burned in and legible with sound off
- [ ] No dead air longer than ~0.3s between cuts
- [ ] Under 200MB, H.264, 1920×1080
- [ ] Watched once fully muted — does it still tell the whole story?
- [ ] Watched once at 2x speed — does the pacing still feel intentional, or does it drag?
