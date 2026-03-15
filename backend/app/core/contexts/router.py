import copy
import uuid
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .schemas import GameContextCreate, GameContextRead
from .service import create_context, get_context, get_match_contexts, get_context_tree
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.core.visibility.service import FogProjection, VisibilityPolicy
from app.database import get_db

router = APIRouter(tags=["contexts"])


def _strip_agent_memory(state_blob: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow-copy of *state_blob* with ``memory`` removed from every
    agent and trader dict.

    Agent memory can hold up to 2000 entries per entity.  Including it in every
    ``getTree`` / ``get`` response wastes bandwidth because the frontend only
    needs it on demand (memory tab, profile modal).  The full array is still
    preserved in Redis / the database and served by the dedicated
    ``GET /contexts/{id}/agents/{agent_id}/memory`` endpoint.
    """
    stripped = copy.copy(state_blob)
    for collection in ("agents", "traders"):
        if collection in stripped and isinstance(stripped[collection], dict):
            stripped[collection] = {
                k: {**v, "memory": []} if isinstance(v, dict) and "memory" in v else v
                for k, v in stripped[collection].items()
            }
    return stripped


@router.post("/contexts", response_model=GameContextRead)
def create(data: GameContextCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Delegate auto-initialisation to the game's own RuleSet so no
    # game-specific code lives in the platform core.
    if not data.state_blob:
        from app.core.matches.models import Match
        from app.core.commands.pipeline import get_ruleset
        match = db.query(Match).filter(Match.id == data.match_id).first()
        if match:
            ruleset = get_ruleset(match.game_id)
            if ruleset:
                initial = ruleset.create_initial_context_state(data.context_type, data.match_id, db)
                if initial:
                    data = data.model_copy(update={"state_blob": initial})

    return create_context(data, db)


@router.get("/contexts/{context_id}", response_model=GameContextRead)
def get(context_id: uuid.UUID, db: Session = Depends(get_db)):
    ctx = get_context(context_id, db)
    # Serve the authoritative state (Redis if ahead of DB, otherwise DB).
    from app.core.state_cache.service import load_context_state
    state = load_context_state(ctx.id, ctx)
    ctx.state_blob = _strip_agent_memory(state)
    return ctx


@router.get("/matches/{match_id}/contexts", response_model=List[GameContextRead])
def get_tree(match_id: uuid.UUID, db: Session = Depends(get_db)):
    ctxs = get_context_tree(match_id, db)
    from app.core.state_cache.service import load_context_state
    for ctx in ctxs:
        state = load_context_state(ctx.id, ctx)
        ctx.state_blob = _strip_agent_memory(state)
    return ctxs


@router.get("/contexts/{context_id}/agents/{agent_id}/memory")
def get_agent_memory(
    context_id: uuid.UUID,
    agent_id: str,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Return the full memory array for a single agent (or trader) on demand.

    Callers should use this instead of relying on ``state_blob.agents[id].memory``
    which is now stripped from the ``getTree`` / ``get`` responses to reduce
    bandwidth.
    """
    ctx = get_context(context_id, db)
    from app.core.state_cache.service import load_context_state
    state = load_context_state(ctx.id, ctx)
    # Check agents first, then traders (both can carry memory entries)
    entity = (
        state.get("agents", {}).get(agent_id)
        or state.get("traders", {}).get(agent_id)
    )
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found in context")
    return entity.get("memory", [])


@router.get("/contexts/{context_id}/projection")
def get_projection(context_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ctx = get_context(context_id, db)
    policy = VisibilityPolicy()
    fog = FogProjection()
    return fog.project(ctx, current_user.id, policy, db)

