def test_create_context(test_client, auth_headers):
    match_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = match_resp.json()["id"]
    response = test_client.post("/api/contexts", json={
        "match_id": match_id,
        "context_type": "galaxy_map",
        "config": {}
    }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["context_type"] == "galaxy_map"
    assert data["match_id"] == match_id

def test_create_child_context(test_client, auth_headers):
    match_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = match_resp.json()["id"]
    parent_resp = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "root"}, headers=auth_headers)
    parent_id = parent_resp.json()["id"]
    response = test_client.post("/api/contexts", json={
        "match_id": match_id,
        "parent_id": parent_id,
        "context_type": "tactical_battle"
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["parent_id"] == parent_id

def test_get_context_tree(test_client, auth_headers):
    match_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = match_resp.json()["id"]
    test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "root"}, headers=auth_headers)
    test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "child"}, headers=auth_headers)
    response = test_client.get(f"/api/matches/{match_id}/contexts")
    assert response.status_code == 200
    assert len(response.json()) >= 2
