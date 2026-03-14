"""
WebSocket router — real-time push notifications per match.

Endpoint: GET /api/ws/matches/{match_id}

Clients connect with a JWT token in the query string:
    ws://host/api/ws/matches/<uuid>?token=<jwt>

The server keeps the connection open and pushes a JSON notification every
time the match state changes (after a tick or a command).

Message shape:
    {"type": "ticked",   "match_id": "...", "world_turn": 42}  ← after tick
    {"type": "state_updated", "match_id": "..."}               ← after command

Clients should call their existing refresh() function on receipt.

The connection closes with code 4401 if authentication fails.
"""
import uuid
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.orm import Session

from app.core.ws.manager import ws_manager
from app.database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


def _authenticate_token(token: str) -> bool:
    """Return True if *token* is a valid, non-expired JWT for an existing user."""
    try:
        from jose import JWTError, jwt
        from app.config import settings

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str | None = payload.get("sub")
        if not user_id:
            return False
        db: Session = SessionLocal()
        try:
            from app.core.auth.models import User
            return db.query(User).filter(User.id == user_id).first() is not None
        finally:
            db.close()
    except Exception:
        return False


@router.websocket("/ws/matches/{match_id}")
async def match_websocket(
    match_id: uuid.UUID,
    ws: WebSocket,
    token: str = Query(default=""),
) -> None:
    """
    WebSocket endpoint for real-time match state push notifications.

    The client connects, receives push messages whenever the match state
    changes (tick or command), and can send ``{"type": "ping"}`` to keep
    the connection alive.  Any other client message is ignored.
    """
    # ── Auth ──────────────────────────────────────────────────────────────────
    if not _authenticate_token(token):
        await ws.close(code=4401)
        return

    mid = str(match_id)
    await ws_manager.connect(mid, ws)
    try:
        # Keep the socket open; process incoming messages (ping/pong).
        while True:
            data = await ws.receive_json()
            if isinstance(data, dict) and data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WS error for match %s: %s", mid, exc)
    finally:
        ws_manager.disconnect(mid, ws)
