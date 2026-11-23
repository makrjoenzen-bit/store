import asyncio
import json
from typing import Set, Dict, Any
from fastapi import WebSocket
from loguru import logger

from src.core.redis import get_redis_client


class WebSocketManager:
    """Manages active browser WebSocket connections to Dashboard with Redis pub/sub fan-out."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.active_connections.add(websocket)
        logger.info(f"Dashboard client connected. Active: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self.lock:
            self.active_connections.discard(websocket)
        logger.info(f"Dashboard client disconnected. Active: {len(self.active_connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcasts payload to all open Dashboard WebSocket streams."""
        async with self.lock:
            sockets = list(self.active_connections)

        if not sockets:
            return

        payload = json.dumps(message, default=str)
        dead_sockets = []
        for ws in sockets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead_sockets.append(ws)

        if dead_sockets:
            async with self.lock:
                for dead in dead_sockets:
                    self.active_connections.discard(dead)

    async def start_redis_listener(self):
        """Background coroutine listening to Redis channels and pushing to WebSockets."""
        redis = get_redis_client()
        pubsub = redis.pubsub()
        await pubsub.subscribe(
            "orders:live",
            "alerts:stream",
            "suppliers:health",
            "config:updates"
        )
        logger.info("WebSocket Manager subscribed to Redis channels.")

        try:
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    try:
                        data = json.loads(msg["data"])
                        await self.broadcast({
                            "channel": msg["channel"],
                            "data": data,
                        })
                    except Exception as e:
                        logger.error(f"Error parsing redis message for websocket: {e}")
        except asyncio.CancelledError:
            await pubsub.unsubscribe()
            await pubsub.close()


ws_manager = WebSocketManager()
