import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.core.auth.router import router as auth_router
from app.core.matches.router import router as matches_router
from app.core.contexts.router import router as contexts_router
from app.core.entities.router import router as entities_router
from app.core.commands.router import router as commands_router
from app.core.turns.router import router as turns_router
from app.core.events.router import router as events_router
from app.core.admin.router import router as admin_router
from app.core.ticker.router import router as ticker_router
from app.core.ws.router import router as ws_router
from app.core.commands.pipeline import register_ruleset
from app.games.tictactoe.rules import TicTacToeRuleSet
from app.games.zone_stalkers.ruleset import ZoneStalkerRuleSet
from app.games.zone_stalkers.router import router as zone_stalkers_router

logger = logging.getLogger(__name__)


async def _background_ticker(interval_seconds: int) -> None:
    """Periodically tick all active matches."""
    from app.database import SessionLocal
    from app.core.ticker.service import tick_all_active_matches
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            db = SessionLocal()
            try:
                result = tick_all_active_matches(db)
                if result["ticked"]:
                    logger.info("Auto-tick: %s matches ticked", result["ticked"])
            finally:
                db.close()
        except Exception as exc:
            logger.error("Background ticker error: %s", exc)


async def _debug_auto_ticker() -> None:
    """
    Fast lightweight poller for matches that have debug auto-tick enabled.
    stored in their game state.

    Runs ``tick_debug_auto_matches`` in a thread pool via
    ``asyncio.to_thread`` so that the DB/Redis I/O does not block the
    asyncio event loop.  The function manages its own DB session internally.
    WebSocket notifications from the worker thread use ``ws_manager``'s
    thread-safe scheduling path (``run_coroutine_threadsafe``).
    """
    from app.core.ticker.service import tick_debug_auto_matches
    while True:
        await asyncio.sleep(0.02)
        try:
            result = await asyncio.to_thread(tick_debug_auto_matches)
            if result.get("ticked"):
                logger.debug("Debug auto-tick: %s match(es) ticked", result["ticked"])
        except Exception as exc:
            logger.error("Debug auto-ticker error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import settings
    from app.core.ws.manager import ws_manager

    # Bind the running event loop to ws_manager so that worker threads
    # (debug auto-ticker) can schedule WebSocket broadcasts safely.
    ws_manager.bind_loop(asyncio.get_running_loop())

    tasks = []
    if settings.AUTO_TICK_ENABLED:
        tasks.append(asyncio.create_task(_background_ticker(settings.TICK_INTERVAL_SECONDS)))
        logger.info(
            "World ticker started (interval=%ds, 1 game-hour per tick)",
            settings.TICK_INTERVAL_SECONDS,
        )
    # Debug auto-ticker always runs (checks per-match flag in Redis state).
    tasks.append(asyncio.create_task(_debug_auto_ticker()))
    logger.info("Debug auto-ticker started (20 ms poll interval)")

    yield

    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="WebPlatformForMPTBG",
    description="Platform for async turn-based multiplayer games",
    lifespan=lifespan,
)

app.include_router(auth_router, prefix="/api")
app.include_router(matches_router, prefix="/api")
app.include_router(contexts_router, prefix="/api")
app.include_router(entities_router, prefix="/api")
app.include_router(commands_router, prefix="/api")
app.include_router(turns_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(ticker_router, prefix="/api")
app.include_router(ws_router, prefix="/api")
# Game-specific routers — each game may expose its own endpoints
app.include_router(zone_stalkers_router, prefix="/api")

# Register game rulesets
register_ruleset("tictactoe", TicTacToeRuleSet())
register_ruleset("zone_stalkers", ZoneStalkerRuleSet())

@app.get("/")
def root():
    return {"status": "ok", "platform": "WebPlatformForMPTBG"}

# ── Static media files (location images, etc.) ────────────────────────────────
_media_root = os.environ.get("MEDIA_ROOT", "/app/media")
os.makedirs(_media_root, exist_ok=True)
app.mount("/media", StaticFiles(directory=_media_root), name="media")

@app.get("/health")
def health():
    return {"status": "healthy"}
