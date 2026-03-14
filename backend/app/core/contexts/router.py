import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from .schemas import GameContextCreate, GameContextRead
from .service import create_context, get_context, get_match_contexts, get_context_tree
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.core.visibility.service import FogProjection, VisibilityPolicy
from app.database import get_db

router = APIRouter(tags=["contexts"])


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
    ctx.state_blob = load_context_state(ctx.id, ctx)
    return ctx


@router.get("/matches/{match_id}/contexts", response_model=List[GameContextRead])
def get_tree(match_id: uuid.UUID, db: Session = Depends(get_db)):
    ctxs = get_context_tree(match_id, db)
    from app.core.state_cache.service import load_context_state
    for ctx in ctxs:
        ctx.state_blob = load_context_state(ctx.id, ctx)
    return ctxs


@router.get("/contexts/{context_id}/projection")
def get_projection(context_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ctx = get_context(context_id, db)
    policy = VisibilityPolicy()
    fog = FogProjection()
    return fog.project(ctx, current_user.id, policy, db)

