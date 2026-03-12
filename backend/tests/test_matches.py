def test_create_match(test_client, auth_headers):
    response = test_client.post("/api/matches", json={
        "game_id": "test_game",
        "config": {}
    }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["game_id"] == "test_game"
    assert data["status"] == "waiting_for_players"

def test_list_matches(test_client, auth_headers):
    test_client.post("/api/matches", json={"game_id": "game1"}, headers=auth_headers)
    test_client.post("/api/matches", json={"game_id": "game2"}, headers=auth_headers)
    response = test_client.get("/api/matches")
    assert response.status_code == 200
    assert len(response.json()) >= 2

def test_join_match(test_client, auth_headers):
    create_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = create_resp.json()["id"]
    test_client.post("/api/auth/register", json={"username": "player2", "email": "p2@example.com", "password": "pass123"})
    login_resp = test_client.post("/api/auth/login", data={"username": "player2", "password": "pass123"})
    headers2 = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}
    response = test_client.post(f"/api/matches/{match_id}/join", json={}, headers=headers2)
    assert response.status_code == 200
    assert response.json()["role"] == "player"

def test_start_match(test_client, auth_headers):
    create_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = create_resp.json()["id"]
    response = test_client.post(f"/api/matches/{match_id}/start", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "active"

# ── Close room tests ──────────────────────────────────────────────────────────

def test_creator_can_close_match(test_client, auth_headers):
    """The match creator can archive (close) their own match."""
    create_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = create_resp.json()["id"]
    response = test_client.delete(f"/api/matches/{match_id}", headers=auth_headers)
    assert response.status_code == 204
    # Match should now be archived
    get_resp = test_client.get(f"/api/matches/{match_id}")
    assert get_resp.json()["status"] == "archived"

def test_non_creator_cannot_close_match(test_client, auth_headers):
    """A regular user who is not the creator cannot close someone else's match."""
    create_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = create_resp.json()["id"]
    # Register a second user
    test_client.post("/api/auth/register", json={
        "username": "other_user", "email": "other@example.com", "password": "pass123"
    })
    login_resp = test_client.post("/api/auth/login", data={"username": "other_user", "password": "pass123"})
    headers2 = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}
    response = test_client.delete(f"/api/matches/{match_id}", headers=headers2)
    assert response.status_code == 403

def test_admin_can_close_any_match(test_client, auth_headers, db_session):
    """A superuser (admin) can close a match even if they are not the creator."""
    # Create a match as the regular test user
    create_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = create_resp.json()["id"]

    # Register + promote an admin
    test_client.post("/api/auth/register", json={
        "username": "adminclose", "email": "adminclose@example.com", "password": "adminpass"
    })
    from app.core.auth.models import User as UserModel
    admin = db_session.query(UserModel).filter(UserModel.username == "adminclose").first()
    admin.is_superuser = True
    db_session.commit()
    login_resp = test_client.post("/api/auth/login", data={"username": "adminclose", "password": "adminpass"})
    admin_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    # Admin deletes the match created by someone else
    response = test_client.delete(f"/api/matches/{match_id}", headers=admin_headers)
    assert response.status_code == 204
    get_resp = test_client.get(f"/api/matches/{match_id}")
    assert get_resp.json()["status"] == "archived"
