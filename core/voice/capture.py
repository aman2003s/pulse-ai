import numpy as np
import sounddevice as sd
import torch
import time
import io
import wave
import os
import threading
import warnings

# Suppress warnings from torch hub
warnings.filterwarnings("ignore", category=UserWarning)

class CapturePipeline:
    def __init__(self, sample_rate=16000):
        self.sample_rate = sample_rate
        print("Loading Silero VAD...")
        self.model, self.utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False
        )
        self.earcon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'models', 'ack.wav'))
        self._abort = threading.Event()

    def cancel_capture(self):
        """Aborts an in-progress capture_until_silence() call immediately. Fixes the bug
        where 'cancel' stopped TTS but a blocking mic recording kept running regardless,
        letting it race a subsequent command."""
        self._abort.set()
        sd.stop()

    def play_earcon(self):
        # Rapidly switching output sample rate (Kokoro plays at 24kHz, this at 16kHz)
        # can glitch some Windows audio drivers, especially back-to-back in the wake-word
        # training loop. sd.stop() + one retry makes it reliable.
        import soundfile as sf
        data, fs = sf.read(self.earcon_path)
        for attempt in range(2):
            try:
                sd.stop()
                sd.play(data, fs)
                sd.wait()
                return
            except Exception as e:
                print(f"Earcon failed to play (attempt {attempt + 1}): {e}")
                time.sleep(0.15)

    def capture_until_silence(self, silence_threshold_ms=900, max_duration_s=25.0, no_speech_timeout_s=6.0):
        self._abort.clear()
        self.play_earcon()
        if self._abort.is_set():  # cancelled during the earcon itself
            return None

        print("Listening for user speech...")
        audio_buffer = []
        silence_start_time = None
        has_spoken = False
        start_time = time.time()
        
        # 512 samples at 16kHz = 32ms
        chunk_size = 512
        
        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='float32') as stream:
            while True:
                if self._abort.is_set():
                    print("Capture aborted (cancel).")
                    return None
                if time.time() - start_time > max_duration_s:
                    print("Max recording duration reached.")
                    break
                    
                chunk, overflow = stream.read(chunk_size)
                audio_buffer.append(chunk.copy())
                
                # Check VAD
                audio_tensor = torch.from_numpy(chunk.flatten())
                speech_prob = self.model(audio_tensor, self.sample_rate).item()
                
                if speech_prob > 0.5:
                    has_spoken = True
                    silence_start_time = None
                else:
                    if has_spoken:
                        if silence_start_time is None:
                            silence_start_time = time.time()
                        elif (time.time() - silence_start_time) * 1000 > silence_threshold_ms:
                            print("Silence detected, ending capture.")
                            break
                    else:
                        if time.time() - start_time > no_speech_timeout_s:
                            print(f"No speech detected ({no_speech_timeout_s}s timeout).")
                            break
                            
        if not audio_buffer:
            return None
            
        full_audio = np.concatenate(audio_buffer, axis=0)
        full_audio_int16 = (full_audio * 32767).astype(np.int16)
        
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(full_audio_int16.tobytes())
            
        return wav_io.getvalue()
