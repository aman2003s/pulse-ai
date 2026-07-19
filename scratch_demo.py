"""3-minute Superhero Mode demo. Staggered model loading so this script's Kokoro
instance is never resident in memory at the same time as the live command sequence
runs (avoids memory pressure alongside the core's own loaded Gemma+Kokoro+Whisper) --
suspected cause of a prior unexplained core crash on only 4GB free RAM.
Phase 1: load Kokoro once, pre-render narrator lines to disk, then fully unload it.
Phase 2: record screen + run the live sequence, capturing ONLY Pulse's response TEXT.
Phase 3: load Kokoro again (now that phase 2's load is gone) to render Pulse's actual
response text into audio -- same model/voice Pulse itself uses, so it's genuinely
real Pulse audio, not a fake.
Phase 4: mux.
No internet-dependent task is used (WiFi disable needs admin rights this session
doesn't have) -- demonstrates the same offline capability either way.
"""
import asyncio, json, time, subprocess, os, sys, gc
import numpy as np
import soundfile as sf
import websockets

SR = 24000
FFMPEG = r"C:\Users\itzam\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"
OUT_DIR = r"C:\Users\itzam\AppData\Local\Temp\claude\C--Users-itzam-Desktop-pulse\252fd61b-0342-42dd-b783-bcead90f64c2\scratchpad"
VIDEO_PATH = f"{OUT_DIR}\\screen_raw.mp4"
AUDIO_PATH = f"{OUT_DIR}\\track.wav"
FINAL_PATH = r"C:\Users\itzam\Desktop\pulse\pulse_demo.mp4"
DURATION = 185

NARRATOR_LINES = [
    "Welcome to Pulse. A local, offline, voice first assistant built accessibility first, for blind and disabled users. Let's turn on Superhero Mode and see it in action.",
    "Pulse, open Notepad.",
    "Pulse, type Pulse end to end test into the document area.",
    "Pulse, what is on my screen right now?",
    "Pulse, repeat that.",
]
COMMANDS = [None, "open notepad", "type Pulse end to end test into the document area", "what is on my screen right now", "repeat that"]

# ---------- Phase 1: pre-render narrator lines, then fully unload Kokoro ----------
print("Phase 1: rendering narrator lines (male voice)...")
from kokoro import KPipeline
pipe = KPipeline(lang_code='a')
narrator_clips = []
for line in NARRATOR_LINES:
    audio = np.concatenate([a for _, _, a in pipe(line, voice='am_michael', speed=1.0)]).astype(np.float32)
    narrator_clips.append(audio)
del pipe
gc.collect()
print(f"  rendered {len(narrator_clips)} lines, Kokoro unloaded")

# ---------- Phase 2: record screen + run the live sequence ----------
async def send_and_capture(ws, text, max_s=45):
    if text is None:
        return ""
    await ws.send(json.dumps({"v": 1, "type": "text_command", "text": text}))
    spoken, tools = [], []
    t0 = time.time()
    while time.time() - t0 < max_s:
        m = json.loads(await asyncio.wait_for(ws.recv(), max_s))
        if m.get("type") == "feedback":
            spoken.append(m["text"])
        if m.get("type") == "action":
            tools.append(m.get("tool"))
        if m.get("type") == "state" and m.get("payload") in ("idle", "listening"):
            break
    print(f"    '{text}' -> tools={tools}")
    return " ".join(spoken)

async def run_live_sequence():
    print("Phase 2: recording screen + running live sequence...")
    rec = subprocess.Popen([
        FFMPEG, "-y", "-f", "gdigrab", "-framerate", "12", "-i", "desktop",
        "-t", str(DURATION), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        VIDEO_PATH
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)

    response_texts = []
    async with websockets.connect("ws://127.0.0.1:7550") as ws:
        # Turn on Superhero Mode first (this is the flagship experience we're showing)
        await ws.send(json.dumps({"v": 1, "type": "set_config", "key": "accessibility_mode", "value": "on"}))
        await asyncio.sleep(0.5)
        for i, (line, cmd) in enumerate(zip(NARRATOR_LINES, COMMANDS)):
            # Play the pre-rendered narrator line live (audible during recording)
            import sounddevice as sd
            sd.play(narrator_clips[i], SR)
            sd.wait()
            time.sleep(0.3)
            if cmd:
                spoken = await send_and_capture(ws, cmd)
                response_texts.append(spoken)
                time.sleep(1.5)
            else:
                response_texts.append("")

    remaining = DURATION - 3
    print(f"Waiting for {remaining:.0f}s recording to finish...")
    rec.wait()
    return response_texts

response_texts = asyncio.run(run_live_sequence())
print("Phase 2 done. Responses captured:", [t[:60] for t in response_texts])

# ---------- Phase 3: render Pulse's actual response text into audio (same model/voice) ----------
print("Phase 3: rendering Pulse's real response audio...")
from kokoro import KPipeline as KPipeline2
pipe2 = KPipeline2(lang_code='a')

track = []
def add_clip(audio, pad=0.4):
    track.append(audio)
    track.append(np.zeros(int(SR * pad), dtype=np.float32))

for i, line in enumerate(NARRATOR_LINES):
    add_clip(narrator_clips[i], 0.5)
    if response_texts[i]:
        r_audio = np.concatenate([a for _, _, a in pipe2(response_texts[i], voice='af_heart', speed=1.0)]).astype(np.float32)
        add_clip(r_audio, 0.8)

del pipe2
gc.collect()

# ---------- Phase 4: mux ----------
full = np.concatenate(track)
sf.write(AUDIO_PATH, full, SR)
print(f"Phase 4: audio track {len(full)/SR:.1f}s, video {DURATION}s. Muxing...")

subprocess.run([
    FFMPEG, "-y", "-i", VIDEO_PATH, "-i", AUDIO_PATH,
    "-filter_complex", "[1:a]apad[a]", "-map", "0:v", "-map", "[a]",
    "-c:v", "copy", "-c:a", "aac", FINAL_PATH
], check=True)
print("DONE:", FINAL_PATH)
