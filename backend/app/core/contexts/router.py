import uuid
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .schemas import GameContextCreate, GameContextRead
from .service import create_context, get_context, get_match_contexts, get_context_tree
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.core.visibility.service import FogProjection, VisibilityPolicy
from app.database import get_db

router = APIRouter(tags=["contexts"])


def _tictactoe_initial_state(match_id: uuid.UUID, db: Session) -> dict | None:
    """
    Look up match participants and return a pre-initialised Tic-Tac-Toe
    state blob, or None if fewer than 2 participants are present.

    The first participant (by join time) is assigned 'X' and goes first.
    """
    from app.core.matches.models import Participant
    parts = (
        db.query(Participant)
        .filter(Participant.match_id == match_id)
        .order_by(Participant.joined_at, Participant.id)
        .all()
    )
    if len(parts) < 2:
        return None
    # Skip bot/system participants that have no user_id
    user_parts = [p for p in parts if p.user_id is not None]
    if len(user_parts) < 2:
        return None
    p1 = str(user_parts[0].user_id)
    p2 = str(user_parts[1].user_id)
    return {
        "board": [None] * 9,
        "player_marks": {p1: "X", p2: "O"},
        "current_player_id": p1,
        "winner": None,
        "winner_mark": None,
        "game_over": False,
        "turn_count": 0,
    }


def _zone_stalkers_initial_state(match_id: uuid.UUID, db: Session) -> dict | None:
    """
    Generate a deterministic zone_map state for a Zone Stalkers match.

    Uses the match's seed for deterministic world generation.
    Assigns player agents to human participants.
    """
    from app.core.matches.models import Match, Participant
    from app.games.zone_stalkers.generators.zone_generator import generate_zone

    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        return None

    parts = (
        db.query(Participant)
        .filter(Participant.match_id == match_id)
        .order_by(Participant.joined_at, Participant.id)
        .all()
    )
    user_parts = [p for p in parts if p.user_id is not None]
    num_players = max(1, len(user_parts))

    seed = match.seed if match.seed is not None else 42
    state = generate_zone(seed=seed, num_players=num_players)

    # Bind human participant IDs to pre-generated player agent slots
    for i, part in enumerate(user_parts):
        agent_id = f"agent_p{i}"
        participant_id = str(part.user_id)
        if agent_id in state["agents"]:
            state["agents"][agent_id]["controller"]["participant_id"] = participant_id
            state["player_agents"][participant_id] = agent_id

    return state


@router.post("/contexts", response_model=GameContextRead)
def create(data: GameContextCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # For tictactoe games: auto-initialize state so turn order is set from
    # the very first render — avoids both players seeing "waiting for opponent".
    if data.context_type == "tictactoe" and not data.state_blob:
        initial = _tictactoe_initial_state(data.match_id, db)
        if initial:
            data = data.model_copy(update={"state_blob": initial})

    # For zone_stalkers: generate the world deterministically from the match seed.
    if data.context_type == "zone_map" and not data.state_blob:
        initial = _zone_stalkers_initial_state(data.match_id, db)
        if initial:
            data = data.model_copy(update={"state_blob": initial})

    return create_context(data, db)


@router.get("/contexts/{context_id}", response_model=GameContextRead)
def get(context_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_context(context_id, db)


@router.get("/matches/{match_id}/contexts", response_model=List[GameContextRead])
def get_tree(match_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_context_tree(match_id, db)


@router.get("/contexts/{context_id}/projection")
def get_projection(context_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ctx = get_context(context_id, db)
    policy = VisibilityPolicy()
    fog = FogProjection()
    return fog.project(ctx, current_user.id, policy, db)
