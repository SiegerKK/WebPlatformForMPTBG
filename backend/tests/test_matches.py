def test_create_match(test_client, auth_headers):
    response = test_client.post("/api/matches", json={
        "game_id": "test_game",
        "config": {}
    }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["game_id"] == "test_game"
    assert data["status"] == "waiting"

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
