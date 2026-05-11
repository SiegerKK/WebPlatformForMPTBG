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

    return {"context_id": context_id, "location_id": location_id}


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

    monkeypatch.setattr("sqlalchemy.orm.session.Session.commit", _integrity_error)

    response = test_client.post(
        f"/api/locations/{context_id}/{location_id}/image",
        headers=auth_headers,
        files={"file": ("img.jpg", b"img-data", "image/jpeg")},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Location image was updated concurrently; retry upload"

    loc_dir = tmp_path / "locations" / str(context_id) / location_id
    assert (not loc_dir.exists()) or (not any(loc_dir.iterdir()))
    rows = (
        db_session.query(LocationImage)
        .filter(LocationImage.context_id == context_id, LocationImage.location_id == location_id)
        .all()
    )
    assert rows == []
