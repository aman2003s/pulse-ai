import os
import shutil
import urllib.request
import json
import zipfile
import sys

from core.paths import models_dir

MODELS_DIR = models_dir()
# Public source (confirmed working 2026-07-28) — this used to be a hardcoded
# path on the original developer's own machine, which meant this script
# silently did nothing for anyone else who cloned the repo. The planner model
# is the one thing every install genuinely needs, so this can't be a manual
# step left implicit.
GEMMA_URL = "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf"
GEMMA_TARGET = os.path.join(MODELS_DIR, "gemma-4-E4B-it-Q4_K_M.gguf")
# Vision projector for look_at_screen/click_at_position (screen understanding) —
# optional: llama-server runs text-only fine without it, but those two tools
# will fail without --mmproj pointed at this file.
MMPROJ_URL = "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf"
MMPROJ_TARGET = os.path.join(MODELS_DIR, "mmproj.gguf")

def _download_with_progress(url, target_path, label):
    print(f"Downloading {label} from {url}...")
    def _report(block_num, block_size, total_size):
        if total_size <= 0:
            return
        done = block_num * block_size
        pct = min(100, done * 100 // total_size)
        print(f"\r  {label}: {pct}% ({done // (1024*1024)}MB / {total_size // (1024*1024)}MB)", end="", flush=True)
    tmp_path = target_path + ".partial"
    try:
        urllib.request.urlretrieve(url, tmp_path, reporthook=_report)
        print()
        os.replace(tmp_path, target_path)
        print(f"{label} downloaded successfully.")
    except Exception as e:
        print(f"\n{label} download failed: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def setup_gemma():
    if os.path.exists(GEMMA_TARGET):
        print(f"Gemma already exists at {GEMMA_TARGET}")
        return
    os.makedirs(MODELS_DIR, exist_ok=True)
    _download_with_progress(GEMMA_URL, GEMMA_TARGET, "Gemma 4 E4B (~5GB)")

def setup_mmproj():
    if os.path.exists(MMPROJ_TARGET):
        print(f"mmproj already exists at {MMPROJ_TARGET}")
        return
    os.makedirs(MODELS_DIR, exist_ok=True)
    _download_with_progress(MMPROJ_URL, MMPROJ_TARGET, "vision projector (~950MB)")

def fetch_llama_server():
    server_path = os.path.join(MODELS_DIR, "llama-server.exe")
    if os.path.exists(server_path):
        print("llama-server.exe already exists.")
        return
    
    print("Fetching latest llama.cpp release URL for Windows...")
    api_url = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"
    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
        
        download_url = None
        for asset in data.get('assets', []):
            name = asset.get('name', '')
            if 'bin-win' in name and name.endswith('.zip') and 'vulkan' in name:
                download_url = asset.get('browser_download_url')
                break
        
        if not download_url:
            for asset in data.get('assets', []):
                name = asset.get('name', '')
                if 'bin-win' in name and name.endswith('.zip'):
                    download_url = asset.get('browser_download_url')
                    break
        
        if download_url:
            print(f"Downloading llama.cpp from {download_url}...")
            zip_path = os.path.join(MODELS_DIR, "llama_bin.zip")
            urllib.request.urlretrieve(download_url, zip_path)
            
            print("Extracting llama-server and DLLs...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file in zip_ref.namelist():
                    if file.endswith('.exe') or file.endswith('.dll'):
                        source = zip_ref.open(file)
                        target_path = os.path.join(MODELS_DIR, os.path.basename(file))
                        with source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
            os.remove(zip_path)
            print("llama-server.exe downloaded and extracted.")
        else:
            print("Could not find a suitable Windows release for llama.cpp.")
    except Exception as e:
        print(f"Error fetching llama-server: {e}")

def fetch_other_models():
    print("Triggering download of python-based models (Kokoro, Silero, openWakeWord)...")
    try:
        import torch
        print("Downloading Silero VAD model...")
        model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', force_reload=False)
        print("Silero VAD model triggered successfully.")
    except Exception as e:
        print(f"Silero fetch failed (is venv active?): {e}")

    try:
        import openwakeword
        print("Downloading openWakeWord models...")
        openwakeword.utils.download_models()
        print("openWakeWord models downloaded.")
    except Exception as e:
        print(f"openWakeWord fetch failed (is venv active?): {e}")

if __name__ == "__main__":
    os.makedirs(MODELS_DIR, exist_ok=True)
    setup_gemma()
    setup_mmproj()
    fetch_llama_server()
    fetch_other_models()
    print("Model fetch process completed.")
