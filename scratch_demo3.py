"""Fully voice-driven 3-min demo. After the first wake, every following command is
delivered as REAL AUDIO through the speakers into the real mic, exactly when the app
itself invites it ("What would you like me to do...") and enters listening — no idle
polling, no fixed pacing timers. Confirmations answered the same way ("Yes, go ahead").
"""
import asyncio, json, time, subprocess, gc, sys
import socket as _socket
_lock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", 7548))  # same port-lock trick as pulse.py: twin launches exit here
    _lock.listen(1)
except OSError:
    sys.exit(0)
import numpy as np
import soundfile as sf
import sounddevice as sd
import websockets

SR = 24000
FFMPEG = r"C:\Users\itzam\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"
OUT = r"C:\Users\itzam\AppData\Local\Temp\claude\C--Users-itzam-Desktop-pulse\252fd61b-0342-42dd-b783-bcead90f64c2\scratchpad"
VIDEO = f"{OUT}\\screen4.mp4"
AUDIO = f"{OUT}\\track4.wav"
FINAL = r"C:\Users\itzam\Desktop\pulse\pulse_demo.mp4"
DUR = 200

LINES = [
    "Open my desktop.",
    "Open my email folder.",
    "Close the file explorer.",
    "Open Notepad, write, this is the test for Pulse multistep tasks execution, and save it on the desktop as test dot T X T.",
    "Open Brave and search for NASA, and read all the options on the screen.",
]

print("Phase 1: rendering voice clips (young male voice)...")
from kokoro import KPipeline
pipe = KPipeline(lang_code='a')
def render(t, v='am_puck'):
    return np.concatenate([a for _, _, a in pipe(t, voice=v, speed=1.0)]).astype(np.float32)
intro = render("Welcome to Pulse. A local, offline, voice first assistant. Completely hands free.")
clips = [render(l) for l in LINES]
yes_clip = render("Yes, go ahead.")
outro = render("That's Pulse. Fully hands free, screen aware, and running entirely on this device.")
del pipe; gc.collect()
print(f"  {len(clips)} command clips ready, Kokoro unloaded")

def set_mic_level(scalar):
    """User keeps their mic muted to avoid disturbances — raise it just for this
    recording and restore their setting afterward."""
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
    mic = AudioUtilities.GetMicrophone()
    vol = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
    old = vol.GetMasterVolumeLevelScalar()
    vol.SetMasterVolumeLevelScalar(scalar, None)
    return old

async def demo():
    rec = subprocess.Popen([FFMPEG, "-y", "-f", "gdigrab", "-framerate", "12", "-i", "desktop",
                            "-t", str(DUR), "-c:v", "libx264", "-preset", "ultrafast",
                            "-pix_fmt", "yuv420p", VIDEO],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    sd.play(intro, SR); sd.wait()

    responses = [[] for _ in LINES]
    async with websockets.connect("ws://127.0.0.1:7550") as ws:
        idx = 0                 # which command clip is next
        pending = "command"     # what to play at the next listening state: command | yes | None
        awaiting = False        # spoke a command, transcript not yet seen
        retries = 0
        await ws.send(json.dumps({"v": 1, "type": "wake"}))  # first turn: wake via orb-equivalent
        t0 = time.time()
        while idx < len(LINES) and time.time() - t0 < DUR - 15:
            m = json.loads(await asyncio.wait_for(ws.recv(), 90))
            mt, payload = m.get("type"), m.get("payload")
            if mt == "state" and payload == "idle" and awaiting and retries < 3:
                # Command wasn't heard (went idle with no transcript) — retry it once.
                retries += 1
                idx -= 1
                pending = "command"
                awaiting = False
                print(f"  !! not heard, retrying command {idx+1}")
                await ws.send(json.dumps({"v": 1, "type": "wake"}))
                continue
            if mt == "transcript":
                awaiting = False
                print(f"  heard: {m.get('payload')!r}")
            if mt == "feedback":
                txt = m.get("text", "")
                print(f"  pulse: {txt[:80]}")
                if idx > 0:
                    responses[idx - 1].append(txt)
                low = txt.lower()
                if "should i continue" in low or ("continue" in low and "?" in txt):
                    pending = "yes"
                elif "what would you like me to do" in low:
                    pending = "command"
            if mt == "state" and payload == "listening" and pending:
                await asyncio.sleep(0.8)  # let the earcon finish before speaking
                if pending == "yes":
                    sd.play(yes_clip, SR); sd.wait()
                    pending = None
                else:
                    if idx < len(LINES):
                        print(f"--- speaking command {idx+1}: {LINES[idx][:50]}")
                        sd.play(clips[idx], SR); sd.wait()
                        idx += 1
                        pending = None
                        awaiting = True
                    else:
                        break
        # collect the final task's responses until it invites again, then end cleanly
        t1 = time.time()
        while time.time() - t1 < 60:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 60))
            except asyncio.TimeoutError:
                break
            if m.get("type") == "feedback":
                txt = m.get("text", "")
                print(f"  pulse: {txt[:80]}")
                responses[-1].append(txt)
                if "what would you like me to do" in txt.lower():
                    await ws.send(json.dumps({"v": 1, "type": "cancel"}))
                    break
    sd.play(outro, SR); sd.wait()
    rec.wait()
    return responses

old_mic = set_mic_level(0.85)
print(f"mic raised to 85% (was {round(old_mic*100)}%)")
try:
    responses = asyncio.run(demo())
finally:
    set_mic_level(old_mic)
    print(f"mic restored to {round(old_mic*100)}%")
print("Live run done.")

print("Phase 3: rebuilding audio track...")
from kokoro import KPipeline as KP2
pipe2 = KP2(lang_code='a')
track = [intro, np.zeros(int(SR*0.6), dtype=np.float32)]
for i, line in enumerate(LINES):
    track += [clips[i], np.zeros(int(SR*0.4), dtype=np.float32)]
    text = " ".join(responses[i]).strip()
    if text:
        track += [np.concatenate([a for _, _, a in pipe2(text, voice='af_heart', speed=1.0)]).astype(np.float32),
                  np.zeros(int(SR*0.7), dtype=np.float32)]
track += [outro]
del pipe2; gc.collect()
full = np.concatenate(track)
sf.write(AUDIO, full, SR)
print(f"audio {len(full)/SR:.1f}s; muxing...")
subprocess.run([FFMPEG, "-y", "-i", VIDEO, "-i", AUDIO,
                "-filter_complex", "[1:a]apad[a]", "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", FINAL], check=True)
print("DONE:", FINAL)
