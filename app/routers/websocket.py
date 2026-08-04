"""
app/routers/websocket.py — WebSocket endpoint for real-time updates.

ws://host:port/ws/{token}

Accepts JWT token as path parameter (browsers can't set headers on WS upgrade).
Receives: "ping" → replies "pong"
Sends: {"type": "status", "status": "searching|analyzing|refining|generating|done"}
Sends: {"type": "active_users", "count": N}
Sends: {"type": "error", "message": "..."}
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.auth_service import decode_access_token
from app.services.ws_manager import ws_manager

log = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    """
    WebSocket endpoint with JWT authentication via path token.

    After connecting, the client receives:
      - active_users count on every connect/disconnect
      - stage updates during RAG pipeline execution
      - pong responses to ping keep-alives
    """
    # Validate JWT
    payload = decode_access_token(token)
    if payload is None:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    user_id = payload.get("sub")
    username = payload.get("username", "unknown")
    if not user_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    # Register connection
    await ws_manager.connect(websocket, user_id)
    log.info(f"WebSocket authenticated: user_id={user_id}, username={username}")

    try:
        while True:
            data = await websocket.receive_text()

            # Handle ping/pong keep-alive
            if data == "ping":
                await websocket.send_text("pong")
            else:
                # Ignore unknown messages
                pass
    except WebSocketDisconnect:
        log.info(f"WebSocket disconnected: user_id={user_id}")
    except Exception as e:
        log.error(f"WebSocket error for user_id={user_id}: {e}")
    finally:
        await ws_manager.disconnect(user_id)
