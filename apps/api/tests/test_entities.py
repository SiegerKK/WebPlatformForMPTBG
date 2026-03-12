def test_create_entity(test_client, auth_headers):
    match_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = match_resp.json()["id"]
    ctx_resp = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "battle"}, headers=auth_headers)
    context_id = ctx_resp.json()["id"]
    response = test_client.post("/api/entities", json={
        "context_id": context_id,
        "archetype": "space_marine",
        "components": {"position": {"x": 0, "y": 0}, "stats": {"hp": 10}},
        "tags": ["infantry"]
    }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["archetype"] == "space_marine"

def test_get_entities_in_context(test_client, auth_headers):
    match_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = match_resp.json()["id"]
    ctx_resp = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "battle"}, headers=auth_headers)
    context_id = ctx_resp.json()["id"]
    test_client.post("/api/entities", json={"context_id": context_id, "archetype": "unit1"}, headers=auth_headers)
    test_client.post("/api/entities", json={"context_id": context_id, "archetype": "unit2"}, headers=auth_headers)
    response = test_client.get(f"/api/contexts/{context_id}/entities")
    assert response.status_code == 200
    assert len(response.json()) >= 2
