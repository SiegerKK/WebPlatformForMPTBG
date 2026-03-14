"""
Zone Stalkers game-specific API endpoints.

These routes live here (not in app/core) because they contain game-specific
domain knowledge (zone_map, zone_event context types, etc.) that must not
pollute the generic platform core.
"""
import uuid
import copy
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.core.contexts.schemas import GameContextCreate, GameContextRead
from app.core.contexts.service import create_context
from app.database import get_db

router = APIRouter(tags=["zone_stalkers"])


class ZoneEventCreate(BaseModel):
    match_id: uuid.UUID
    zone_map_context_id: uuid.UUID
    title: str
    description: str = ""
    max_turns: int = 5
    participant_ids: List[str] = []   # player IDs to auto-add (empty = open join)


@router.post("/contexts/zone-event", response_model=GameContextRead)
def create_zone_event(
    data: ZoneEventCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new zone_event context (text quest) as a child of a zone_map
    context.

    Any match participant or admin can create an event.  The event is
    registered in the zone_map's ``active_events`` list so players can join.
    """
    from app.core.contexts.models import GameContext, ContextStatus
    from app.games.zone_stalkers.rules.event_rules import create_zone_event_state

    # Validate zone_map context belongs to the match
    zone_ctx = db.query(GameContext).filter(
        GameContext.id == data.zone_map_context_id,
        GameContext.match_id == data.match_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not zone_ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found in this match")

    event_id = str(uuid.uuid4())
    event_state = create_zone_event_state(
        event_id=event_id,
        title=data.title,
        description=data.description,
        location_id="",
        participant_ids=data.participant_ids,
        max_turns=data.max_turns,
    )

    ctx_data = GameContextCreate(
        match_id=data.match_id,
        parent_context_id=data.zone_map_context_id,
        context_type="zone_event",
        label=data.title,
        state_blob=event_state,
    )
    event_ctx = create_context(ctx_data, db)

    # Register the event in the zone_map's active_events list.
    # Reassign state_blob entirely so SQLAlchemy tracks the mutation.
    zone_state = copy.deepcopy(zone_ctx.state_blob or {})
    zone_state.setdefault("active_events", []).append(str(event_ctx.id))
    zone_ctx.state_blob = zone_state
    zone_ctx.state_version += 1
    db.commit()

    return event_ctx
