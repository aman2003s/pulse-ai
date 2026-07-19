"""System-wide typing echo for accessibility: speaks each character as typed via a
dedicated fast SAPI voice, and the completed word on space/punctuation. Skips password
fields (checked live via UIA). Uses SAPI's purge-before-speak so each new keystroke
interrupts the previous announcement instead of silently dropping it — interrupt, not
skip, is how real screen readers (JAWS/NVDA) behave. A dedicated higher-rate SAPI voice
(not the main Kokoro assistant voice) because neural TTS is too slow to keep up with
real typing speed — the same reason JAWS runs character echo faster than normal speech.
"""
import threading
import queue
import string
import keyboard

PRINTABLE = set(string.ascii_letters + string.digits + string.punctuation + " ")
WORD_BOUNDARY = {"space", "enter", "tab", ".", ",", "!", "?", ";", ":"}

SVSFlagsAsync = 1
SVSFPurgeBeforeSpeak = 2


def _is_password_focused() -> bool:
    try:
        import uiautomation as auto
        c = auto.GetFocusedControl()
        return bool(getattr(c, "IsPassword", False))
    except Exception:
        return False


class TypingEcho:
    def __init__(self, tts):
        self.tts = tts  # main Kokoro assistant voice — checked so echo never talks over it
        self.enabled = False
        self.buffer = ""
        self._q = queue.Queue()
        self._hook = None
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voice.Rate = 6  # faster than normal reading speech, matches JAWS's dedicated char-echo rate
        while True:
            text = self._q.get()
            if not self.enabled or self.tts.is_playing:
                continue
            try:
                voice.Speak(text, SVSFlagsAsync | SVSFPurgeBeforeSpeak)
            except Exception:
                pass

    def _on_key(self, event):
        if not self.enabled or event.event_type != "down":
            return
        name = event.name or ""
        if _is_password_focused():
            self.buffer = ""
            return
        if name in WORD_BOUNDARY:
            if self.buffer:
                self._q.put(self.buffer)
                self.buffer = ""
        elif name == "backspace":
            self.buffer = self.buffer[:-1]
        elif len(name) == 1 and name in PRINTABLE:
            self.buffer += name
            self._q.put(name)

    def start(self):
        if self._hook is None:
            self._hook = keyboard.hook(self._on_key)

    def stop(self):
        if self._hook is not None:
            keyboard.unhook(self._hook)
            self._hook = None
