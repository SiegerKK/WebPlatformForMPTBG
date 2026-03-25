"""
Zone Stalkers game-specific API endpoints.

These routes live here (not in app/core) because they contain game-specific
domain knowledge (zone_map, zone_event context types, etc.) that must not
pollute the generic platform core.
"""
import os
import uuid
import shutil
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.core.contexts.schemas import GameContextCreate, GameContextRead
from app.core.contexts.service import create_context
from app.database import get_db

router = APIRouter(tags=["zone_stalkers"])

# ── Media configuration ────────────────────────────────────────────────────────

MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/app/media")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB

_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


# ── Location image upload / delete ────────────────────────────────────────────

@router.post("/locations/{context_id}/{location_id}/image")
async def upload_location_image(
    context_id: uuid.UUID,
    location_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload an image for a location.

    The image is stored on disk and the URL is returned so the caller can
    persist it in the game state via the ``debug_update_location`` command.
    Any previously stored image for the same ``(context_id, location_id)``
    pair is deleted before the new file is saved.
    """
    from app.core.contexts.models import GameContext
    from app.games.zone_stalkers.models import LocationImage

    # Validate context exists
    ctx = db.query(GameContext).filter(GameContext.id == context_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")

    content_type = file.content_type or ""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type '{content_type}'. "
                   f"Allowed: {sorted(ALLOWED_IMAGE_TYPES)}",
        )

    # Read file content (enforcing size limit)
    contents = await file.read(MAX_IMAGE_SIZE + 1)
    if len(contents) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large. Maximum size is {MAX_IMAGE_SIZE // (1024 * 1024)} MB.",
        )

    ext = _EXT_MAP[content_type]
    rel_dir = os.path.join("locations", str(context_id))
    rel_path = os.path.join(rel_dir, f"{location_id}{ext}")
    abs_dir = os.path.join(MEDIA_ROOT, rel_dir)
    abs_path = os.path.join(MEDIA_ROOT, rel_path)

    # Remove any existing image files for this location (may differ in extension)
    existing = (
        db.query(LocationImage)
        .filter(
            LocationImage.context_id == context_id,
            LocationImage.location_id == location_id,
        )
        .first()
    )
    if existing:
        old_abs = os.path.join(MEDIA_ROOT, existing.file_path)
        if os.path.exists(old_abs):
            os.remove(old_abs)
        db.delete(existing)

    # Save file to disk
    os.makedirs(abs_dir, exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(contents)

    # Persist metadata
    record = LocationImage(
        context_id=context_id,
        location_id=location_id,
        filename=file.filename or f"{location_id}{ext}",
        content_type=content_type,
        file_path=rel_path,
    )
    db.add(record)
    db.commit()

    url = f"/media/{rel_path}"
    return {"url": url}


@router.delete("/locations/{context_id}/{location_id}/image", status_code=204)
def delete_location_image(
    context_id: uuid.UUID,
    location_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete the image attached to a location."""
    from app.games.zone_stalkers.models import LocationImage

    existing = (
        db.query(LocationImage)
        .filter(
            LocationImage.context_id == context_id,
            LocationImage.location_id == location_id,
        )
        .first()
    )
    if not existing:
        raise HTTPException(status_code=404, detail="No image found for this location")

    abs_path = os.path.join(MEDIA_ROOT, existing.file_path)
    if os.path.exists(abs_path):
        os.remove(abs_path)

    db.delete(existing)
    db.commit()


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
    # Use load_context_state so we always act on the latest state (Redis > DB).
    from app.core.state_cache.service import load_context_state, save_context_state
    zone_state = load_context_state(zone_ctx.id, zone_ctx)
    zone_state.setdefault("active_events", []).append(str(event_ctx.id))
    # force_persist=True: user-initiated creation must be immediately durable.
    save_context_state(zone_ctx.id, zone_state, zone_ctx, force_persist=True)
    db.commit()

    return event_ctx
