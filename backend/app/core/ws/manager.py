"""
Framework WebSocket connection manager.

Tracks active WebSocket connections per match and provides a sync-safe
``notify()`` helper that game code can call from synchronous tick/command
handlers — it schedules the async broadcast on the already-running asyncio
event loop via ``loop.create_task()``.

Usage (from sync code inside a FastAPI request or background task):
    from app.core.ws.manager import ws_manager
    ws_manager.notify(match_id, {"type": "ticked", "world_turn": 42})

The WebSocket endpoint (app/core/ws/router.py) calls ``connect`` /
``disconnect`` and keeps the socket alive.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Per-match WebSocket connection registry."""

    def __init__(self) -> None:
        # match_id (str) → set of active WebSocket objects
        self._connections: Dict[str, Set[WebSocket]] = {}
        # conn_id (str(id(ws))) → WebSocket — reverse map for per-connection sends
        self._conn_id_to_ws: Dict[str, WebSocket] = {}
        # Event loop reference stored at startup for thread-safe scheduling.
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Store the main event loop so that ``notify()`` can schedule broadcasts
        from worker threads (e.g. ``asyncio.to_thread`` / ``run_in_executor``).
        Called once at application startup.
        """
        self._loop = loop

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self, match_id: str, ws: WebSocket) -> None:
        """Register *ws* under *match_id*. The caller must have already accepted the WebSocket."""
        conn_id = str(id(ws))
        self._conn_id_to_ws[conn_id] = ws
        self._connections.setdefault(match_id, set()).add(ws)
        logger.debug("WS connect: match=%s total=%d", match_id, len(self._connections[match_id]))

    def disconnect(self, match_id: str, ws: WebSocket) -> None:
        """Remove *ws* from the registry (safe to call if not registered)."""
        conn_id = str(id(ws))
        self._conn_id_to_ws.pop(conn_id, None)
        sockets = self._connections.get(match_id)
        if sockets:
            sockets.discard(ws)
            if not sockets:
                del self._connections[match_id]
        logger.debug("WS disconnect: match=%s", match_id)

    def get_connection(self, conn_id: str) -> WebSocket | None:
        """Return the WebSocket for a given connection id, or None if not found."""
        return self._conn_id_to_ws.get(conn_id)

    # ── Broadcast ─────────────────────────────────────────────────────────────

    async def broadcast(self, match_id: str, data: Dict[str, Any]) -> None:
        """Send *data* as JSON to every socket connected to *match_id*."""
        sockets = list(self._connections.get(match_id, set()))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(data)
            except Exception as exc:
                logger.debug("WS send failed (will disconnect): %s", exc)
                dead.append(ws)
        for ws in dead:
            self.disconnect(match_id, ws)

    async def send_to(self, ws: WebSocket, data: dict) -> None:
        """Send *data* as JSON to a single WebSocket connection."""
        try:
            await ws.send_json(data)
        except Exception as exc:
            logger.debug("WS send_to failed: %s", exc)

    def notify(self, match_id: str, data: Dict[str, Any]) -> None:
        """
        Schedule a broadcast from **synchronous** code.

        Safe to call from:
        * Inside a FastAPI request handler (asyncio event loop running).
        * A worker thread (e.g. ``asyncio.to_thread`` / ``run_in_executor``) —
          uses ``asyncio.run_coroutine_threadsafe`` with the stored loop.
        * Unit-test context with no event loop — silently skipped.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(match_id, data))
            return
        except RuntimeError:
            pass

        # Called from a thread — schedule via the stored event loop.
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(match_id, data), self._loop)


# ── Module-level singleton ─────────────────────────────────────────────────────
ws_manager = ConnectionManager()


# ── Debug subscription tracking ───────────────────────────────────────────────
# {match_id: {connection_id: subscription_dict}}
_debug_subscriptions: dict[str, dict[str, dict]] = {}


def add_debug_subscription(match_id: str, connection_id: str, subscription: dict) -> None:
    """Register a debug subscription for a connection."""
    _debug_subscriptions.setdefault(match_id, {})[connection_id] = subscription


def remove_debug_subscription(match_id: str, connection_id: str) -> None:
    """Remove a debug subscription for a connection."""
    _debug_subscriptions.get(match_id, {}).pop(connection_id, None)


def get_debug_subscriptions(match_id: str) -> dict[str, dict]:
    """Return a snapshot of active debug subscriptions for a match."""
    return dict(_debug_subscriptions.get(match_id, {}))
