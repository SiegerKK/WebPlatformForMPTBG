from fastapi import FastAPI
from app.core.auth.router import router as auth_router
from app.core.matches.router import router as matches_router
from app.core.contexts.router import router as contexts_router
from app.core.entities.router import router as entities_router
from app.core.commands.router import router as commands_router
from app.core.turns.router import router as turns_router
from app.core.events.router import router as events_router
from app.core.admin.router import router as admin_router
from app.core.commands.pipeline import register_ruleset
from app.games.tictactoe.rules import TicTacToeRuleSet
from app.games.zone_stalkers.ruleset import ZoneStalkerRuleSet

app = FastAPI(
    title="WebPlatformForMPTBG",
    description="Platform for async turn-based multiplayer games"
)

app.include_router(auth_router, prefix="/api")
app.include_router(matches_router, prefix="/api")
app.include_router(contexts_router, prefix="/api")
app.include_router(entities_router, prefix="/api")
app.include_router(commands_router, prefix="/api")
app.include_router(turns_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(admin_router, prefix="/api")

# Register game rulesets
register_ruleset("tictactoe", TicTacToeRuleSet())
register_ruleset("zone_stalkers", ZoneStalkerRuleSet())

@app.get("/")
def root():
    return {"status": "ok", "platform": "WebPlatformForMPTBG"}

@app.get("/health")
def health():
    return {"status": "healthy"}
