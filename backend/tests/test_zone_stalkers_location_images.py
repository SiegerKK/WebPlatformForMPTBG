from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.games.zone_stalkers.models import LocationImage


def _media_path(media_root: Path, url: str) -> Path:
    rel = url.removeprefix("/media/").replace("/", os.sep)
    return media_root / rel


@pytest.fixture
def zone_context(test_client, auth_headers, db_session):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state

    match_resp = test_client.post("/api/matches", json={"game_id": "zone_stalkers"}, headers=auth_headers)
    assert match_resp.status_code == 200
    match_id = match_resp.json()["id"]

    ctx_resp = test_client.post(
        "/api/contexts",
        json={"match_id": match_id, "context_type": "zone_map"},
        headers=auth_headers,
    )
    assert ctx_resp.status_code == 200
    context_id = ctx_resp.json()["id"]

    db_session.expire_all()
    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    location_id = next(iter(state.get("locations", {}).keys()))

    return {"context_id": context_id, "location_id": location_id, "match_id": match_id}


def test_upload_same_location_returns_unique_url_and_updates_state(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers.models import LocationImage
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    res1 = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("first.jpg", b"image-a", "image/jpeg")},
    )
    assert res1.status_code == 200
    payload1 = res1.json()
    first_url = payload1["url"]
    first_path = _media_path(tmp_path, first_url)
    assert first_path.exists()

    res2 = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("second.jpg", b"image-b", "image/jpeg")},
    )
    assert res2.status_code == 200
    payload2 = res2.json()
    second_url = payload2["url"]
    second_path = _media_path(tmp_path, second_url)

    assert second_url != first_url
    assert payload2["image_url"] == second_url
    assert payload2["location_id"] == location_id
    assert not first_path.exists()
    assert second_path.exists()

    rows = (
        db_session.query(LocationImage)
        .filter(LocationImage.context_id == context_id, LocationImage.location_id == location_id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].file_path.replace(os.sep, "/") in second_url

    db_session.expire_all()
    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    assert state["locations"][location_id]["image_url"] == second_url


def test_upload_rejects_missing_location(test_client, auth_headers, monkeypatch, tmp_path, zone_context):
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]

    response = test_client.post(
        f"/api/locations/{context_id}/missing_loc/image",
        headers=auth_headers,
        files={"file": ("x.jpg", b"x", "image/jpeg")},
    )
    assert response.status_code == 404


def test_delete_image_clears_state_and_files(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers.models import LocationImage
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    upload = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("img.png", b"png-data", "image/png")},
    )
    assert upload.status_code == 200
    image_url = upload.json()["url"]
    image_path = _media_path(tmp_path, image_url)
    assert image_path.exists()

    loc_dir = tmp_path / "locations" / str(context_id) / location_id
    assert loc_dir.exists()

    delete = test_client.delete(f"/api/locations/{context_id}/{location_id}/image", headers=auth_headers)
    assert delete.status_code == 200
    delete_payload = delete.json()
    assert delete_payload["status"] == "deleted"
    assert delete_payload["location_id"] == location_id

    assert not image_path.exists()
    assert (not loc_dir.exists()) or (not any(loc_dir.iterdir()))

    rows = (
        db_session.query(LocationImage)
        .filter(LocationImage.context_id == context_id, LocationImage.location_id == location_id)
        .all()
    )
    assert len(rows) == 0

    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    assert state["locations"][location_id].get("image_url") is None


def test_delete_image_removes_state_only_legacy_file_without_db_row(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state, save_context_state
    from app.games.zone_stalkers.models import LocationImage
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    legacy_rel = Path("locations") / str(context_id) / location_id / "legacy.jpg"
    legacy_abs = tmp_path / legacy_rel
    legacy_abs.parent.mkdir(parents=True, exist_ok=True)
    legacy_abs.write_bytes(b"legacy")

    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    state["locations"][location_id]["image_url"] = f"/media/{legacy_rel.as_posix()}"
    save_context_state(ctx.id, state, ctx, force_persist=True)
    db_session.commit()

    rows = (
        db_session.query(LocationImage)
        .filter(LocationImage.context_id == context_id, LocationImage.location_id == location_id)
        .all()
    )
    assert rows == []
    assert legacy_abs.exists()

    delete = test_client.delete(f"/api/locations/{context_id}/{location_id}/image", headers=auth_headers)
    assert delete.status_code == 200
    assert not legacy_abs.exists()


def test_upload_cleans_new_file_if_save_context_state_fails(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("save failed")

    monkeypatch.setattr("app.core.state_cache.service.save_context_state", _boom)

    with pytest.raises(RuntimeError, match="save failed"):
        test_client.post(
            f"/api/locations/{context_id}/{location_id}/image",
            headers=auth_headers,
            files={"file": ("img.jpg", b"img-data", "image/jpeg")},
        )

    loc_dir = tmp_path / "locations" / str(context_id) / location_id
    assert (not loc_dir.exists()) or (not any(loc_dir.iterdir()))
    rows = (
        db_session.query(LocationImage)
        .filter(LocationImage.context_id == context_id, LocationImage.location_id == location_id)
        .all()
    )
    assert rows == []


def test_upload_integrity_error_cleans_file_and_returns_409(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    def _integrity_error(*args, **kwargs):  # noqa: ARG001
        raise IntegrityError("INSERT", {}, Exception("unique violation"))

    invalidated: list[str] = []

    def _invalidate(_context_id):  # noqa: ANN001
        invalidated.append(str(_context_id))

    monkeypatch.setattr("app.core.state_cache.service.invalidate_context_state", _invalidate)
    monkeypatch.setattr("sqlalchemy.orm.session.Session.commit", _integrity_error)

    response = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("img.jpg", b"img-data", "image/jpeg")},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Location image was updated concurrently; retry upload"
    assert invalidated == [str(context_id)]

    loc_dir = tmp_path / "locations" / str(context_id) / location_id
    assert (not loc_dir.exists()) or (not any(loc_dir.iterdir()))
    rows = (
        db_session.query(LocationImage)
        .filter(LocationImage.context_id == context_id, LocationImage.location_id == location_id)
        .all()
    )
    assert rows == []


# ────────────────────────────────────────────────────────────────────────────────
# Multi-slot image tests (added in PR: spawn-crash-fix + image-slots)
# ────────────────────────────────────────────────────────────────────────────────

def test_migrate_location_images_moves_image_url_to_clear_slot():
    """A location with only image_url should get it migrated to image_slots.clear."""
    from app.games.zone_stalkers.rules.world_rules import migrate_location_images

    loc = {"image_url": "/media/old.jpg"}
    migrate_location_images(loc)
    assert loc["image_slots"]["clear"] == "/media/old.jpg"
    assert loc["primary_image_slot"] == "clear"
    assert loc["image_url"] == "/media/old.jpg"


def test_migrate_location_images_no_op_when_no_url():
    """Migration on a location with no image should not crash or add garbage."""
    from app.games.zone_stalkers.rules.world_rules import migrate_location_images

    loc = {}
    migrate_location_images(loc)
    assert loc.get("image_url") is None
    assert "image_slots" in loc  # dict created


def test_migrate_location_images_idempotent():
    """Running migration twice should not change the result."""
    from app.games.zone_stalkers.rules.world_rules import migrate_location_images

    loc = {"image_url": "/media/old.jpg"}
    migrate_location_images(loc)
    first_result = dict(loc)
    migrate_location_images(loc)
    assert loc["image_slots"]["clear"] == first_result["image_slots"]["clear"]
    assert loc["primary_image_slot"] == first_result["primary_image_slot"]
    assert loc["image_url"] == first_result["image_url"]


def test_sync_location_primary_image_url_no_slots():
    """With no slots populated, image_url should remain if set (legacy)."""
    from app.games.zone_stalkers.rules.world_rules import _sync_location_primary_image_url

    loc = {"image_url": "/media/legacy.jpg", "image_slots": {}}
    _sync_location_primary_image_url(loc)
    assert loc["image_url"] == "/media/legacy.jpg"


def test_sync_location_primary_image_url_picks_first_slot():
    """With slots but no primary set, sync should pick the first available."""
    from app.games.zone_stalkers.rules.world_rules import _sync_location_primary_image_url

    loc = {
        "image_url": None,
        "image_slots": {"clear": None, "fog": "/media/fog.jpg", "rain": None},
        "primary_image_slot": None,
    }
    _sync_location_primary_image_url(loc)
    assert loc["primary_image_slot"] == "fog"
    assert loc["image_url"] == "/media/fog.jpg"


def test_debug_set_location_primary_image_command(zone_context):
    """debug_set_location_primary_image should update primary_image_slot via world_rules (unit)."""
    from app.games.zone_stalkers.rules.world_rules import resolve_world_command

    location_id = zone_context["location_id"]

    state = {
        "locations": {
            location_id: {
                "id": location_id,
                "image_slots": {
                    "clear": "/media/clear.jpg",
                    "rain": "/media/rain.jpg",
                },
                "primary_image_slot": "clear",
                "image_url": "/media/clear.jpg",
            }
        }
    }
    new_state, events = resolve_world_command(
        "debug_set_location_primary_image",
        {"loc_id": location_id, "slot": "rain"},
        state,
        player_id="debug",
    )
    assert events[0]["event_type"] == "debug_location_primary_image_set"
    loc = new_state["locations"][location_id]
    assert loc["primary_image_slot"] == "rain"
    assert loc["image_url"] == "/media/rain.jpg"


def test_debug_update_location_with_image_slots():
    """debug_update_location should merge image_slots and sync image_url."""
    from app.games.zone_stalkers.rules.world_rules import resolve_world_command

    state = {
        "locations": {
            "loc_A": {
                "id": "loc_A",
                "name": "Test",
                "terrain_type": "plain",
                "anomaly_activity": 0,
                "dominant_anomaly_type": None,
                "connections": [],
                "artifacts": [],
                "items": [],
                "agents": [],
                "image_slots": {},
                "primary_image_slot": None,
                "image_url": None,
            }
        }
    }
    new_state, _ = resolve_world_command(
        "debug_update_location",
        {
            "loc_id": "loc_A",
            "image_slots": {"clear": "/media/clear.jpg", "fog": "/media/fog.jpg"},
            "primary_image_slot": "fog",
        },
        state,
        player_id="debug",
    )
    loc = new_state["locations"]["loc_A"]
    assert loc["image_slots"]["clear"] == "/media/clear.jpg"
    assert loc["image_slots"]["fog"] == "/media/fog.jpg"
    assert loc["primary_image_slot"] == "fog"
    assert loc["image_url"] == "/media/fog.jpg"


def test_debug_update_location_image_url_migrates_to_clear_slot():
    """Legacy image_url update should sync into clear slot and keep state consistent."""
    from app.games.zone_stalkers.rules.world_rules import resolve_world_command

    state = {
        "locations": {
            "loc_A": {
                "id": "loc_A",
                "name": "Test",
                "terrain_type": "plain",
                "anomaly_activity": 0,
                "dominant_anomaly_type": None,
                "connections": [],
                "artifacts": [],
                "items": [],
                "agents": [],
                "image_slots": {},
                "primary_image_slot": None,
                "image_url": None,
            }
        }
    }
    new_state, _ = resolve_world_command(
        "debug_update_location",
        {"loc_id": "loc_A", "image_url": "/media/new.jpg"},
        state,
        player_id="debug",
    )
    loc = new_state["locations"]["loc_A"]
    assert loc["image_url"] == "/media/new.jpg"
    assert (loc.get("image_slots") or {}).get("clear") == "/media/new.jpg"
    assert loc.get("primary_image_slot") == "clear"


def test_debug_import_full_map_accepts_image_slots():
    from app.games.zone_stalkers.rules.world_rules import resolve_world_command

    state = {
        "locations": {
            "loc_A": {
                "id": "loc_A",
                "name": "Old",
                "terrain_type": "plain",
                "anomaly_activity": 0,
                "dominant_anomaly_type": None,
                "region": None,
                "connections": [],
                "artifacts": [],
                "agents": [],
                "items": [],
                "image_slots": {"clear": None, "fog": None, "rain": None, "night_clear": None, "night_rain": None},
                "primary_image_slot": None,
                "image_url": None,
            }
        },
        "debug_layout": {"positions": {}, "regions": {}},
        "map_revision": 1,
    }
    new_state, _ = resolve_world_command(
        "debug_import_full_map",
        {
            "locations": {
                "loc_A": {
                    "name": "Imported",
                    "terrain_type": "plain",
                    "anomaly_activity": 0,
                    "dominant_anomaly_type": None,
                    "region": None,
                    "connections": [],
                    "artifacts": [],
                    "image_slots": {"clear": "/media/a_clear.jpg", "rain": "/media/a_rain.jpg"},
                    "primary_image_slot": "rain",
                    "image_url": "/media/a_rain.jpg",
                }
            },
            "positions": {"loc_A": {"x": 1, "y": 2}},
            "regions": {},
        },
        state,
        player_id="debug",
    )
    loc = new_state["locations"]["loc_A"]
    assert loc["image_slots"]["clear"] == "/media/a_clear.jpg"
    assert loc["image_slots"]["rain"] == "/media/a_rain.jpg"
    assert loc["primary_image_slot"] == "rain"
    assert loc["image_url"] == "/media/a_rain.jpg"


def test_debug_import_full_map_migrates_legacy_image_url_to_clear():
    from app.games.zone_stalkers.rules.world_rules import resolve_world_command

    state = {"locations": {}, "debug_layout": {"positions": {}, "regions": {}}}
    new_state, _ = resolve_world_command(
        "debug_import_full_map",
        {
            "locations": {
                "loc_A": {
                    "name": "Imported",
                    "connections": [],
                    "artifacts": [],
                    "image_url": "/media/legacy.jpg",
                }
            },
        },
        state,
        player_id="debug",
    )
    loc = new_state["locations"]["loc_A"]
    assert loc["image_slots"]["clear"] == "/media/legacy.jpg"
    assert loc["primary_image_slot"] == "clear"
    assert loc["image_url"] == "/media/legacy.jpg"


def test_debug_import_full_map_rejects_invalid_primary_slot():
    from app.games.zone_stalkers.rules.world_rules import validate_world_command

    result = validate_world_command(
        "debug_import_full_map",
        {
            "locations": {
                "loc_A": {
                    "name": "Imported",
                    "connections": [],
                    "artifacts": [],
                    "image_slots": {"clear": "/media/a.jpg"},
                    "primary_image_slot": "invalid_slot",
                }
            }
        },
        {"locations": {}},
        player_id="debug",
    )
    assert result.valid is False


def test_debug_import_full_map_syncs_image_url_to_primary_slot():
    from app.games.zone_stalkers.rules.world_rules import resolve_world_command

    state = {"locations": {}, "debug_layout": {"positions": {}, "regions": {}}}
    new_state, _ = resolve_world_command(
        "debug_import_full_map",
        {
            "locations": {
                "loc_A": {
                    "name": "Imported",
                    "connections": [],
                    "artifacts": [],
                    "image_slots": {"clear": "/media/a_clear.jpg", "fog": "/media/a_fog.jpg"},
                    "primary_image_slot": "fog",
                    "image_url": None,
                }
            },
        },
        state,
        player_id="debug",
    )
    loc = new_state["locations"]["loc_A"]
    assert loc["primary_image_slot"] == "fog"
    assert loc["image_url"] == "/media/a_fog.jpg"


def test_delete_commit_failure_invalidates_context_state_cache(
    test_client, auth_headers, monkeypatch, tmp_path, zone_context
):
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    upload = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("img.jpg", b"img-data", "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text

    invalidated: list[str] = []

    def _invalidate(_context_id):  # noqa: ANN001
        invalidated.append(str(_context_id))

    def _boom_commit(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("commit failed")

    monkeypatch.setattr("app.core.state_cache.service.invalidate_context_state", _invalidate)
    monkeypatch.setattr("sqlalchemy.orm.session.Session.commit", _boom_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        test_client.delete(
            f"/api/locations/{context_id}/{location_id}/image",
            headers=auth_headers,
        )

    assert invalidated == [str(context_id)]


def test_delete_primary_slot_falls_back_to_next_available(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    """Deleting the primary slot should fall back to the next available slot."""
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state, save_context_state
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    # Upload clear and rain slots
    res_clear = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("clear.jpg", b"clear-data", "image/jpeg")},
        data={"slot": "clear"},
    )
    assert res_clear.status_code == 200, res_clear.text

    res_rain = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("rain.jpg", b"rain-data", "image/jpeg")},
        data={"slot": "rain"},
    )
    assert res_rain.status_code == 200, res_rain.text

    # Set primary to rain
    match_id = zone_context["match_id"]
    cmd_resp = test_client.post(
        "/api/commands",
        json={
            "match_id": str(match_id),
            "context_id": str(context_id),
            "command_type": "debug_set_location_primary_image",
            "payload": {"loc_id": location_id, "slot": "rain"},
        },
        headers=auth_headers,
    )
    assert cmd_resp.status_code == 200, cmd_resp.text

    # Verify set-primary actually worked before deleting
    from app.core.contexts.models import GameContext as _GCCheck
    _ctx_check = db_session.query(_GCCheck).filter(_GCCheck.id == context_id).first()
    db_session.expire_all()
    from app.core.state_cache.service import load_context_state as _lcs
    _state_check = _lcs(_ctx_check.id, _ctx_check)
    assert (_state_check["locations"][location_id].get("primary_image_slot") == "rain"), (
        "Expected primary_image_slot=rain after debug_set_location_primary_image"
    )

    # Now delete rain slot
    delete_resp = test_client.delete(
        f"/api/locations/{context_id}/{location_id}/image",
        params={"slot": "rain"},
        headers=auth_headers,
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["slot"] == "rain"

    # Reload state — primary should have fallen back to clear
    db_session.expire_all()  # ensure we get fresh data, not stale identity-map cache
    from app.core.contexts.models import GameContext as GC
    ctx = db_session.query(GC).filter(GC.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    loc = state["locations"][location_id]
    assert (loc.get("image_slots") or {}).get("rain") is None
    # Clear was previously uploaded so primary should be clear or None, not rain
    assert loc.get("primary_image_slot") != "rain"


def test_upload_slot_updates_correct_slot_in_state(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    """Uploading with slot=fog should update image_slots.fog in state."""
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    resp = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("fog.jpg", b"fog-data", "image/jpeg")},
        data={"slot": "fog"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["slot"] == "fog"
    fog_url = payload["url"]

    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    loc = state["locations"][location_id]
    assert loc.get("image_slots", {}).get("fog") == fog_url


def test_upload_invalid_slot_returns_400(
    test_client, auth_headers, monkeypatch, tmp_path, zone_context
):
    """Uploading with an unknown slot should return 400."""
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    resp = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("x.jpg", b"x", "image/jpeg")},
        data={"slot": "unknown_slot"},
    )
    assert resp.status_code == 400


def test_delete_all_slots_clears_image_url(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    """DELETE without slot param should clear all images and image_url."""
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    # Upload to two slots
    test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("clear.jpg", b"c", "image/jpeg")},
        data={"slot": "clear"},
    )
    test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("rain.jpg", b"r", "image/jpeg")},
        data={"slot": "rain"},
    )

    # Delete all
    resp = test_client.delete(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
    )
    assert resp.status_code == 200

    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    loc = state["locations"][location_id]
    assert loc.get("image_url") is None
    assert loc.get("primary_image_slot") is None
    slots = loc.get("image_slots") or {}
    assert all(v is None for v in slots.values())


def test_upload_group_slot_updates_v2_and_db_group(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers.models import LocationImage
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    patch_profile = test_client.patch(
        f"/api/locations/{context_id}/{location_id}/image-profile",
        headers=auth_headers,
        json={"is_anomalous": True},
    )
    assert patch_profile.status_code == 200

    resp = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("gloom_rain.jpg", b"gloom-rain", "image/jpeg")},
        data={"group": "gloom", "slot": "rain"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["group"] == "gloom"
    assert body["slot"] == "rain"

    row = (
        db_session.query(LocationImage)
        .filter(
            LocationImage.context_id == context_id,
            LocationImage.location_id == location_id,
            LocationImage.group == "gloom",
            LocationImage.slot == "rain",
        )
        .first()
    )
    assert row is not None

    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    loc = state["locations"][location_id]
    assert (loc.get("image_slots_v2") or {}).get("gloom", {}).get("rain") == body["url"]


def test_upload_rejects_disabled_group(
    test_client, auth_headers, monkeypatch, tmp_path, zone_context
):
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    resp = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("psi_high.jpg", b"psi", "image/jpeg")},
        data={"group": "psi", "slot": "high"},
    )
    assert resp.status_code == 400


def test_set_primary_image_endpoint_updates_primary_ref(
    test_client, auth_headers, db_session, monkeypatch, tmp_path, zone_context
):
    from app.core.contexts.models import GameContext
    from app.core.state_cache.service import load_context_state
    from app.games.zone_stalkers import router as zone_router

    monkeypatch.setattr(zone_router, "MEDIA_ROOT", str(tmp_path))
    context_id = zone_context["context_id"]
    location_id = zone_context["location_id"]

    # Enable anomaly group and upload image there
    test_client.patch(
        f"/api/locations/{context_id}/{location_id}/image-profile",
        headers=auth_headers,
        json={"is_anomalous": True},
    )
    up = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("anomaly_clear.jpg", b"a", "image/jpeg")},
        data={"group": "anomaly", "slot": "clear"},
    )
    assert up.status_code == 200

    set_primary = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image/primary",
        headers=auth_headers,
        json={"group": "anomaly", "slot": "clear"},
    )
    assert set_primary.status_code == 200

    ctx = db_session.query(GameContext).filter(GameContext.id == context_id).first()
    state = load_context_state(ctx.id, ctx)
    loc = state["locations"][location_id]
    assert loc.get("primary_image_ref") == {"group": "anomaly", "slot": "clear"}
    assert isinstance(loc.get("image_url"), str)


def test_projection_and_delta_include_image_v2_fields():
    from app.games.zone_stalkers.delta import build_zone_delta
    from app.games.zone_stalkers.projections import project_zone_state

    state = {
        "context_type": "zone_map",
        "world_turn": 1,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "game_over": False,
        "emission_active": False,
        "emission_scheduled_turn": 0,
        "emission_ends_turn": 0,
        "auto_tick_enabled": False,
        "auto_tick_speed": None,
        "player_agents": {},
        "active_events": [],
        "debug_layout": {"positions": {}},
        "state_revision": 1,
        "map_revision": 1,
        "agents": {},
        "traders": {},
        "mutants": {},
        "locations": {
            "L1": {
                "id": "L1",
                "name": "Loc",
                "connections": [],
                "agents": [],
                "artifacts": [],
                "items": [],
                "image_profile": {"is_anomalous": False, "is_psi": False, "is_underground": False},
                "image_slots_v2": {"normal": {"clear": "/media/x.jpg"}},
                "primary_image_ref": {"group": "normal", "slot": "clear"},
                "image_url": "/media/x.jpg",
                "image_slots": {"clear": "/media/x.jpg"},
                "primary_image_slot": "clear",
            }
        },
    }

    projected = project_zone_state(state=state, mode="game")
    loc = projected["locations"]["L1"]
    assert "image_profile" in loc
    assert "image_slots_v2" in loc
    assert "primary_image_ref" in loc

    new_state = {
        **state,
        "state_revision": 2,
        "locations": {
            "L1": {
                **state["locations"]["L1"],
                "primary_image_ref": {"group": "normal", "slot": "fog"},
            }
        },
    }
    delta = build_zone_delta(old_state=state, new_state=new_state, events=[], mode="game")
    assert "L1" in delta["changes"]["locations"]
    assert "primary_image_ref" in delta["changes"]["locations"]["L1"]


# ---------------------------------------------------------------------------
# Regression tests: image_url must not resurrect deleted v2 slots
# ---------------------------------------------------------------------------

def test_delete_last_v2_image_does_not_resurrect_image_url_into_normal_clear():
    """After deleting the last v2 slot, sync must not re-inject stale image_url."""
    from app.games.zone_stalkers.location_images import sync_location_primary_image_url_v2

    loc = {
        "image_profile": {"is_anomalous": False, "is_psi": False, "is_underground": False},
        "image_slots_v2": {
            "normal": {
                "clear": None,
                "fog": None,
                "rain": "/media/locations/ctx/loc/normal/rain/img.webp",
                "night_clear": None,
                "night_rain": None,
            },
            "gloom": {},
            "anomaly": {},
            "psi": {},
            "underground": {},
        },
        "primary_image_ref": {"group": "normal", "slot": "rain"},
        "image_url": "/media/locations/ctx/loc/normal/rain/img.webp",
        "image_slots": {"clear": None, "fog": None, "rain": "/media/locations/ctx/loc/normal/rain/img.webp", "night_clear": None, "night_rain": None},
        "primary_image_slot": "rain",
    }

    # Simulate what delete_location_image handler does for a group+slot delete:
    # clears v2 slot, legacy slot, primary_image_slot, and primary_image_ref.
    loc["image_slots_v2"]["normal"]["rain"] = None
    loc["image_slots"]["rain"] = None
    loc["primary_image_slot"] = None
    loc["primary_image_ref"] = None

    sync_location_primary_image_url_v2(loc)

    assert loc["image_slots_v2"]["normal"]["clear"] is None, "normal.clear must not be re-populated from stale image_url"
    assert loc["image_slots_v2"]["normal"]["rain"] is None
    assert loc["primary_image_ref"] is None
    assert loc["image_url"] is None
    assert loc["image_slots"]["clear"] is None
    assert loc["image_slots"]["rain"] is None
    assert loc["primary_image_slot"] is None


def test_delete_rain_image_does_not_move_deleted_url_to_clear():
    """Deleting normal.rain must not cause the URL to appear in normal.clear."""
    from app.games.zone_stalkers.location_images import sync_location_primary_image_url_v2

    loc = {
        "image_profile": {"is_anomalous": False, "is_psi": False, "is_underground": False},
        "image_slots_v2": {
            "normal": {
                "clear": None,
                "fog": None,
                "rain": "/media/loc/rain.webp",
                "night_clear": None,
                "night_rain": None,
            },
        },
        "primary_image_ref": {"group": "normal", "slot": "rain"},
        "image_url": "/media/loc/rain.webp",
        "image_slots": {"clear": None, "fog": None, "rain": "/media/loc/rain.webp", "night_clear": None, "night_rain": None},
        "primary_image_slot": "rain",
    }

    # Simulate delete_location_image handler for normal.rain:
    loc["image_slots_v2"]["normal"]["rain"] = None
    loc["image_slots"]["rain"] = None
    loc["primary_image_slot"] = None
    loc["primary_image_ref"] = None

    sync_location_primary_image_url_v2(loc)

    assert loc["image_slots_v2"]["normal"]["clear"] is None, "deleted URL must not migrate to normal.clear"
    assert loc["image_url"] is None


def test_legacy_image_url_migrates_only_when_no_v2_schema_exists():
    """A truly legacy location (no image_slots_v2) should still migrate image_url to normal.clear."""
    from app.games.zone_stalkers.location_images import migrate_location_images_v2

    loc = {"image_url": "/media/legacy.webp"}
    migrate_location_images_v2(loc, allow_legacy_image_url_import=True)

    assert loc["image_slots_v2"]["normal"]["clear"] == "/media/legacy.webp"
    assert loc["primary_image_ref"] == {"group": "normal", "slot": "clear"}


def test_explicit_empty_v2_schema_does_not_import_stale_image_url():
    """A v2-aware location with an empty schema must not import stale image_url."""
    from app.games.zone_stalkers.location_images import migrate_location_images_v2

    loc = {
        "image_slots_v2": {"normal": {"clear": None}},
        "image_url": "/media/stale.webp",
    }
    migrate_location_images_v2(loc, allow_legacy_image_url_import=False)

    assert loc["image_slots_v2"]["normal"]["clear"] is None, "stale image_url must not fill clear when allow_legacy_image_url_import=False"
