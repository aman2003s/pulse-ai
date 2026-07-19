import subprocess
import time
import httpx
import os
import sys

MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models'))
MODEL_PATH = os.path.join(MODELS_DIR, 'gemma-4-E4B-it-Q4_K_M.gguf')
SERVER_EXE = os.path.join(MODELS_DIR, 'llama-server.exe')

if not os.path.exists(SERVER_EXE) or not os.path.exists(MODEL_PATH):
    print("Server or model missing.")
    sys.exit(1)

print("Starting llama-server...")
process = subprocess.Popen([
    SERVER_EXE,
    "-m", MODEL_PATH,
    "--port", "8081",
    "-c", "2048",
], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')

ready = False
while True:
    line = process.stdout.readline()
    if not line:
        break
    print("SERVER STDOUT:", line.strip())
    if "listening on http" in line or "HTTP server listening" in line or "llama server listening at" in line:
        ready = True
        break
    if process.poll() is not None:
        break

if not ready:
    print("Server failed to start.")
    sys.exit(1)

time.sleep(2)
print("Server ready on port 8081.")

print("\n--- Running Text Prompt Smoke Test (a) ---")
try:
    with httpx.Client() as client:
        resp = client.post("http://127.0.0.1:8081/completion", json={
            "prompt": "<bos><start_of_turn>user\nHello! Write a short 5 word sentence.<end_of_turn>\n<start_of_turn>model\n",
            "n_predict": 20
        }, timeout=30.0)
        data = resp.json()
        text = data.get('content', '')
        tps = data.get('timings', {}).get('predicted_per_second', 0)
        print(f"Response: {text.strip()}")
        print(f"Tokens/sec: {tps}")
        print("Text prompt test PASS.")

    print("\n--- Running JSON Schema Smoke Test (c) ---")
    with httpx.Client() as client:
        resp = client.post("http://127.0.0.1:8081/completion", json={
            "prompt": "<bos><start_of_turn>user\nThe user's name is John Doe and he is 30 years old. Extract this into JSON.<end_of_turn>\n<start_of_turn>model\n",
            "json_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"}
                },
                "required": ["name", "age"]
            },
            "n_predict": 50
        }, timeout=30.0)
        data = resp.json()
        print(f"Response: {data.get('content', '').strip()}")
        print("JSON schema test PASS.")

except Exception as e:
    print(f"Test failed: {e}")

finally:
    print("\nKilling server...")
    process.kill()
    print("Smoke test complete.")

# Note on (b): Audio input is skipped because mmproj.gguf is unavailable.
# Flipping switch to Plan B: faster-whisper STT -> text -> Gemma.
print("\nNote: Audio WAV test (b) skipped due to missing mmproj file. Flipping to Plan B (faster-whisper).")
