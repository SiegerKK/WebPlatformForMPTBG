"""
Zone Stalkers game-specific API endpoints.

These routes live here (not in app/core) because they contain game-specific
domain knowledge (zone_map, zone_event context types, etc.) that must not
pollute the generic platform core.
"""
import io
import json
import os
import uuid
import zipfile
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.core.contexts.schemas import GameContextCreate, GameContextRead
from app.core.contexts.service import create_context
from app.database import get_db

router = APIRouter(tags=["zone_stalkers"])
ProjectionModeParam = Literal["zone-lite", "game", "debug-map", "debug-map-lite", "full"]

# ── Media configuration ────────────────────────────────────────────────────────

MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/app/media")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_MEDIA_CLEANUP_DEPTH = 4

_EXT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


# ── Location image upload / delete ────────────────────────────────────────────
def _normalize_media_url(rel_path: str) -> str:
    return "/media/" + rel_path.replace(os.sep, "/")


def _safe_location_segment(location_id: str) -> str:
    if not location_id or location_id in {".", ".."} or "/" in location_id or "\\" in location_id:
        raise HTTPException(status_code=400, detail="Invalid location_id for media path")
    return location_id


def _resolve_media_path(rel_path: str) -> str:
    media_root_abs = os.path.realpath(MEDIA_ROOT)
    candidate_abs = os.path.realpath(os.path.join(media_root_abs, rel_path))
    if os.path.commonpath([media_root_abs, candidate_abs]) != media_root_abs:
        raise ValueError("media path escapes MEDIA_ROOT")
    return candidate_abs


def _abs_media_path(rel_path: str) -> str | None:
    try:
        return _resolve_media_path(rel_path)
    except ValueError:
        return None


def _safe_remove_media_file(abs_path: str | None) -> str | None:
    if not abs_path:
        return None
    media_root_abs = os.path.realpath(MEDIA_ROOT)
    try:
        rel_path = os.path.relpath(abs_path, media_root_abs)
    except ValueError:
        return None
    safe_abs = _abs_media_path(rel_path)
    if safe_abs is None:
        return None
    try:
        os.remove(safe_abs)
    except FileNotFoundError:
        return None
    return safe_abs


def _delete_location_images(records: list) -> None:
    removed_abs_paths: list[str] = []
    for existing in records:
        old_abs = _abs_media_path(existing.file_path)
        removed = _safe_remove_media_file(old_abs)
        if removed:
            removed_abs_paths.append(removed)
    _cleanup_parent_dirs(removed_abs_paths)


def _cleanup_parent_dirs(abs_paths: list[str]) -> None:
    media_root_abs = os.path.realpath(MEDIA_ROOT)
    for abs_path in abs_paths:
        if not abs_path:
            continue
        loc_dir = os.path.dirname(abs_path)
        try:
            rel_dir = os.path.relpath(loc_dir, media_root_abs)
        except ValueError:
            continue
        safe_dir = _abs_media_path(rel_dir)
        if safe_dir is None:
            continue
        try:
            os.rmdir(safe_dir)
        except OSError:
            pass


def _rel_path_from_media_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    if not url.startswith("/media/"):
        return None
    rel = url.removeprefix("/media/").replace("/", os.sep)
    return rel


# Image-slot constants and helpers — shared module (P1-3)
from app.games.zone_stalkers.location_images import (
    VALID_LOCATION_IMAGE_SLOTS as _VALID_IMAGE_SLOTS,
    ORDERED_LOCATION_IMAGE_SLOTS as _ORDERED_IMAGE_SLOTS_TUPLE,
    sync_location_primary_image_url as _sync_loc_image_url,
)
_ORDERED_IMAGE_SLOTS = list(_ORDERED_IMAGE_SLOTS_TUPLE)


def _safe_slot_segment(slot: str) -> str:
    if not slot or slot not in _VALID_IMAGE_SLOTS:
        raise HTTPException(status_code=400, detail=f"Invalid slot '{slot}'. Must be one of: {sorted(_VALID_IMAGE_SLOTS)}")
    return slot


@router.post("/locations/{context_id}/{location_id}/image")
async def upload_location_image(
    context_id: uuid.UUID,
    location_id: str,
    file: UploadFile = File(...),
    slot: str = Form("clear"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload an image for a location slot.

    The image is stored on disk under locations/<context_id>/<location_id>/<slot>/<uuid>.<ext>.
    The slot defaults to "clear". Any previously stored image for the same
    ``(context_id, location_id, slot)`` triple is deleted before the new file is saved.
    The location state is updated with the new image URL in image_slots[slot] and
    image_url is synced to the primary slot.
    """
    from app.core.contexts.models import GameContext
    from app.games.zone_stalkers.models import LocationImage
    from app.core.state_cache.service import (
        invalidate_context_state,
        load_context_state,
        save_context_state,
    )

    # Validate slot
    _safe_slot_segment(slot)

    # Validate context exists
    ctx = db.query(GameContext).filter(GameContext.id == context_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")

    state = load_context_state(ctx.id, ctx)
    locations = state.get("locations", {})
    loc = locations.get(location_id)
    if not isinstance(loc, dict):
        raise HTTPException(status_code=404, detail="Location not found in zone state")
    location_segment = _safe_location_segment(location_id)

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
    image_id = uuid.uuid4().hex
    rel_dir = os.path.join("locations", str(context_id), location_segment, slot)
    rel_path = os.path.join(rel_dir, f"{image_id}{ext}")
    abs_dir = _resolve_media_path(rel_dir)
    abs_path = _resolve_media_path(rel_path)
    written_abs_path: str | None = None

    # Collect old record abs paths for deferred deletion (P0-4: never delete files before commit)
    existing_records = (
        db.query(LocationImage)
        .filter(
            LocationImage.context_id == context_id,
            LocationImage.location_id == location_id,
            LocationImage.slot == slot,
        )
        .all()
    )
    # Collect abs paths now (records may be detached after rollback)
    old_abs_paths: list[str] = []
    for r in existing_records:
        p = _abs_media_path(r.file_path)
        if p:
            old_abs_paths.append(p)

    # Step 1: write new file to disk
    os.makedirs(abs_dir, exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(contents)
    written_abs_path = abs_path

    # Step 2: update DB rows (remove old, add new) — still in pending transaction
    for existing in existing_records:
        db.delete(existing)
    if existing_records:
        db.flush()

    record = LocationImage(
        context_id=context_id,
        location_id=location_id,
        slot=slot,
        filename=file.filename or f"{image_id}{ext}",
        content_type=content_type,
        file_path=rel_path,
    )
    db.add(record)

    url = _normalize_media_url(rel_path)
    # Step 3: update in-memory state
    loc.setdefault("image_slots", {s: None for s in _ORDERED_IMAGE_SLOTS})
    loc["image_slots"][slot] = url
    if not loc.get("primary_image_slot"):
        loc["primary_image_slot"] = slot
    _sync_loc_image_url(loc)

    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["map_revision"] = int(state.get("map_revision", 0)) + 1

    # Step 4: save state (marks context_obj.state_blob dirty) then commit everything
    try:
        save_context_state(ctx.id, state, ctx, force_persist=True)
        db.commit()
    except IntegrityError:
        db.rollback()
        invalidate_context_state(ctx.id)
        cleanup_abs_paths: list[str] = []
        removed = _safe_remove_media_file(written_abs_path)
        if removed:
            cleanup_abs_paths.append(removed)
        _cleanup_parent_dirs_deep(cleanup_abs_paths)
        raise HTTPException(
            status_code=409,
            detail="Location image was updated concurrently; retry upload",
        )
    except Exception:
        db.rollback()
        invalidate_context_state(ctx.id)
        cleanup_abs_paths = []
        removed = _safe_remove_media_file(written_abs_path)
        if removed:
            cleanup_abs_paths.append(removed)
        _cleanup_parent_dirs_deep(cleanup_abs_paths)
        raise

    # Step 5: only after successful commit+state-save: delete old files
    for old_abs in old_abs_paths:
        _safe_remove_media_file(old_abs)
    _cleanup_parent_dirs_deep(old_abs_paths)

    return {
        "url": url,
        "image_url": loc.get("image_url"),
        "slot": slot,
        "primary_image_slot": loc.get("primary_image_slot"),
        "image_slots": loc.get("image_slots"),
        "location_id": location_id,
        "state_revision": state.get("state_revision"),
        "map_revision": state.get("map_revision"),
    }


def _cleanup_parent_dirs_deep(abs_paths: list[str]) -> None:
    """Walk up up to 4 directory levels trying to remove empty directories."""
    media_root_abs = os.path.realpath(MEDIA_ROOT)
    for abs_path in abs_paths:
        if not abs_path:
            continue
        current = os.path.dirname(abs_path)
        for _ in range(MAX_MEDIA_CLEANUP_DEPTH):
            if not current:
                break
            try:
                rel_dir = os.path.relpath(current, media_root_abs)
            except ValueError:
                break
            safe_dir = _abs_media_path(rel_dir)
            if safe_dir is None or safe_dir == media_root_abs:
                break
            try:
                os.rmdir(safe_dir)
            except OSError:
                break
            current = os.path.dirname(current)


@router.delete("/locations/{context_id}/{location_id}/image")
def delete_location_image(
    context_id: uuid.UUID,
    location_id: str,
    slot: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete the image(s) attached to a location and clear from zone state.

    Without ``slot``: deletes ALL slot images and clears all image state.
    With ``slot``: deletes only that slot's image; primary_image_slot falls
    back to next available slot automatically.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import (
        invalidate_context_state,
        load_context_state,
        save_context_state,
    )
    from app.games.zone_stalkers.models import LocationImage

    ctx = db.query(GameContext).filter(GameContext.id == context_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")

    state = load_context_state(ctx.id, ctx)
    loc = state.get("locations", {}).get(location_id)
    if not isinstance(loc, dict):
        raise HTTPException(status_code=404, detail="Location not found in zone state")
    _safe_location_segment(location_id)

    if slot is not None and slot not in _VALID_IMAGE_SLOTS:
        raise HTTPException(status_code=400, detail=f"Invalid slot '{slot}'")

    # Collect abs paths for deferred file deletion (P0-4: never delete files before commit)
    deferred_remove_abs: list[str] = []

    if slot is None:
        # Delete ALL slot images
        existing_records = (
            db.query(LocationImage)
            .filter(
                LocationImage.context_id == context_id,
                LocationImage.location_id == location_id,
            )
            .all()
        )
        has_image = bool(loc.get("image_url")) or bool(existing_records)
        if not has_image:
            raise HTTPException(status_code=404, detail="No image found for this location")

        # Collect abs paths and queue DB deletions
        for rec in existing_records:
            old_abs = _abs_media_path(rec.file_path)
            if old_abs:
                deferred_remove_abs.append(old_abs)
            db.delete(rec)

        # Legacy fallback: also schedule legacy file for removal
        if not existing_records:
            rel = _rel_path_from_media_url(loc.get("image_url"))
            if rel:
                old_abs = _abs_media_path(rel)
                if old_abs:
                    deferred_remove_abs.append(old_abs)

        loc["image_url"] = None
        loc["image_slots"] = {s: None for s in _ORDERED_IMAGE_SLOTS}
        loc["primary_image_slot"] = None
    else:
        # Delete only the specified slot
        existing_records = (
            db.query(LocationImage)
            .filter(
                LocationImage.context_id == context_id,
                LocationImage.location_id == location_id,
                LocationImage.slot == slot,
            )
            .all()
        )
        slot_url = (loc.get("image_slots") or {}).get(slot)
        if not existing_records and not slot_url:
            raise HTTPException(status_code=404, detail=f"No image found for slot '{slot}'")

        # Collect abs paths and queue DB deletions
        for rec in existing_records:
            old_abs = _abs_media_path(rec.file_path)
            if old_abs:
                deferred_remove_abs.append(old_abs)
            db.delete(rec)

        # Fallback: if no DB record but state has URL for this slot
        if not existing_records and slot_url:
            rel = _rel_path_from_media_url(slot_url)
            if rel:
                old_abs = _abs_media_path(rel)
                if old_abs:
                    deferred_remove_abs.append(old_abs)

        loc.setdefault("image_slots", {})
        loc["image_slots"][slot] = None
        if loc.get("primary_image_slot") == slot:
            loc["primary_image_slot"] = None
        _sync_loc_image_url(loc)

    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["map_revision"] = int(state.get("map_revision", 0)) + 1

    # save_context_state marks state_blob dirty, then db.commit() persists everything
    try:
        save_context_state(ctx.id, state, ctx, force_persist=True)
        db.commit()
    except Exception:
        db.rollback()
        invalidate_context_state(ctx.id)
        raise

    # Only after successful commit+state-save: delete files from disk
    for abs_path in deferred_remove_abs:
        _safe_remove_media_file(abs_path)
    _cleanup_parent_dirs_deep(deferred_remove_abs)

    result: dict = {
        "status": "deleted",
        "location_id": location_id,
        "image_url": loc.get("image_url"),
        "image_slots": loc.get("image_slots"),
        "primary_image_slot": loc.get("primary_image_slot"),
        "state_revision": state.get("state_revision"),
        "map_revision": state.get("map_revision"),
    }
    if slot is not None:
        result["slot"] = slot
    return result


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


@router.get("/zone-stalkers/contexts/{context_id}/projection")
def get_zone_projection(
    context_id: uuid.UUID,
    mode: ProjectionModeParam = Query(default="game"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers.projections import json_size_bytes, project_zone_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")
    state = load_context_state(ctx.id, ctx)
    projected = project_zone_state(state=state, mode=mode)
    return {
        "context_id": str(ctx.id),
        "projection_mode": mode,
        "projection_size_bytes": json_size_bytes(projected),
        "state": projected,
    }


@router.get("/zone-stalkers/debug/state-size/{context_id}")
def get_zone_state_size(
    context_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers.projections import build_zone_state_size_report

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")
    state = load_context_state(ctx.id, ctx)
    return {
        "context_id": str(ctx.id),
        **build_zone_state_size_report(state),
    }


@router.get("/zone-stalkers/debug/performance/{match_id}")
def get_zone_performance(
    match_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
):
    from app.games.zone_stalkers.performance_metrics import get_last_tick_metrics, get_tick_metrics

    match_id_str = str(match_id)
    metrics = get_tick_metrics(match_id=match_id_str, limit=limit)
    return {
        "match_id": match_id_str,
        "count": len(metrics),
        "latest": get_last_tick_metrics(match_id=match_id_str),
        "items": metrics,
    }


@router.get("/zone-stalkers/debug/hunt-search/{context_id}")
def get_hunt_debug(
    context_id: uuid.UUID,
    store: bool = Query(default=False, description="If true, persist result into state.debug"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Build and return hunt-search debug payload for the given zone_map context.

    When ``store=true``, the payload is also persisted into ``state.debug`` and
    ``debug_hunt_traces_enabled`` is set so that subsequent ticks refresh it
    automatically.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state, save_context_state
    from app.games.zone_stalkers.debug.hunt_search_debug import build_hunt_debug_payload

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")
    state = load_context_state(ctx.id, ctx)
    debug_payload = build_hunt_debug_payload(state=state, world_turn=state.get("world_turn", 0))
    if store:
        state.setdefault("debug", {}).update(debug_payload)
        state["debug_hunt_traces_enabled"] = True
        state["_debug_hunt_traces_built_turn"] = state.get("world_turn", 0)
        save_context_state(ctx.id, state, ctx)
    return {
        "context_id": str(ctx.id),
        **debug_payload,
    }



# ── Static/dynamic map split endpoints ────────────────────────────────────────

@router.get("/zone-stalkers/contexts/{context_id}/map-static")
def get_zone_map_static(
    context_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return static map data (location topology, names, terrain, connections, layout).
    This data only changes when map_revision increments — clients can cache it.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    locations = state.get("locations") or {}
    static_locs = {}
    for loc_id, loc in locations.items():
        if not isinstance(loc, dict):
            continue
        static_locs[loc_id] = {
            "id": loc.get("id"),
            "name": loc.get("name"),
            "terrain_type": loc.get("terrain_type"),
            "connections": loc.get("connections"),
            "debug_layout": loc.get("debug_layout"),
            "image_url": loc.get("image_url"),
            "image_slots": loc.get("image_slots"),
            "primary_image_slot": loc.get("primary_image_slot"),
            "region": loc.get("region"),
            "exit_zone": loc.get("exit_zone"),
        }
    return {
        "context_id": str(ctx.id),
        "map_revision": state.get("map_revision", 0),
        "debug_layout": state.get("debug_layout"),
        "locations": static_locs,
    }


@router.get("/zone-stalkers/contexts/{context_id}/map-dynamic")
def get_zone_map_dynamic(
    context_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return dynamic map data (agent positions, resource counts, anomaly activity,
    world time). Clients refresh this on zone_delta messages.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    locations = state.get("locations") or {}
    agents_raw = state.get("agents") or {}

    # Lite agent info for map display
    agents_lite = {
        agent_id: {
            "id": a.get("id"),
            "name": a.get("name"),
            "location_id": a.get("location_id"),
            "is_alive": a.get("is_alive"),
            "has_left_zone": a.get("has_left_zone"),
            "archetype": a.get("archetype"),
            "faction": a.get("faction"),
            "controller": a.get("controller"),
        }
        for agent_id, a in agents_raw.items()
        if isinstance(a, dict)
    }

    # Per-location dynamic fields
    dynamic_locs = {}
    for loc_id, loc in locations.items():
        if not isinstance(loc, dict):
            continue
        artifacts = loc.get("artifacts") or []
        items = loc.get("items") or []
        dynamic_locs[loc_id] = {
            "id": loc.get("id"),
            "agents": loc.get("agents", []),
            "artifacts_count": len(artifacts),
            "items_count": len(items),
            "anomaly_activity": loc.get("anomaly_activity"),
            "dominant_anomaly_type": loc.get("dominant_anomaly_type"),
        }

    return {
        "context_id": str(ctx.id),
        "state_revision": state.get("state_revision", 0),
        "world_turn": state.get("world_turn"),
        "world_day": state.get("world_day"),
        "world_hour": state.get("world_hour"),
        "world_minute": state.get("world_minute"),
        "emission_active": state.get("emission_active"),
        "emission_scheduled_turn": state.get("emission_scheduled_turn"),
        "locations": dynamic_locs,
        "agents": agents_lite,
    }


# ── Scoped debug endpoints ─────────────────────────────────────────────────────

@router.get("/zone-stalkers/contexts/{context_id}/debug/hunt-search")
def get_debug_hunt_search(
    context_id: uuid.UUID,
    hunter_id: str = Query(default=None),
    target_id: str = Query(default=None),
    min_confidence: float = Query(default=0.0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return compact hunt_search_by_agent summary.

    Optional filters: hunter_id, target_id, min_confidence.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    debug = state.get("debug") or {}
    hsba = debug.get("hunt_search_by_agent") or {}

    # Build on-demand if cache is empty
    if not hsba:
        try:
            from app.games.zone_stalkers.debug.hunt_search_debug import build_hunt_debug_payload
            from app.core.state_cache.service import save_context_state
            state = build_hunt_debug_payload(state=state, world_turn=state.get("world_turn", 0))
            state["debug_hunt_traces_enabled"] = True
            state["_debug_hunt_traces_built_turn"] = state.get("world_turn", 0)
            save_context_state(ctx.id, state, ctx)
            debug = state.get("debug") or {}
            hsba = debug.get("hunt_search_by_agent") or {}
        except Exception:
            pass  # Return empty result gracefully

    result = {}
    count = 0
    for agent_id, v in hsba.items():
        if count >= limit:
            break
        if hunter_id and agent_id != hunter_id:
            continue
        if not isinstance(v, dict):
            continue
        if target_id and v.get("target_id") != target_id:
            continue
        conf = v.get("best_location_confidence") or 0.0
        if conf < min_confidence:
            continue
        result[agent_id] = {
            "target_id": v.get("target_id"),
            "best_location_id": v.get("best_location_id"),
            "best_location_confidence": conf,
            "lead_count": v.get("lead_count"),
        }
        count += 1

    return {
        "context_id": str(ctx.id),
        "state_revision": state.get("state_revision", 0),
        "total_agents": len(hsba),
        "returned": len(result),
        "hunt_search_by_agent": result,
    }


@router.get("/zone-stalkers/contexts/{context_id}/debug/hunt-search/agents/{agent_id}")
def get_debug_hunt_search_agent(
    context_id: uuid.UUID,
    agent_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return full hunt_search data for one specific agent."""
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    debug = state.get("debug") or {}
    hsba = debug.get("hunt_search_by_agent") or {}

    # Build on-demand if cache is empty
    if not hsba:
        try:
            from app.games.zone_stalkers.debug.hunt_search_debug import build_hunt_debug_payload
            from app.core.state_cache.service import save_context_state
            state = build_hunt_debug_payload(state=state, world_turn=state.get("world_turn", 0))
            state["debug_hunt_traces_enabled"] = True
            state["_debug_hunt_traces_built_turn"] = state.get("world_turn", 0)
            save_context_state(ctx.id, state, ctx)
            debug = state.get("debug") or {}
            hsba = debug.get("hunt_search_by_agent") or {}
        except Exception:
            pass  # Return empty result gracefully

    entry = hsba.get(agent_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No hunt search data for agent {agent_id}")

    leads = entry.get("leads") or []
    return {
        "context_id": str(ctx.id),
        "agent_id": agent_id,
        "state_revision": state.get("state_revision", 0),
        "target_id": entry.get("target_id"),
        "best_location_id": entry.get("best_location_id"),
        "best_location_confidence": entry.get("best_location_confidence"),
        "possible_locations": entry.get("possible_locations"),
        "likely_routes": entry.get("likely_routes"),
        "exhausted_locations": entry.get("exhausted_locations"),
        "lead_count": entry.get("lead_count"),
        "leads": leads[:30],
    }


@router.get("/zone-stalkers/contexts/{context_id}/debug/hunt-search/locations/{location_id}")
def get_debug_hunt_search_location(
    context_id: uuid.UUID,
    location_id: str,
    hunter_id: str = Query(default=None),
    target_id: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return hunt traces for one location, with optional filters."""
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    debug = state.get("debug") or {}
    lht = debug.get("location_hunt_traces") or {}

    # Build on-demand if cache is empty
    if not lht:
        try:
            from app.games.zone_stalkers.debug.hunt_search_debug import build_hunt_debug_payload
            from app.core.state_cache.service import save_context_state
            state = build_hunt_debug_payload(state=state, world_turn=state.get("world_turn", 0))
            state["debug_hunt_traces_enabled"] = True
            state["_debug_hunt_traces_built_turn"] = state.get("world_turn", 0)
            save_context_state(ctx.id, state, ctx)
            debug = state.get("debug") or {}
            lht = debug.get("location_hunt_traces") or {}
        except Exception:
            pass  # Return empty result gracefully

    loc_trace = lht.get(location_id)
    if loc_trace is None:
        return {
            "context_id": str(ctx.id),
            "location_id": location_id,
            "records": [],
            "records_count": 0,
        }

    records = loc_trace.get("records") or loc_trace.get("positive_leads") or []
    if hunter_id:
        records = [r for r in records if isinstance(r, dict) and r.get("hunter_id") == hunter_id]
    if target_id:
        records = [r for r in records if isinstance(r, dict) and r.get("target_id") == target_id]

    return {
        "context_id": str(ctx.id),
        "location_id": location_id,
        "state_revision": state.get("state_revision", 0),
        "records_count": len(records),
        "records": records[:limit],
    }


@router.get("/zone-stalkers/contexts/{context_id}/debug/hunt-search/targets/{target_id}")
def get_debug_hunt_search_target(
    context_id: uuid.UUID,
    target_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return hunt search data for all hunters targeting target_id."""
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    debug = state.get("debug") or {}
    hsba = debug.get("hunt_search_by_agent") or {}

    # Build on-demand if cache is empty
    if not hsba:
        try:
            from app.games.zone_stalkers.debug.hunt_search_debug import build_hunt_debug_payload
            from app.core.state_cache.service import save_context_state
            state = build_hunt_debug_payload(state=state, world_turn=state.get("world_turn", 0))
            state["debug_hunt_traces_enabled"] = True
            state["_debug_hunt_traces_built_turn"] = state.get("world_turn", 0)
            save_context_state(ctx.id, state, ctx)
            debug = state.get("debug") or {}
            hsba = debug.get("hunt_search_by_agent") or {}
        except Exception:
            pass  # Return empty result gracefully

    result = {}
    for agent_id, v in hsba.items():
        if len(result) >= limit:
            break
        if not isinstance(v, dict):
            continue
        if v.get("target_id") != target_id:
            continue
        result[agent_id] = {
            "target_id": v.get("target_id"),
            "best_location_id": v.get("best_location_id"),
            "best_location_confidence": v.get("best_location_confidence"),
            "lead_count": v.get("lead_count"),
            "possible_locations": (v.get("possible_locations") or [])[:5],
            "exhausted_locations": (v.get("exhausted_locations") or [])[:10],
        }

    return {
        "context_id": str(ctx.id),
        "target_id": target_id,
        "state_revision": state.get("state_revision", 0),
        "hunters": result,
    }


@router.post("/zone-stalkers/contexts/{context_id}/debug/hunt-search/refresh")
def refresh_debug_hunt_search(
    context_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Trigger a rebuild of hunt debug payload for the context.
    Sets debug_hunt_traces_enabled=True and rebuilds immediately.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state, save_context_state
    from app.games.zone_stalkers.debug.hunt_search_debug import build_hunt_debug_payload

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    debug_payload = build_hunt_debug_payload(state=state, world_turn=state.get("world_turn", 0))
    state.setdefault("debug", {}).update(debug_payload)
    state["debug_hunt_traces_enabled"] = True
    state["_debug_hunt_traces_built_turn"] = state.get("world_turn", 0)
    save_context_state(ctx.id, state, ctx)

    return {
        "context_id": str(ctx.id),
        "status": "refreshed",
        "world_turn": state.get("world_turn", 0),
    }


# ── NPC logs ZIP export ────────────────────────────────────────────────────────

_NPC_LOG_AGENT_KEYS = (
    "id",
    "name",
    "faction",
    "location_id",
    "is_alive",
    "hp",
    "money",
    "global_goal",
    "current_goal",
    "memory",
    "memory_v3",
    "brain_v3_context",
    "brain_runtime",
    "active_plan_v3",
    "knowledge_v1",
    "_v2_context",
    "controller",
)


def _extract_npc_log(agent: dict) -> dict:
    """Return only the log-relevant fields for one agent."""
    return {k: agent[k] for k in _NPC_LOG_AGENT_KEYS if k in agent}


@router.get("/zone-stalkers/contexts/{context_id}/debug/npc-logs-export")
def export_npc_logs(
    context_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export debug logs and decision history for all NPC agents as a ZIP archive.

    Each agent with ``controller.kind == 'bot'`` (or any agent when no
    controller is set) gets its own JSON file inside the archive named
    ``<agent_id>_<agent_name>.json``.  A top-level ``_summary.json`` file
    provides context metadata and an index of all included agents.

    The response streams the ZIP directly so no temporary files are written to disk.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    ctx = db.query(GameContext).filter(
        GameContext.id == context_id,
        GameContext.context_type == "zone_map",
    ).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="zone_map context not found")

    state = load_context_state(ctx.id, ctx)
    agents_raw: dict = state.get("agents") or {}
    world_turn: int = int(state.get("world_turn") or 0)
    world_day: int = int(state.get("world_day") or 0)

    # Build in-memory ZIP
    buf = io.BytesIO()
    agent_index: list[dict] = []
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for agent_id, agent in agents_raw.items():
            if not isinstance(agent, dict):
                continue
            agent_name = str(agent.get("name") or agent_id)
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_name)
            filename = f"{agent_id}_{safe_name}.json"
            log_data = _extract_npc_log(agent)
            zf.writestr(filename, json.dumps(log_data, ensure_ascii=False, indent=2, default=str))
            agent_index.append({
                "agent_id": agent_id,
                "name": agent_name,
                "file": filename,
                "is_alive": bool(agent.get("is_alive")),
                "controller": (agent.get("controller") or {}).get("kind", "unknown"),
            })

        summary = {
            "context_id": str(ctx.id),
            "exported_at_turn": world_turn,
            "world_day": world_day,
            "agent_count": len(agent_index),
            "agents": agent_index,
        }
        zf.writestr("_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

    buf.seek(0)
    filename_header = f"npc_logs_context_{context_id}_turn_{world_turn}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename_header}"'},
    )
