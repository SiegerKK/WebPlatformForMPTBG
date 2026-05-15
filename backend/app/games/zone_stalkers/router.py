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


# Image-slot constants and helpers — shared module
from app.games.zone_stalkers.location_images import (
    VALID_LOCATION_IMAGE_SLOTS as _VALID_IMAGE_SLOTS,
    VALID_LOCATION_IMAGE_GROUPS as _VALID_IMAGE_GROUPS,
    ORDERED_LOCATION_IMAGE_SLOTS as _ORDERED_IMAGE_SLOTS_TUPLE,
    LOCATION_IMAGE_GROUP_SLOT_MAP as _GROUP_SLOT_MAP,
    normalize_location_image_profile as _normalize_loc_image_profile,
    sync_location_primary_image_url as _sync_loc_image_url,
    migrate_location_images as _migrate_loc_images,
    validate_image_group_slot as _validate_group_slot,
    is_group_enabled_for_location as _is_group_enabled_for_location,
)
_ORDERED_IMAGE_SLOTS = list(_ORDERED_IMAGE_SLOTS_TUPLE)


def _safe_slot_segment(slot: str) -> str:
    if not slot or slot not in _VALID_IMAGE_SLOTS:
        raise HTTPException(status_code=400, detail=f"Invalid slot '{slot}'. Must be one of: {sorted(_VALID_IMAGE_SLOTS)}")
    return slot


def _safe_group_segment(group: str) -> str:
    if not group or group not in _VALID_IMAGE_GROUPS:
        raise HTTPException(status_code=400, detail=f"Invalid group '{group}'. Must be one of: {sorted(_VALID_IMAGE_GROUPS)}")
    return group


def _safe_group_slot(group: str, slot: str) -> tuple[str, str]:
    _safe_group_segment(group)
    if not _validate_group_slot(group, slot):
        raise HTTPException(status_code=400, detail=f"Invalid slot '{slot}' for group '{group}'")
    return group, slot


def _legacy_slot_for_group(group: str, slot: str) -> str | None:
    if group == "normal" and slot in _VALID_IMAGE_SLOTS:
        return slot
    return None


@router.post("/locations/{context_id}/{location_id}/image")
async def upload_location_image(
    context_id: uuid.UUID,
    location_id: str,
    file: UploadFile = File(...),
    group: str = Form("normal"),
    slot: str = Form("clear"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload an image for a location slot.

    The image is stored on disk under
    locations/<context_id>/<location_id>/<group>/<slot>/<uuid>.<ext>.
    Defaults are group="normal", slot="clear". Existing image for the same
    ``(context_id, location_id, group, slot)`` is replaced.
    """
    from app.core.contexts.models import GameContext
    from app.games.zone_stalkers.models import LocationImage
    from app.core.state_cache.service import (
        invalidate_context_state,
        load_context_state,
        save_context_state,
    )

    # Validate group+slot
    group, slot = _safe_group_slot(group, slot)

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
    _migrate_loc_images(loc)

    if not _is_group_enabled_for_location(loc, group):
        raise HTTPException(status_code=400, detail=f"Group '{group}' is disabled for this location profile")

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
    rel_dir = os.path.join("locations", str(context_id), location_segment, group, slot)
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
            LocationImage.group == group,
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
        group=group,
        slot=slot,
        filename=file.filename or f"{image_id}{ext}",
        content_type=content_type,
        file_path=rel_path,
    )
    db.add(record)

    url = _normalize_media_url(rel_path)
    # Step 3: update in-memory state
    slots_v2 = loc.setdefault("image_slots_v2", {})
    slots_v2.setdefault(group, {})[slot] = url

    legacy_slot = _legacy_slot_for_group(group, slot)
    if legacy_slot is not None:
        loc.setdefault("image_slots", {s: None for s in _ORDERED_IMAGE_SLOTS})
        loc["image_slots"][legacy_slot] = url
        if not loc.get("primary_image_slot"):
            loc["primary_image_slot"] = legacy_slot

    if not loc.get("primary_image_ref"):
        loc["primary_image_ref"] = {"group": group, "slot": slot}
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
        "group": group,
        "slot": slot,
        "primary_image_ref": loc.get("primary_image_ref"),
        "image_slots_v2": loc.get("image_slots_v2"),
        "primary_image_slot": loc.get("primary_image_slot"),
        "image_slots": loc.get("image_slots"),
        "image_profile": loc.get("image_profile"),
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
    group: str | None = Query(default=None),
    slot: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete location images.

    - No group/slot: delete all images for location.
    - group only: delete all images in the group.
    - group+slot: delete one image slot in that group.
    """
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import (
        invalidate_context_state,
        load_context_state,
        save_context_state,
    )
    from app.games.zone_stalkers.models import LocationImage

    if slot is not None and group is None:
        group = "normal"

    if group is not None:
        _safe_group_segment(group)
    if slot is not None:
        if group is None:
            raise HTTPException(status_code=400, detail="group is required when slot is provided")
        _safe_group_slot(group, slot)

    ctx = db.query(GameContext).filter(GameContext.id == context_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")

    state = load_context_state(ctx.id, ctx)
    loc = state.get("locations", {}).get(location_id)
    if not isinstance(loc, dict):
        raise HTTPException(status_code=404, detail="Location not found in zone state")
    _safe_location_segment(location_id)
    _migrate_loc_images(loc)

    query = db.query(LocationImage).filter(
        LocationImage.context_id == context_id,
        LocationImage.location_id == location_id,
    )
    if group is not None:
        query = query.filter(LocationImage.group == group)
    if slot is not None:
        query = query.filter(LocationImage.slot == slot)

    existing_records = query.all()
    deferred_remove_abs: list[str] = []

    if not existing_records and group is None and slot is None and not loc.get("image_url"):
        raise HTTPException(status_code=404, detail="No image found for this location")

    for rec in existing_records:
        old_abs = _abs_media_path(rec.file_path)
        if old_abs:
            deferred_remove_abs.append(old_abs)
        db.delete(rec)

    if not existing_records and group is None and slot is None:
        rel = _rel_path_from_media_url(loc.get("image_url"))
        if rel:
            old_abs = _abs_media_path(rel)
            if old_abs:
                deferred_remove_abs.append(old_abs)

    if group is None and slot is None:
        loc["image_slots_v2"] = {}
        loc["image_slots"] = {s: None for s in _ORDERED_IMAGE_SLOTS}
        loc["primary_image_ref"] = None
        loc["primary_image_slot"] = None
        loc["image_url"] = None
    elif group is not None and slot is None:
        slots_v2 = loc.setdefault("image_slots_v2", {})
        group_slots = slots_v2.get(group)
        if isinstance(group_slots, dict):
            for k in list(group_slots.keys()):
                group_slots[k] = None
        if group == "normal":
            loc.setdefault("image_slots", {s: None for s in _ORDERED_IMAGE_SLOTS})
            for s in _ORDERED_IMAGE_SLOTS:
                loc["image_slots"][s] = None
            loc["primary_image_slot"] = None
        if isinstance(loc.get("primary_image_ref"), dict) and loc["primary_image_ref"].get("group") == group:
            loc["primary_image_ref"] = None
        _sync_loc_image_url(loc)
    else:
        assert group is not None and slot is not None
        slots_v2 = loc.setdefault("image_slots_v2", {})
        slots_v2.setdefault(group, {})[slot] = None

        legacy_slot = _legacy_slot_for_group(group, slot)
        if legacy_slot is not None:
            loc.setdefault("image_slots", {s: None for s in _ORDERED_IMAGE_SLOTS})
            loc["image_slots"][legacy_slot] = None
            if loc.get("primary_image_slot") == legacy_slot:
                loc["primary_image_slot"] = None

        if isinstance(loc.get("primary_image_ref"), dict):
            ref = loc["primary_image_ref"]
            if ref.get("group") == group and ref.get("slot") == slot:
                loc["primary_image_ref"] = None
        _sync_loc_image_url(loc)

    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["map_revision"] = int(state.get("map_revision", 0)) + 1

    try:
        save_context_state(ctx.id, state, ctx, force_persist=True)
        db.commit()
    except Exception:
        db.rollback()
        invalidate_context_state(ctx.id)
        raise

    for abs_path in deferred_remove_abs:
        _safe_remove_media_file(abs_path)
    _cleanup_parent_dirs_deep(deferred_remove_abs)

    result: dict[str, object] = {
        "status": "deleted",
        "location_id": location_id,
        "group": group,
        "slot": slot,
        "image_profile": loc.get("image_profile"),
        "image_slots_v2": loc.get("image_slots_v2"),
        "primary_image_ref": loc.get("primary_image_ref"),
        "image_url": loc.get("image_url"),
        "image_slots": loc.get("image_slots"),
        "primary_image_slot": loc.get("primary_image_slot"),
        "state_revision": state.get("state_revision"),
        "map_revision": state.get("map_revision"),
    }
    return result


class LocationPrimaryImagePayload(BaseModel):
    group: str = "normal"
    slot: str


@router.post("/locations/{context_id}/{location_id}/image/primary")
def set_location_primary_image(
    context_id: uuid.UUID,
    location_id: str,
    payload: LocationPrimaryImagePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import (
        invalidate_context_state,
        load_context_state,
        save_context_state,
    )

    group, slot = _safe_group_slot(payload.group, payload.slot)

    ctx = db.query(GameContext).filter(GameContext.id == context_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")

    state = load_context_state(ctx.id, ctx)
    loc = state.get("locations", {}).get(location_id)
    if not isinstance(loc, dict):
        raise HTTPException(status_code=404, detail="Location not found in zone state")

    _migrate_loc_images(loc)
    slots_v2 = (loc.get("image_slots_v2") or {}).get(group)
    if not isinstance(slots_v2, dict) or not slots_v2.get(slot):
        raise HTTPException(status_code=404, detail=f"No image found for {group}.{slot}")

    loc["primary_image_ref"] = {"group": group, "slot": slot}
    legacy_slot = _legacy_slot_for_group(group, slot)
    if legacy_slot is not None:
        loc["primary_image_slot"] = legacy_slot
    _sync_loc_image_url(loc)

    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["map_revision"] = int(state.get("map_revision", 0)) + 1

    try:
        save_context_state(ctx.id, state, ctx, force_persist=True)
        db.commit()
    except Exception:
        db.rollback()
        invalidate_context_state(ctx.id)
        raise

    return {
        "location_id": location_id,
        "primary_image_ref": loc.get("primary_image_ref"),
        "image_slots_v2": loc.get("image_slots_v2"),
        "image_profile": loc.get("image_profile"),
        "image_url": loc.get("image_url"),
        "image_slots": loc.get("image_slots"),
        "primary_image_slot": loc.get("primary_image_slot"),
        "state_revision": state.get("state_revision"),
        "map_revision": state.get("map_revision"),
    }


class LocationImageProfilePatch(BaseModel):
    is_anomalous: bool | None = None
    is_psi: bool | None = None
    is_underground: bool | None = None


@router.patch("/locations/{context_id}/{location_id}/image-profile")
def patch_location_image_profile(
    context_id: uuid.UUID,
    location_id: str,
    payload: LocationImageProfilePatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import (
        invalidate_context_state,
        load_context_state,
        save_context_state,
    )

    ctx = db.query(GameContext).filter(GameContext.id == context_id).first()
    if not ctx:
        raise HTTPException(status_code=404, detail="Context not found")

    state = load_context_state(ctx.id, ctx)
    loc = state.get("locations", {}).get(location_id)
    if not isinstance(loc, dict):
        raise HTTPException(status_code=404, detail="Location not found in zone state")

    _migrate_loc_images(loc)
    profile = _normalize_loc_image_profile(loc)

    if payload.is_anomalous is not None:
        profile["is_anomalous"] = payload.is_anomalous
    if payload.is_psi is not None:
        profile["is_psi"] = payload.is_psi
    if payload.is_underground is not None:
        profile["is_underground"] = payload.is_underground

    loc["image_profile"] = profile
    _migrate_loc_images(loc)

    if isinstance(loc.get("primary_image_ref"), dict):
        current_group = str(loc["primary_image_ref"].get("group") or "")
        if not _is_group_enabled_for_location(loc, current_group):
            loc["primary_image_ref"] = None

    _sync_loc_image_url(loc)

    state["state_revision"] = int(state.get("state_revision", 0)) + 1
    state["map_revision"] = int(state.get("map_revision", 0)) + 1

    try:
        save_context_state(ctx.id, state, ctx, force_persist=True)
        db.commit()
    except Exception:
        db.rollback()
        invalidate_context_state(ctx.id)
        raise

    return {
        "location_id": location_id,
        "image_profile": loc.get("image_profile"),
        "image_slots_v2": loc.get("image_slots_v2"),
        "primary_image_ref": loc.get("primary_image_ref"),
        "image_url": loc.get("image_url"),
        "image_slots": loc.get("image_slots"),
        "primary_image_slot": loc.get("primary_image_slot"),
        "state_revision": state.get("state_revision"),
        "map_revision": state.get("map_revision"),
    }


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
            "image_profile": loc.get("image_profile"),
            "image_slots_v2": loc.get("image_slots_v2"),
            "primary_image_ref": loc.get("primary_image_ref"),
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

    from app.games.zone_stalkers.memory.memory_events import get_memory_metrics  # noqa: PLC0415

    _obs_records_written = 0
    _stalkers_seen_records_written = 0
    _corpse_seen_records_written = 0
    _target_belief_fallbacks = 0
    _context_builder_fallbacks = 0
    _memory_evictions_total = 0.0
    _memory_drops_total = 0.0
    _turns_elapsed = max(1, world_turn)  # elapsed turns (divisor for per-tick rates)
    for _, agent in agents_raw.items():
        if not isinstance(agent, dict):
            continue
        memory_v3 = agent.get("memory_v3") if isinstance(agent.get("memory_v3"), dict) else {}
        records = memory_v3.get("records") if isinstance(memory_v3, dict) else {}
        if isinstance(records, dict):
            for raw in records.values():
                if not isinstance(raw, dict):
                    continue
                details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
                action_kind = str(details.get("action_kind") or raw.get("kind") or "")
                if str(details.get("memory_type") or "") == "observation":
                    _obs_records_written += 1
                if action_kind == "stalkers_seen":
                    _stalkers_seen_records_written += 1
                if action_kind in {"corpse_seen", "target_corpse_seen", "target_corpse_reported"}:
                    _corpse_seen_records_written += 1
        stats = memory_v3.get("stats") if isinstance(memory_v3, dict) else {}
        if isinstance(stats, dict):
            _memory_evictions_total += float(stats.get("memory_evictions") or 0.0)
            _memory_drops_total += float(stats.get("memory_write_dropped") or 0.0)
        ctx_metrics = agent.get("brain_context_metrics")
        if isinstance(ctx_metrics, dict):
            _target_belief_fallbacks += int(ctx_metrics.get("target_belief_memory_fallbacks") or 0)
            _context_builder_fallbacks += int(ctx_metrics.get("context_builder_memory_fallbacks") or 0)

    runtime_memory_metrics = get_memory_metrics()
    try:
        from app.games.zone_stalkers.economy.debts import DEBT_ESCAPE_THRESHOLD, ensure_debt_ledger  # noqa: PLC0415
        debt_ledger = ensure_debt_ledger(state, world_turn=world_turn)
    except Exception:
        debt_ledger = state.get("debt_ledger") if isinstance(state.get("debt_ledger"), dict) else {}
        DEBT_ESCAPE_THRESHOLD = 5000

    accounts = (debt_ledger.get("accounts") or {}) if isinstance(debt_ledger, dict) else {}
    active_accounts = 0
    total_outstanding = 0
    survival_credit_advances = 0
    debt_payments = 0
    accounts_repaid = 0
    rollovers_total = 0
    accounts_over_escape_threshold = 0
    debt_escape_triggered_count = 0
    if isinstance(accounts, dict):
        for account in accounts.values():
            if not isinstance(account, dict):
                continue
            status = str(account.get("status") or "")
            outstanding = int(account.get("outstanding_total") or 0)
            if status == "active" and outstanding > 0:
                active_accounts += 1
                total_outstanding += outstanding
            survival_credit_advances += int(account.get("credit_advance_count") or 0)
            if int(account.get("repaid_total") or 0) > 0:
                debt_payments += 1
            if status == "repaid":
                accounts_repaid += 1
            rollovers_total += int(account.get("rollover_count") or 0)
            if outstanding >= int(DEBT_ESCAPE_THRESHOLD):
                accounts_over_escape_threshold += 1

    for _, agent in agents_raw.items():
        if not isinstance(agent, dict):
            continue
        if bool(agent.get("_debt_escape_triggered")):
            debt_escape_triggered_count += 1

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
            "knowledge_first_metrics": {
                "runtime_knowledge_only_events": int(runtime_memory_metrics.get("knowledge_only_events", 0)),
                "observation_memory_records_written": _obs_records_written,
                "stalkers_seen_memory_records_written": _stalkers_seen_records_written,
                "corpse_seen_memory_records_written": _corpse_seen_records_written,
                "target_belief_memory_fallbacks": _target_belief_fallbacks,
                "context_builder_memory_fallbacks": _context_builder_fallbacks,
                "stale_corpse_seen_ignored": int(runtime_memory_metrics.get("stale_corpse_seen_ignored", 0)),
                "corpse_seen_alive_agent_ignored": int(runtime_memory_metrics.get("corpse_seen_alive_agent_ignored", 0)),
                "memory_evictions_per_tick": round(_memory_evictions_total / _turns_elapsed, 4),
                "memory_drops_per_tick": round(_memory_drops_total / _turns_elapsed, 4),
            },
            "debt_summary": {
                "active_accounts": active_accounts,
                "total_outstanding": total_outstanding,
                "survival_credit_advances": survival_credit_advances,
                "debt_payments": debt_payments,
                "accounts_repaid": accounts_repaid,
                "rollovers_total": rollovers_total,
                "accounts_over_escape_threshold": accounts_over_escape_threshold,
                "debt_escape_triggered_count": debt_escape_triggered_count,
            },
        }
        zf.writestr("_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

    buf.seek(0)
    filename_header = f"npc_logs_context_{context_id}_turn_{world_turn}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename_header}"'},
    )
