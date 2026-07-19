from kokoro import KPipeline
import sounddevice as sd
import numpy as np
import threading
import time
import re
import warnings

warnings.filterwarnings("ignore")

class TTSService:
    def __init__(self):
        print("Loading Kokoro TTS Service...")
        # 'a' for American English
        self.pipeline = KPipeline(lang_code='a')
        self.voice = 'af_heart'
        self.speed = 1.0
        self.cancel_event = threading.Event()
        self.is_playing = False
        self.last_active = 0.0  # for AEC-lite: audio can still be resonating briefly after playback ends
        
    def _chunk_text(self, text):
        # Split on sentence boundaries to allow streaming
        sentences = re.split(r'(?<=[.!?]) +', text)
        return sentences

    def speak(self, text):
        """Blocking call to speak text. Generation of sentence N+1 overlaps playback of
        sentence N (producer/consumer), so there are no silent gaps between sentences.
        Interruptible via self.cancel_event."""
        import queue
        self.cancel_event.clear()
        self.is_playing = True
        sentences = [s for s in self._chunk_text(text) if s.strip()]
        q = queue.Queue(maxsize=3)

        def producer():
            try:
                for sentence in sentences:
                    if self.cancel_event.is_set():
                        break
                    for _, _, audio in self.pipeline(sentence, voice=self.voice, speed=self.speed):
                        if self.cancel_event.is_set():
                            break
                        q.put(audio)
            except Exception as e:
                print(f"TTS generation error: {e}")
            finally:
                q.put(None)

        threading.Thread(target=producer, daemon=True).start()
        try:
            while not self.cancel_event.is_set():
                audio = q.get()
                if audio is None:
                    break
                sd.play(audio, 24000)
                while sd.get_stream() and sd.get_stream().active:
                    if self.cancel_event.is_set():
                        sd.stop()
                        break
                    self.last_active = time.time()
                    time.sleep(0.02)
        except Exception as e:
            print(f"TTS Error: {e}")
        finally:
            self.is_playing = False
            self.last_active = time.time()
            sd.stop()

    def cancel(self):
        """Immediately stops playback."""
        print("TTS Cancelled.")
        self.cancel_event.set()
        sd.stop()
        self.is_playing = False

    def speak_now(self, text, speed=None):
        """Fire-and-forget single short utterance (typing echo). Does not touch
        cancel_event/is_playing, so it never fights the main assistant speech."""
        try:
            sd.stop()
            for _, _, audio in self.pipeline(text, voice=self.voice, speed=speed or self.speed):
                sd.play(audio, 24000)
                sd.wait()
        except Exception as e:
            print(f"speak_now error: {e}")
