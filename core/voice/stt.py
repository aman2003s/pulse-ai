import os
import io
from faster_whisper import WhisperModel
import tempfile
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

class STTService:
    def __init__(self, model_size="base.en"):
        print(f"Loading faster-whisper model ({model_size})...")
        whisper_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'models', 'whisper'))
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8", download_root=whisper_dir)
        
    # Biases decoding toward Pulse's actual vocabulary — fixes misheard commands
    # like "desktop" -> "next up" on short, ambiguous utterances.
    VOCAB_PROMPT = (
        "Pulse, open, close, find, search, read, screen, desktop, documents, downloads, "
        "pictures, folder, file, notepad, chrome, explorer, narrate, repeat, spell, "
        "faster, slower, train my voice, what's on my screen"
    )

    def transcribe(self, wav_bytes: bytes, extra_vocab: str = "") -> str:
        if not wav_bytes:
            return ""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            temp_path = f.name

        prompt = f"{self.VOCAB_PROMPT}, {extra_vocab}" if extra_vocab else self.VOCAB_PROMPT
        try:
            segments, info = self.model.transcribe(temp_path, beam_size=5, initial_prompt=prompt)
            text = " ".join([segment.text for segment in segments])
            return text.strip()
        except Exception as e:
            print(f"STT error: {e}")
            return ""
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
