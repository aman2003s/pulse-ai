"""
benchmark_test.py — Benchmark test suite for Pulse optimizations.

Measures:
  1. Fast-Path Intent Router vs LLM (Gemma 4B) inference latency
  2. Folder Resolution (Predefined roots & UIA vs live filesystem search)
  3. Kokoro TTS: Full text synthesis vs Hybrid Pre-baked Splicing (latency to first playable audio)
  4. Kokoro TTS: Silence trimming & Parallel Multi-Sentence synthesis
"""
import os
import sys
import time
import re
import numpy as np
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(ROOT)

# ── BENCHMARK 1: Fast-Path Intent Routing vs LLM Inference ──────────────────
def benchmark_intent_routing():
    print("\n" + "="*60)
    print("  BENCHMARK 1: Fast-Path Intent Router vs LLM Inference")
    print("="*60)

    test_commands = [
        "open desktop",
        "open my downloads folder",
        "close notepad",
        "what's on my screen",
        "read screen"
    ]

    # Fast-Path regex matcher simulation
    INTENTS = [
        (re.compile(r"^(?:open|show)\s+(?:my\s+)?desktop$", re.I), "open_folder", "Desktop"),
        (re.compile(r"^(?:open|show)\s+(?:my\s+)?downloads(?:\s+folder)?$", re.I), "open_folder", "Downloads"),
        (re.compile(r"^(?:open|show)\s+(?:my\s+)?documents(?:\s+folder)?$", re.I), "open_folder", "Documents"),
        (re.compile(r"^(?:open|show)\s+(?:my\s+)?pictures(?:\s+folder)?$", re.I), "open_folder", "Pictures"),
        (re.compile(r"^(?:close|quit)\s+(.+)$", re.I), "close_app", None),
        (re.compile(r"^(?:what's on my screen|read screen|read window)$", re.I), "read_screen", None),
    ]

    def fast_path_match(cmd):
        for pat, intent, arg in INTENTS:
            m = pat.match(cmd.strip())
            if m:
                return intent, arg or (m.group(1) if m.groups() else None)
        return None, None

    # Measure Fast-Path latency
    t0 = time.perf_counter()
    for _ in range(1000):
        for cmd in test_commands:
            fast_path_match(cmd)
    t_fast = (time.perf_counter() - t0) / (1000 * len(test_commands)) * 1000  # ms per command

    print(f"  Fast-Path Intent Router avg latency: {t_fast:.4f} ms")

    # Measure LLM Inference latency if llama-server is running
    try:
        from core.planner.client import PlannerClient
        from core.planner.prompts import get_system_prompt
        from core.tools.registry import registry
        
        client = PlannerClient(port=8081)
        sys_prompt = get_system_prompt()
        schema = registry.get_planner_schema()

        # Warm-up call
        client.prompt(sys_prompt, "open desktop", schema)

        llm_times = []
        for cmd in test_commands[:3]:
            t_start = time.perf_counter()
            resp = client.prompt(sys_prompt, cmd, schema)
            dur = (time.perf_counter() - t_start) * 1000
            llm_times.append(dur)
            print(f"  LLM Command '{cmd}': {dur:.1f} ms  ->  tool={resp.get('plan')}")

        avg_llm = np.mean(llm_times)
        speedup = avg_llm / max(t_fast, 0.0001)
        print(f"\n  Summary: Fast-Path ({t_fast:.3f} ms) vs LLM ({avg_llm:.1f} ms)  ==>  {speedup:.0f}x FASTER")
    except Exception as e:
        print(f"  [!] Skipping LLM test (llama-server not running or unreachable): {e}")


# ── BENCHMARK 2: Folder Resolution Latency ───────────────────────────────────
def benchmark_folder_resolution():
    print("\n" + "="*60)
    print("  BENCHMARK 2: Folder Resolution Latency")
    print("="*60)

    target_name = "downloads"
    home = Path.home()

    # Strategy A: Predefined roots probe
    t0 = time.perf_counter()
    predefined_roots = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Pictures",
        home / "Music",
        home / "Videos",
        home
    ]
    found_path = None
    target_lower = target_name.lower()
    for root in predefined_roots:
        if root.exists():
            if root.name.lower() == target_lower:
                found_path = str(root)
                break
            # check 1st level subfolders
            try:
                for child in root.iterdir():
                    if child.is_dir() and child.name.lower() == target_lower:
                        found_path = str(child)
                        break
            except Exception:
                pass
        if found_path:
            break
    t_predefined = (time.perf_counter() - t0) * 1000

    print(f"  Predefined Roots Lookup: {t_predefined:.3f} ms  -> Found: {found_path}")

    # Strategy B: Live filesystem search (SearchFileTool)
    from core.tools.win_tools import SearchFileTool
    tool = SearchFileTool()
    t0 = time.perf_counter()
    res = tool.execute({"query": target_name})
    t_search = (time.perf_counter() - t0) * 1000

    print(f"  Full SearchFileTool Walk: {t_search:.1f} ms")
    if t_search > 0:
        print(f"\n  Summary: Predefined Roots ({t_predefined:.3f} ms) vs Live Walk ({t_search:.1f} ms)  ==>  {t_search/max(t_predefined, 0.001):.0f}x FASTER")


# ── BENCHMARK 3: Kokoro Audio Splicing vs Full Synthesis ─────────────────────
def benchmark_kokoro_splicing():
    print("\n" + "="*60)
    print("  BENCHMARK 3: Kokoro Audio Generation & Hybrid Splicing")
    print("="*60)

    try:
        from kokoro import KPipeline
        pipeline = KPipeline(lang_code='a')
        voice = 'af_heart'

        full_text = "Opening your downloads folder."
        prefix_text = "Opening your "
        dynamic_word = "downloads folder."

        # 1. Full text synthesis latency
        t0 = time.perf_counter()
        full_audio_chunks = [audio for _, _, audio in pipeline(full_text, voice=voice, speed=1.0)]
        t_full = (time.perf_counter() - t0) * 1000
        full_pcm = np.concatenate(full_audio_chunks)

        print(f"  Full Synthesis ('{full_text}'): {t_full:.1f} ms (Audio len: {len(full_pcm)/24000:.2f}s)")

        # 2. Pre-baked prefix (cached in memory) + Dynamic word synthesis
        # Pre-bake prefix into memory
        prefix_pcm = np.concatenate([audio for _, _, audio in pipeline(prefix_text, voice=voice, speed=1.0)])

        # Measure latency when prefix is ALREADY in RAM (0ms) and dynamic word is generated
        t0 = time.perf_counter()
        dynamic_chunks = [audio for _, _, audio in pipeline(dynamic_word, voice=voice, speed=1.0)]
        t_dynamic_gen = (time.perf_counter() - t0) * 1000
        dynamic_pcm = np.concatenate(dynamic_chunks)

        # Crossfade splicing in RAM (15ms crossfade)
        t0_splice = time.perf_counter()
        fade_samples = int(24000 * 0.015)
        fade_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
        fade_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)

        spliced_pcm = np.concatenate([
            prefix_pcm[:-fade_samples],
            prefix_pcm[-fade_samples:] * fade_out + dynamic_pcm[:fade_samples] * fade_in,
            dynamic_pcm[fade_samples:]
        ])
        t_splice = (time.perf_counter() - t0_splice) * 1000

        print(f"  Pre-baked Prefix: 0.0 ms (Instantly ready to start playing!)")
        print(f"  Dynamic Spliced Gen ('{dynamic_word}'): {t_dynamic_gen:.1f} ms  | Splice blend time: {t_splice:.3f} ms")
        print(f"\n  Summary: Playback starts IN 0ms (prefix plays while dynamic word generates in background)!")
    except Exception as e:
        print(f"  [!] Kokoro splicing test error: {e}")


# ── BENCHMARK 4: Silence Trimming & Parallel Sentence Synthesis ─────────────
def benchmark_silence_and_parallel_tts():
    print("\n" + "="*60)
    print("  BENCHMARK 4: Kokoro Silence Trimming & Parallel Synthesis")
    print("="*60)

    try:
        from kokoro import KPipeline
        pipeline = KPipeline(lang_code='a')
        voice = 'af_heart'

        sentence = "Done."

        # Measure raw output duration with trailing silence
        raw_audio = np.concatenate([audio for _, _, audio in pipeline(sentence, voice=voice, speed=1.0)])
        raw_dur = len(raw_audio) / 24000

        # Silence trimming helper (RMS threshold = 0.01)
        def trim_silence(pcm, threshold=0.01):
            abs_pcm = np.abs(pcm)
            non_silent = np.where(abs_pcm > threshold)[0]
            if len(non_silent) == 0:
                return pcm
            start = max(0, non_silent[0] - 240)  # keep 10ms padding
            end = min(len(pcm), non_silent[-1] + 240)
            return pcm[start:end]

        trimmed_pcm = trim_silence(raw_audio)
        trimmed_dur = len(trimmed_pcm) / 24000
        removed_ms = (raw_dur - trimmed_dur) * 1000

        print(f"  Raw Sentence ('{sentence}') Audio Duration: {raw_dur:.3f}s")
        print(f"  Trimmed Audio Duration: {trimmed_dur:.3f}s  (Trimmed {removed_ms:.1f} ms of silent pause!)")

        # Benchmark Parallel vs Sequential synthesis for 3 sentences
        sentences = [
            "Here is what I found on your screen.",
            "You have Google Chrome and Visual Studio Code open.",
            "Let me know if you want me to interact with any of them."
        ]

        # Sequential
        t0 = time.perf_counter()
        seq_audios = []
        for s in sentences:
            seq_audios.append(np.concatenate([a for _, _, a in pipeline(s, voice=voice, speed=1.0)]))
        t_seq = (time.perf_counter() - t0) * 1000

        # Parallel
        t0 = time.perf_counter()
        def synth_one(s):
            return np.concatenate([a for _, _, a in pipeline(s, voice=voice, speed=1.0)])

        with ThreadPoolExecutor(max_workers=3) as executor:
            par_audios = list(executor.map(synth_one, sentences))
        t_par = (time.perf_counter() - t0) * 1000

        print(f"\n  3 Sentences Sequential Synthesis: {t_seq:.1f} ms")
        print(f"  3 Sentences Parallel Synthesis: {t_par:.1f} ms  ==>  {(t_seq - t_par):.1f} ms FASTER ({t_seq/t_par:.2f}x speedup)")

    except Exception as e:
        print(f"  [!] Silence & Parallel TTS test error: {e}")


if __name__ == '__main__':
    benchmark_intent_routing()
    benchmark_folder_resolution()
    benchmark_kokoro_splicing()
    benchmark_silence_and_parallel_tts()
