def _setup_context(test_client, auth_headers):
    match_resp = test_client.post("/api/matches", json={"game_id": "test_game"}, headers=auth_headers)
    match_id = match_resp.json()["id"]
    ctx_resp = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "battle"}, headers=auth_headers)
    return match_id, ctx_resp.json()["id"]

def test_get_turn(test_client, auth_headers):
    match_id, context_id = _setup_context(test_client, auth_headers)
    test_client.post(f"/api/contexts/{context_id}/turn/advance", headers=auth_headers)
    response = test_client.get(f"/api/contexts/{context_id}/turn")
    assert response.status_code == 200
    assert "turn_number" in response.json()

def test_submit_turn(test_client, auth_headers):
    match_id, context_id = _setup_context(test_client, auth_headers)
    test_client.post(f"/api/contexts/{context_id}/turn/advance", headers=auth_headers)
    response = test_client.post(f"/api/contexts/{context_id}/turn/submit", headers=auth_headers)
    assert response.status_code == 200

def test_turn_advance(test_client, auth_headers):
    match_id, context_id = _setup_context(test_client, auth_headers)
    adv_resp = test_client.post(f"/api/contexts/{context_id}/turn/advance", headers=auth_headers)
    assert adv_resp.status_code == 200
    assert adv_resp.json()["turn_number"] == 1
    adv_resp2 = test_client.post(f"/api/contexts/{context_id}/turn/advance", headers=auth_headers)
    assert adv_resp2.status_code == 200
    assert adv_resp2.json()["turn_number"] == 2
