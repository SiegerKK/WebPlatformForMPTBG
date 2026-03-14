import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import settings
    task = None
    if settings.AUTO_TICK_ENABLED:
        task = asyncio.create_task(_background_ticker(settings.TICK_INTERVAL_SECONDS))
        logger.info(
            "World ticker started (interval=%ds, 1 game-hour per tick)",
            settings.TICK_INTERVAL_SECONDS,
        )
    yield
    if task:
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

@app.get("/health")
def health():
    return {"status": "healthy"}
