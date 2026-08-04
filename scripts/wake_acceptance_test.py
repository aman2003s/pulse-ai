"""
Real acoustic acceptance test for the wake-word listener (FLOW_PLAN item 1.7).

Runs against the ACTUAL WakeListener/TTSService classes the app ships with --
not a reimplementation of either -- so a pass here means the real detection
path was exercised, the same "synthesize via Kokoro, play through real
speakers, let the real mic pick it up" acoustic-loop method already used
earlier this session for the false-accept spike test and the 4.8 live
acceptance attempt.

Usage:
  python scripts/wake_acceptance_test.py true_accept [--n 10]
  python scripts/wake_acceptance_test.py barge_in [--n 5]
  python scripts/wake_acceptance_test.py soak [--hours 4]

true_accept / barge_in are fully self-contained and finish in a couple of
minutes -- no human needs to be in the room. soak cannot be faked: it just
runs the real listener passively and logs every trigger with a timestamp,
because measuring false accepts requires real ambient conditions over real
wall-clock time. Ctrl+C stops it early and still reports whatever was
collected up to that point.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import sounddevice as sd

from core.voice.wake_listener import WakeListener
from core.voice.tts import TTSService

DEBOUNCE_CLEAR_S = 2.2  # just past WakeListener's own 2.0s trigger debounce


def _wait_for_trigger(timeout_s: float, flag: dict) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if flag["hit"]:
            return True
        time.sleep(0.05)
    return False


def run_true_accept(n: int, busy: bool):
    tts = TTSService()
    flag = {"hit": False}

    def on_trigger():
        flag["hit"] = True

    listener = WakeListener(callback=on_trigger, is_speaking_fn=(lambda: busy))
    listener.start()
    time.sleep(0.5)  # let the stream settle before the first trial

    label = "barge-in" if busy else "true-accept"
    hits = 0
    for i in range(1, n + 1):
        flag["hit"] = False
        audio = tts._synth_sentence("Pulse")
        if busy:
            # Overlay onto a longer dummy sentence so the mic hears "Pulse"
            # WHILE something else is genuinely playing -- this is what the
            # stricter 0.93 busy-state threshold actually needs to be tested
            # against, not the flag alone with silence underneath it.
            dummy = tts._synth_sentence(
                "This is a long unrelated sentence playing in the background "
                "to simulate Pulse already speaking when the wake word is said."
            )
            offset = int(1.0 * 24000)
            combined = np.zeros(max(len(dummy), offset + len(audio)), dtype=np.float32)
            combined[:len(dummy)] += dummy
            combined[offset:offset + len(audio)] += audio
            play_audio = np.clip(combined, -1.0, 1.0)
        else:
            play_audio = audio
        sd.play(play_audio, 24000)
        sd.wait()
        hit = _wait_for_trigger(timeout_s=1.5, flag=flag)
        hits += int(hit)
        print(f"[{label}] trial {i}/{n}: {'HIT' if hit else 'MISS'}")
        time.sleep(DEBOUNCE_CLEAR_S)

    listener.stop()
    print(f"\n{label} result: {hits}/{n}")
    return hits, n


def run_soak(hours: float):
    events = []

    def on_trigger():
        events.append(time.time())
        print(f"[soak] trigger #{len(events)} at {time.strftime('%H:%M:%S')}")

    listener = WakeListener(callback=on_trigger, is_speaking_fn=(lambda: False))
    listener.start()
    start = time.time()
    end = start + hours * 3600
    print(f"Soak running for {hours}h (Ctrl+C to stop early). Ambient conditions in "
          f"the room ARE the test -- there is no way to synthesize a real multi-hour soak.")
    try:
        while time.time() < end:
            time.sleep(5)
    except KeyboardInterrupt:
        print("Stopped early.")
    finally:
        listener.stop()
    elapsed_hours = (time.time() - start) / 3600
    rate_per_4h = (len(events) / elapsed_hours * 4) if elapsed_hours > 0 else float("nan")
    print(f"\nSoak result: {len(events)} trigger(s) over {elapsed_hours:.2f}h "
          f"-> {rate_per_4h:.2f} false accepts / 4h (target: <1)")
    return events, elapsed_hours


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["true_accept", "barge_in", "soak"])
    p.add_argument("--n", type=int, default=None)
    p.add_argument("--hours", type=float, default=4.0)
    args = p.parse_args()

    if args.mode == "true_accept":
        run_true_accept(n=args.n or 10, busy=False)
    elif args.mode == "barge_in":
        run_true_accept(n=args.n or 5, busy=True)
    else:
        run_soak(hours=args.hours)


if __name__ == "__main__":
    main()
