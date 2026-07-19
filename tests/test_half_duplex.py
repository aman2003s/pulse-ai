import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.voice.controller import VoiceController

if __name__ == "__main__":
    controller = VoiceController()
    controller.start()
