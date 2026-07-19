import os
import shutil
import urllib.request
import json
import zipfile
import sys

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
GEMMA_SOURCE = r"C:\Users\itzam\Desktop\animatron\models\gemma-4-E4B-it-Q4_K_M.gguf"
GEMMA_TARGET = os.path.join(MODELS_DIR, "gemma-4-E4B-it-Q4_K_M.gguf")

def setup_gemma():
    if os.path.exists(GEMMA_TARGET):
        print(f"Gemma already exists at {GEMMA_TARGET}")
        return
    if os.path.exists(GEMMA_SOURCE):
        print(f"Copying Gemma from {GEMMA_SOURCE} to {GEMMA_TARGET}...")
        os.makedirs(MODELS_DIR, exist_ok=True)
        shutil.copy2(GEMMA_SOURCE, GEMMA_TARGET)
        print("Copied successfully.")
    else:
        print(f"Warning: Gemma source not found at {GEMMA_SOURCE}")

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
    fetch_llama_server()
    fetch_other_models()
    print("Model fetch process completed.")
