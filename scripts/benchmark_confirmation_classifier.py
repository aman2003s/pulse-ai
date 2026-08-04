"""
Benchmarks the FastText confirmation classifier (models/confirmation_classifier.ftz)
against the exact Gemma-based approach _classify_yes_no_or_other (controller.py)
currently uses -- same test phrases, same schema/prompt shape, real llama-server
round trip (not simulated), so the comparison is apples-to-apples.
"""
import sys
import time
import json
import httpx
import fasttext

TEST_PHRASES = [
    "sounds good", "that's right", "go for it, do that", "yeah do it",
    "nah, leave it", "don't bother", "not now", "cancel that",
    "open outlook", "search something and write it down", "what's the weather",
    "close notepad and open calculator instead",
]

SYSTEM_PROMPT = (
    "You classify a spoken reply to a yes/no confirmation question. Respond with "
    "exactly one of: yes, no, other. 'yes' = affirmative in any natural phrasing "
    "(\"sounds good\", \"that's right\", \"go for it\", \"do that\"). 'no' = negative "
    "in any natural phrasing (\"nah, leave it\", \"don't bother\", \"not now\"). "
    "'other' = the reply isn't actually answering the question at all — a genuinely "
    "different new instruction unrelated to what was asked."
)
SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["yes", "no", "other"]}},
    "required": ["intent"]
}
QUESTION = "This will close notepad. Should I continue?"


def gemma_classify(text, port=8081):
    prompt_str = f"<bos><start_of_turn>user\n{SYSTEM_PROMPT}\n\nUSER INPUT: QUESTION: {QUESTION}\nREPLY: {text}<end_of_turn>\n<start_of_turn>model\n"
    payload = {"prompt": prompt_str, "json_schema": SCHEMA, "n_predict": 2048,
               "temperature": 0.4, "cache_prompt": True}
    t0 = time.perf_counter()
    r = httpx.post(f"http://127.0.0.1:{port}/completion", json=payload, timeout=60)
    elapsed = time.perf_counter() - t0
    try:
        intent = json.loads(r.json()["content"]).get("intent", "?")
    except Exception:
        intent = "ERROR"
    return intent, elapsed


def fasttext_classify(model, text):
    t0 = time.perf_counter()
    predictions = model.f.predict(text + "\n", 1, 0.0, "strict")
    elapsed = time.perf_counter() - t0
    label = predictions[0][1].replace("__label__", "") if predictions else "?"
    return label, elapsed


def main():
    print("=== Loading FastText model (cold load timing) ===")
    t0 = time.perf_counter()
    model = fasttext.load_model("models/confirmation_classifier.ftz")
    load_time = time.perf_counter() - t0
    print(f"FastText model load: {load_time * 1000:.2f}ms\n")

    print("=== Per-phrase comparison ===")
    ft_times, gemma_times = [], []
    for phrase in TEST_PHRASES:
        ft_label, ft_t = fasttext_classify(model, phrase)
        gemma_label, gemma_t = gemma_classify(phrase)
        ft_times.append(ft_t)
        gemma_times.append(gemma_t)
        agree = "SAME" if ft_label == gemma_label else "DIFFER"
        print(f"{phrase!r:50s} fasttext={ft_label:6s}({ft_t*1000:6.2f}ms)  "
              f"gemma={gemma_label:6s}({gemma_t*1000:8.1f}ms)  [{agree}]")

    print("\n=== Summary ===")
    print(f"FastText: avg={sum(ft_times)/len(ft_times)*1000:.3f}ms  "
          f"max={max(ft_times)*1000:.3f}ms  min={min(ft_times)*1000:.3f}ms")
    print(f"Gemma:    avg={sum(gemma_times)/len(gemma_times)*1000:.1f}ms  "
          f"max={max(gemma_times)*1000:.1f}ms  min={min(gemma_times)*1000:.1f}ms")
    speedup = (sum(gemma_times)/len(gemma_times)) / (sum(ft_times)/len(ft_times))
    print(f"Speedup: {speedup:.0f}x faster per classification")
    print(f"FastText model load time (one-time, amortized): {load_time*1000:.2f}ms")

    import os
    size_kb = os.path.getsize("models/confirmation_classifier.ftz") / 1024
    print(f"FastText model size on disk: {size_kb:.1f} KB (vs. Gemma's ~5GB model already resident)")


if __name__ == "__main__":
    sys.exit(main())
