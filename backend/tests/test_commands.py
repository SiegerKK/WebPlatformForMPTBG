def test_submit_command(test_client, auth_headers):
    match_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = match_resp.json()["id"]
    ctx_resp = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "battle"}, headers=auth_headers)
    context_id = ctx_resp.json()["id"]
    response = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": context_id,
        "command_type": "end_turn",
        "payload": {}
    }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "resolved"

def test_command_auth_check(test_client):
    response = test_client.post("/api/commands", json={
        "match_id": "00000000-0000-0000-0000-000000000001",
        "context_id": "00000000-0000-0000-0000-000000000002",
        "command_type": "end_turn",
        "payload": {}
    })
    assert response.status_code == 401
