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
        """Accept *ws* and register it under *match_id*."""
        await ws.accept()
        self._connections.setdefault(match_id, set()).add(ws)
        logger.debug("WS connect: match=%s total=%d", match_id, len(self._connections[match_id]))

    def disconnect(self, match_id: str, ws: WebSocket) -> None:
        """Remove *ws* from the registry (safe to call if not registered)."""
        sockets = self._connections.get(match_id)
        if sockets:
            sockets.discard(ws)
            if not sockets:
                del self._connections[match_id]
        logger.debug("WS disconnect: match=%s", match_id)

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
