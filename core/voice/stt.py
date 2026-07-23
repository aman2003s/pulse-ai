import io
import warnings
import soundfile as sf
from transformers import AutoProcessor, MoonshineStreamingForConditionalGeneration

warnings.filterwarnings("ignore", category=UserWarning)

class STTService:
    # Moonshine beats Whisper base/small on accuracy at a fraction of the size, and is
    # built for exactly this use case (short commands, no fixed 30s zero-padded window).
    # Medium vs small streaming measured at 0.40s vs 0.17s inference on this machine —
    # a 0.23s delta that's negligible next to the rest of the voice pipeline (planning +
    # TTS), so medium is a fixed, permanent choice, not re-probed at runtime.
    MODEL_REPO = "UsefulSensors/moonshine-streaming-medium"

    def __init__(self, model_repo=None):
        repo = model_repo or self.MODEL_REPO
        print(f"Loading Moonshine model ({repo})...")
        self.processor = AutoProcessor.from_pretrained(repo)
        self.model = MoonshineStreamingForConditionalGeneration.from_pretrained(repo)

    def transcribe(self, wav_bytes: bytes, extra_vocab: str = "") -> str:
        if not wav_bytes:
            return ""
        try:
            audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            inputs = self.processor(audio, sampling_rate=sr, return_tensors="pt")
            out = self.model.generate(**inputs, max_new_tokens=128)
            text = self.processor.batch_decode(out, skip_special_tokens=True)[0]
            return text.strip()
        except Exception as e:
            print(f"STT error: {e}")
            return ""
