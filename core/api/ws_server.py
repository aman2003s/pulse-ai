import asyncio
import websockets
import json
import logging
from typing import Set

logger = logging.getLogger(__name__)

class WebSocketServer:
    def __init__(self, host="127.0.0.1", port=7550):
        self.host = host
        self.port = port
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.message_callback = None

    def set_callback(self, callback):
        """Callback takes a parsed JSON dictionary."""
        self.message_callback = callback

    async def register(self, websocket):
        self.clients.add(websocket)
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if self.message_callback:
                        self.message_callback(data)
                except json.JSONDecodeError:
                    logger.error("Failed to parse incoming WS message.")
        finally:
            self.clients.remove(websocket)

    async def broadcast(self, message: dict):
        if not self.clients:
            return
        
        msg_str = json.dumps(message)
        # Gather sends message to all connected clients concurrently
        await asyncio.gather(
            *[client.send(msg_str) for client in self.clients],
            return_exceptions=True
        )

    async def start(self):
        logger.info(f"WebSocket server starting on ws://{self.host}:{self.port}")
        async with websockets.serve(self.register, self.host, self.port):
            await asyncio.Future()  # run forever

    def run_in_thread(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.start())
