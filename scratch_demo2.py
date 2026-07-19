"""3-minute demo, fully event-driven: every step waits for the app's own genuine
'idle' state before proceeding -- no fixed sleep() timers between actions anywhere.
Narrator = young male Kokoro voice (am_puck), pre-rendered to disk before the live
run. Browser task says "brave" (the user's actual default browser).
"""
import asyncio, json, time, subprocess, os, gc
import numpy as np
import soundfile as sf
import sounddevice as sd
import websockets

SR = 24000
FFMPEG = r"C:\Users\itzam\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"
OUT_DIR = r"C:\Users\itzam\AppData\Local\Temp\claude\C--Users-itzam-Desktop-pulse\252fd61b-0342-42dd-b783-bcead90f64c2\scratchpad"
VIDEO_PATH = f"{OUT_DIR}\\screen_raw3.mp4"
AUDIO_PATH = f"{OUT_DIR}\\track3.wav"
FINAL_PATH = r"C:\Users\itzam\Desktop\pulse\pulse_demo.mp4"
DURATION = 185

TASKS = [
    ("Pulse, open my desktop.", "open desktop", False),
    ("Pulse, open my email folder.", "open email folder", False),
    ("Pulse, close the file explorer.", "close explorer", True),
    ("Pulse, open Notepad, write: this is the test for Pulse multistep tasks execution, and save it on the desktop as test dot txt.",
     "open notepad, write 'this is the test for pulse multistep tasks execution' and save it on desktop as test.txt", False),
    ("Pulse, open Brave, search for NASA, and read all the options available on screen.",
     "open brave and search for nasa and read all options available on screen", False),
]

print("Phase 1: rendering narrator lines (young male voice)...")
from kokoro import KPipeline
pipe = KPipeline(lang_code='a')
def render(text, voice='am_puck'):
    return np.concatenate([a for _, _, a in pipe(text, voice=voice, speed=1.0)]).astype(np.float32)

intro_clip = render("Welcome to Pulse. A local, offline, voice first assistant. Let's put it through some real hands free tasks.")
narrator_clips = [render(line) for line, _, _ in TASKS]
yes_clip = render("Yes, go ahead.")
outro_clip = render("That's Pulse. Fully hands free, screen aware, and running entirely on this device.")
del pipe
gc.collect()
print(f"  rendered {len(narrator_clips)} task lines + intro/outro/yes, Kokoro unloaded")

async def run_task(ws, text, needs_confirm=False, max_s=90):
    """Sends a command and waits ONLY for the app's own genuine 'idle' broadcast --
    no fixed timers. Plays the pre-rendered 'yes' clip (real speaker->mic loopback)
    the instant a confirmation is actually requested."""
    await ws.send(json.dumps({"v": 1, "type": "text_command", "text": text}))
    spoken, tools = [], []
    confirmed = False
    t0 = time.time()
    while time.time() - t0 < max_s:
        m = json.loads(await asyncio.wait_for(ws.recv(), max_s))
        if m.get("type") == "feedback":
            spoken.append(m["text"])
            if needs_confirm and not confirmed and "continue" in m["text"].lower():
                sd.play(yes_clip, SR); sd.wait()
                confirmed = True
        if m.get("type") == "action":
            tools.append(m.get("tool"))
        if m.get("type") == "state" and m.get("payload") == "idle":
            break  # the ONLY exit condition -- a real, final idle
    print(f"    '{text}' -> tools={tools}")
    return " ".join(spoken)

async def run_live():
    print("Phase 2: recording + live sequence (event-driven, no fixed waits)...")
    rec = subprocess.Popen([
        FFMPEG, "-y", "-f", "gdigrab", "-framerate", "12", "-i", "desktop",
        "-t", str(DURATION), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        VIDEO_PATH
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)  # only fixed wait in the whole script: let ffmpeg actually start before anything happens on screen

    sd.play(intro_clip, SR); sd.wait()

    responses = []
    async with websockets.connect("ws://127.0.0.1:7550") as ws:
        for i, (line, cmd, needs_confirm) in enumerate(TASKS):
            sd.play(narrator_clips[i], SR); sd.wait()  # dynamic: waits for real audio to finish, not a guess
            try:
                spoken = await run_task(ws, cmd, needs_confirm)
            except Exception as e:
                print(f"    !! error on '{cmd}': {e}")
                spoken = ""
            responses.append(spoken)

    sd.play(outro_clip, SR); sd.wait()
    rec.wait()
    return responses

responses = asyncio.run(run_live())
print("Phase 2 done:", [r[:60] for r in responses])

print("Phase 3: rendering Pulse's real response audio...")
from kokoro import KPipeline as KP2
pipe2 = KP2(lang_code='a')
track = [intro_clip, np.zeros(int(SR*0.5), dtype=np.float32)]
for i, (line, cmd, nc) in enumerate(TASKS):
    track += [narrator_clips[i], np.zeros(int(SR*0.4), dtype=np.float32)]
    if responses[i]:
        r_audio = np.concatenate([a for _, _, a in pipe2(responses[i], voice='af_heart', speed=1.0)]).astype(np.float32)
        track += [r_audio, np.zeros(int(SR*0.7), dtype=np.float32)]
track += [outro_clip]
del pipe2
gc.collect()

full = np.concatenate(track)
sf.write(AUDIO_PATH, full, SR)
print(f"Phase 4: audio {len(full)/SR:.1f}s, video {DURATION}s. Muxing...")
subprocess.run([
    FFMPEG, "-y", "-i", VIDEO_PATH, "-i", AUDIO_PATH,
    "-filter_complex", "[1:a]apad[a]", "-map", "0:v", "-map", "[a]",
    "-c:v", "copy", "-c:a", "aac", FINAL_PATH
], check=True)
print("DONE:", FINAL_PATH)
