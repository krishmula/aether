"""WebSocket event broadcaster for the Aether orchestrator."""

from __future__ import annotations

import json
import logging
import time

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """Manages connected WebSocket clients and broadcasts events to all of them."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    def register(self, ws: WebSocket) -> None:
        self._connections.add(ws)

    def unregister(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def emit(self, event_type, data: dict) -> None:
        """Broadcast a JSON event to all connected clients.

        Callers pass an EventType enum member; .value converts it to a string.
        Dead connections are silently removed.
        """
        message = json.dumps(
            {
                "type": event_type.value,
                "data": data,
                "timestamp": time.time(),
            }
        )
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except (WebSocketDisconnect, RuntimeError, ConnectionError) as e:
                logger.debug("WebSocket client disconnected during emit: %s", e)
                dead.add(ws)
        self._connections -= dead
