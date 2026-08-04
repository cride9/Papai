"""
app/services/ws_manager.py — WebSocket connection manager.

Manages active WebSocket connections per user, broadcasts online count,
and sends per-user stage updates during RAG pipeline execution.
"""

import json
import logging
from typing import Optional

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    """Thread-safe WebSocket connection manager (single process)."""

    def __init__(self):
        # user_id (str) → WebSocket
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        # If user already connected, close the old one gracefully
        if user_id in self._connections:
            try:
                await self._connections[user_id].close(code=1000)
            except Exception:
                pass
        self._connections[user_id] = websocket
        log.info(f"WS connected: user={user_id} (total: {len(self._connections)})")
        await self.broadcast_active_count()

    async def disconnect(self, user_id: str):
        """Remove a user's WebSocket connection."""
        ws = self._connections.pop(user_id, None)
        if ws:
            try:
                await ws.close(code=1000)
            except Exception:
                pass
        log.info(f"WS disconnected: user={user_id} (total: {len(self._connections)})")
        await self.broadcast_active_count()

    async def send_stage(self, user_id: str, stage: str):
        """Send a stage update to a specific user."""
        ws = self._connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "status", "status": stage}))
            except Exception:
                await self.disconnect(user_id)

    async def send_error(self, user_id: str, message: str):
        """Send an error message to a specific user."""
        ws = self._connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "error", "message": message}))
            except Exception:
                await self.disconnect(user_id)

    async def broadcast_active_count(self):
        """Broadcast the current online user count to all connected clients."""
        count = len(self._connections)
        payload = json.dumps({"type": "active_users", "count": count})
        disconnected = []
        for user_id, ws in self._connections.items():
            try:
                await ws.send_text(payload)
            except Exception:
                disconnected.append(user_id)
        for uid in disconnected:
            self._connections.pop(uid, None)

    def get_active_count(self) -> int:
        """Return the number of currently connected users."""
        return len(self._connections)

    def is_connected(self, user_id: str) -> bool:
        """Check if a specific user is connected."""
        return user_id in self._connections


# Singleton instance
ws_manager = ConnectionManager()
