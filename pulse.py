import os
import sys
import subprocess
import time
import asyncio
import threading
import logging

# Whisper can transcribe characters Windows' default console codepage (cp1252) can't
# encode — a plain print() of that text crashes and silently kills whatever thread
# called it (this took down the capture-session thread repeatedly during testing).
# Reconfiguring stdout/stderr to UTF-8 with replacement fixes every print() call in
# the app at once, rather than hunting down each risky call site individually.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.api.ws_server import WebSocketServer
from core.voice.controller import VoiceController

from logging.handlers import RotatingFileHandler
LOG_DIR = os.path.join(os.environ.get("APPDATA", "."), "Pulse")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler(),
              RotatingFileHandler(os.path.join(LOG_DIR, "pulse.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")],
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # privacy: no transcripts at INFO
logger = logging.getLogger(__name__)

class PulseOrchestrator:
    def __init__(self):
        self.llama_process = None
        self.llama_port = 8081

    def start_llama(self):
        MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), 'models'))
        MODEL_PATH = os.path.join(MODELS_DIR, 'gemma-4-E4B-it-Q4_K_M.gguf')
        SERVER_EXE = os.path.join(MODELS_DIR, 'llama-server.exe')

        if not os.path.exists(SERVER_EXE):
            logger.error("llama-server not found.")
            return

        import httpx
        for port in (8081, 8082, 8083):  # M7.2: fall back if port is taken
            logger.info(f"Starting llama-server on port {port}...")
            self.llama_process = subprocess.Popen(
                [SERVER_EXE, "-m", MODEL_PATH, "--port", str(port), "-c", "8192", "-ngl", "99"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            deadline = time.time() + 120
            while time.time() < deadline:
                if self.llama_process.poll() is not None:
                    logger.warning(f"llama-server exited during startup on port {port}.")
                    break
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                        logger.info("llama-server ready.")
                        self.llama_port = port
                        return
                except httpx.RequestError:
                    pass
                time.sleep(2)
            else:
                logger.error("llama-server did not become healthy within 120s.")
                return
        logger.error("llama-server failed on all ports.")
        
    def check_and_restart_llama(self):
        if self.llama_process and self.llama_process.poll() is not None:
            logger.warning("llama-server crashed! Restarting...")
            self.start_llama()

    async def main_loop(self):
        # Start llama-server
        self.start_llama()
        
        # Start WebSocket server
        ws_server = WebSocketServer(port=7550)
        
        # Start Voice Controller
        vc = VoiceController(ws_server, planner_port=self.llama_port)
        
        def handle_inbound_ws(msg):
            mtype = msg.get("type")
            if mtype == "text_command":
                text = msg.get("text", "").strip()
                if text:
                    vc.handle_text_command(text)
            elif mtype == "wake":
                vc.on_wake_word_detected()
            elif mtype == "cancel":
                vc.tts.cancel()
                vc.capture.cancel_capture()
                vc.broadcast_state("idle")
            elif mtype == "train_wake_word":
                vc.train_wake_word(msg.get("word"))
            elif mtype == "list_devices":
                asyncio.run_coroutine_threadsafe(
                    ws_server.broadcast({"v": 1, "type": "devices", "inputs": vc.list_input_devices()}),
                    asyncio.get_event_loop()
                )
                asyncio.run_coroutine_threadsafe(
                    ws_server.broadcast({"v": 1, "type": "config", "wake_word": vc.wake_word,
                                          "feedback_mode": vc.feedback_mode, "narrate": vc.narrate,
                                          "typing_echo": vc.typing_echo.enabled}),
                    asyncio.get_event_loop()
                )
            elif mtype == "set_config":
                key, value = msg.get("key"), msg.get("value")
                if key == "feedback_mode" and value in ("Minimal", "Standard", "Guided"):
                    vc.feedback_mode = value
                    logger.info(f"feedback_mode set to {value}")
                elif key == "narrate":
                    vc.narrate = (value == "on")
                    logger.info(f"narrate set to {vc.narrate}")
                elif key == "typing_echo":
                    vc.typing_echo.enabled = (value == "on")
                    logger.info(f"typing_echo set to {vc.typing_echo.enabled}")
                elif key == "accessibility_mode":  # "Superhero Mode": one switch for all three
                    on = (value == "on")
                    vc.feedback_mode = "Guided" if on else "Standard"
                    vc.narrate = on
                    vc.typing_echo.enabled = on
                    if on:
                        import threading as _t
                        _t.Thread(target=vc.play_superhero_chime, daemon=True).start()
                    logger.info(f"accessibility_mode (Superhero Mode) set to {on}")
                elif key == "mic_device":
                    try:
                        vc.set_input_device(int(value))
                        logger.info(f"mic_device set to {value}")
                    except Exception as e:
                        logger.error(f"mic_device change failed: {e}")
                
        ws_server.set_callback(handle_inbound_ws)
        
        vc.start()
        
        logger.info("Pulse is running.")
        
        # Run WebSocket server in the main async loop
        ws_task = asyncio.create_task(ws_server.start())
        
        try:
            while True:
                self.check_and_restart_llama()
                await asyncio.sleep(5)
        except KeyboardInterrupt:
            logger.info("Shutting down Pulse...")
        finally:
            vc.stop()
            if self.llama_process:
                self.llama_process.terminate()

def ensure_single_instance():
    # Port-based lock: bind an exclusive localhost port. File-based locks proved
    # bypassable here — sandboxed/virtualized launches can see a DIFFERENT AppData,
    # so two "same path" lock files were actually two files and both instances lived.
    # An OS-level port bind cannot be virtualized: exactly one process can hold it.
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 7549))
        s.listen(1)
        return s  # keep alive for process lifetime — releasing it releases the lock
    except OSError:
        # Already running — silently exit. No popup: the UI will just connect to
        # the existing backend on its own reconnect loop.
        print("Pulse is already running. Exiting silently.")
        sys.exit(0)

if __name__ == "__main__":
    lock_fd = ensure_single_instance()
    
    orchestrator = PulseOrchestrator()
    asyncio.run(orchestrator.main_loop())
