"""
Zone Stalkers game-specific API endpoints.

These routes live here (not in app/core) because they contain game-specific
domain knowledge (zone_map, zone_event context types, etc.) that must not
pollute the generic platform core.
"""
import os
import uuid
import shutil
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
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
