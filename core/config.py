import json
import os

CONFIG_PATH = os.path.expandvars(r"%APPDATA%\Pulse\config.json")

DEFAULT_CONFIG = {
    "mic_device_id": None,
    "voice_choice": "default",
    "feedback_mode": "Standard",
    "model_paths": {
        "gemma": "models/gemma-4-E4B-it-Q4_K_M.gguf",
        "mmproj": "models/mmproj.gguf"
    },
    "port": 7550
}

def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG

def save_config(config_data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=4)
