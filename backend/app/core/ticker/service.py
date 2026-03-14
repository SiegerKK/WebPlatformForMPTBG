"""
Ticker service — drives real-time turn advancement for all game matches.

Called either by the background asyncio task (production) or by the manual
POST /api/matches/{id}/tick endpoint (testing / admin).

Game-specific tick logic lives in each game's RuleSet.tick() implementation.
"""
import logging
from sqlalchemy.orm import Session

from app.core.matches.models import Match, MatchStatus

logger = logging.getLogger(__name__)


def tick_match(match_id_str: str, db: Session) -> dict:
    """
    Advance the world by one game-turn for the given match.

    Delegates all game-specific logic to the match's registered RuleSet.
    Returns a summary dict with events emitted (or an ``"error"`` key on
    failure).
    """
    match = db.query(Match).filter(Match.id == match_id_str).first()
    if not match:
        return {"error": "match not found"}
    if match.status != MatchStatus.ACTIVE:
        return {"error": f"match status is {match.status}, not active"}

    from app.core.commands.pipeline import get_ruleset
    ruleset = get_ruleset(match.game_id)
    if not ruleset:
        return {"error": f"no ruleset registered for game '{match.game_id}'"}

    result = ruleset.tick(match_id_str, db)

    # Notify connected WebSocket clients that the state changed.
    if "error" not in result:
        from app.core.ws.manager import ws_manager
        ws_manager.notify(match_id_str, {
            "type": "ticked",
            "match_id": match_id_str,
            "world_turn": result.get("world_turn"),
        })

    return result


def tick_all_active_matches(db: Session) -> dict:
    """Tick all active matches. Called by the background scheduler."""
    matches = db.query(Match).filter(Match.status == MatchStatus.ACTIVE).all()

    results = []
    for match in matches:
        try:
            result = tick_match(str(match.id), db)
            results.append({"match_id": str(match.id), **result})
        except Exception as exc:
            logger.error("Ticker failed for match %s: %s", match.id, exc)
            results.append({"match_id": str(match.id), "error": str(exc)})

    return {"ticked": len(results), "results": results}

