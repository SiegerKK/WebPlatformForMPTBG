"""
End-to-end tests for the Tic-Tac-Toe game (Крестики-нолики).
Two players create a match, take turns placing marks, and one wins.
"""
import pytest


def _register_and_login(client, username, email, password):
    client.post("/api/auth/register", json={"username": username, "email": email, "password": password})
    resp = client.post("/api/auth/login", data={"username": username, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ──────────────────────────────────────────────────────
# Unit tests for TicTacToeRuleSet in isolation
# ──────────────────────────────────────────────────────

def test_ruleset_place_mark_first_player():
    from app.games.tictactoe.rules import TicTacToeRuleSet
    rs = TicTacToeRuleSet()
    result = rs.validate_command("place_mark", {"cell": 4}, {}, [], "player1")
    assert result.valid


def test_ruleset_place_mark_invalid_cell():
    from app.games.tictactoe.rules import TicTacToeRuleSet
    rs = TicTacToeRuleSet()
    result = rs.validate_command("place_mark", {"cell": 9}, {}, [], "player1")
    assert not result.valid


def test_ruleset_place_mark_occupied_cell():
    from app.games.tictactoe.rules import TicTacToeRuleSet
    rs = TicTacToeRuleSet()
    state, _ = rs.resolve_command("place_mark", {"cell": 0}, {}, [], "player1")
    result = rs.validate_command("place_mark", {"cell": 0}, state, [], "player2")
    assert not result.valid


def test_ruleset_wrong_turn():
    from app.games.tictactoe.rules import TicTacToeRuleSet
    rs = TicTacToeRuleSet()
    # player1 places first
    state, _ = rs.resolve_command("place_mark", {"cell": 0}, {}, [], "player1")
    # player2 places second (registers)
    state, _ = rs.resolve_command("place_mark", {"cell": 1}, state, [], "player2")
    # player2 tries to go again (not their turn)
    result = rs.validate_command("place_mark", {"cell": 2}, state, [], "player2")
    assert not result.valid


def test_ruleset_win_detection():
    from app.games.tictactoe.rules import TicTacToeRuleSet
    rs = TicTacToeRuleSet()
    # X wins on top row: 0, 1, 2
    state, _ = rs.resolve_command("place_mark", {"cell": 0}, {}, [], "p1")  # X:0
    state, _ = rs.resolve_command("place_mark", {"cell": 3}, state, [], "p2")  # O:3
    state, _ = rs.resolve_command("place_mark", {"cell": 1}, state, [], "p1")  # X:1
    state, _ = rs.resolve_command("place_mark", {"cell": 4}, state, [], "p2")  # O:4
    state, events = rs.resolve_command("place_mark", {"cell": 2}, state, [], "p1")  # X:2 → win
    assert state["game_over"] is True
    assert state["winner"] == "p1"
    assert state["winner_mark"] == "X"
    event_types = [e["event_type"] for e in events]
    assert "game_won" in event_types


def test_ruleset_draw_detection():
    from app.games.tictactoe.rules import TicTacToeRuleSet
    rs = TicTacToeRuleSet()
    # Board filling without a winner:
    # X O X
    # X X O
    # O X O  -> draw
    moves = [
        ("p1", 0), ("p2", 1), ("p1", 2),
        ("p1", 3), ("p1", 4), ("p2", 5),
        ("p2", 6), ("p1", 7), ("p2", 8),
    ]
    state = {}
    for player, cell in moves:
        state, events = rs.resolve_command("place_mark", {"cell": cell}, state, [], player)
    assert state["game_over"] is True
    assert state["winner"] is None
    assert any(e["event_type"] == "game_drawn" for e in events)


# ──────────────────────────────────────────────────────
# Integration tests via HTTP API
# ──────────────────────────────────────────────────────

def test_full_tictactoe_game(test_client):
    """Two players play a full game; player1 wins on the left column."""
    p1 = _register_and_login(test_client, "ttt_p1", "ttt_p1@test.com", "pass1234")
    p2 = _register_and_login(test_client, "ttt_p2", "ttt_p2@test.com", "pass1234")

    # Player 1 creates the match
    match = test_client.post("/api/matches", json={"game_id": "tictactoe", "title": "Test TTT"}, headers=p1).json()
    match_id = match["id"]

    # Player 2 joins
    test_client.post(f"/api/matches/{match_id}/join", json={}, headers=p2)

    # Player 1 starts the match
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    # Create context for the tictactoe game
    ctx = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "tictactoe"}, headers=p1).json()
    ctx_id = ctx["id"]

    def cmd(headers, cell):
        return test_client.post("/api/commands", json={
            "match_id": match_id,
            "context_id": ctx_id,
            "command_type": "place_mark",
            "payload": {"cell": cell},
        }, headers=headers)

    # Left-column win for p1: cells 0, 3, 6
    # Interleaved with p2: cells 1, 4
    assert cmd(p1, 0).status_code == 200  # p1→X:0
    assert cmd(p2, 1).status_code == 200  # p2→O:1
    assert cmd(p1, 3).status_code == 200  # p1→X:3
    assert cmd(p2, 4).status_code == 200  # p2→O:4
    r = cmd(p1, 6)                         # p1→X:6 → win
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "resolved"
    event_types = [e["event_type"] for e in data["events"]]
    assert "game_won" in event_types

    # Verify game state is saved
    ctx_state = test_client.get(f"/api/contexts/{ctx_id}", headers=p1).json()
    assert ctx_state["state_blob"]["game_over"] is True
    assert ctx_state["state_blob"]["winner_mark"] == "X"


def test_tictactoe_wrong_turn_rejected(test_client):
    """Player 2 cannot place twice in a row."""
    p1 = _register_and_login(test_client, "ttt_wt1", "ttt_wt1@test.com", "pass1234")
    p2 = _register_and_login(test_client, "ttt_wt2", "ttt_wt2@test.com", "pass1234")

    match = test_client.post("/api/matches", json={"game_id": "tictactoe"}, headers=p1).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/join", json={}, headers=p2)
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)
    ctx = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "tictactoe"}, headers=p1).json()
    ctx_id = ctx["id"]

    def cmd(headers, cell):
        return test_client.post("/api/commands", json={
            "match_id": match_id,
            "context_id": ctx_id,
            "command_type": "place_mark",
            "payload": {"cell": cell},
        }, headers=headers)

    cmd(p1, 0)  # p1 places
    cmd(p2, 1)  # p2 places
    r = cmd(p2, 2)  # p2 tries to go again
    assert r.json()["status"] == "rejected"


def test_tictactoe_occupied_cell_rejected(test_client):
    """Cannot place on an already-occupied cell."""
    p1 = _register_and_login(test_client, "ttt_oc1", "ttt_oc1@test.com", "pass1234")
    p2 = _register_and_login(test_client, "ttt_oc2", "ttt_oc2@test.com", "pass1234")

    match = test_client.post("/api/matches", json={"game_id": "tictactoe"}, headers=p1).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/join", json={}, headers=p2)
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)
    ctx = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "tictactoe"}, headers=p1).json()
    ctx_id = ctx["id"]

    def cmd(headers, cell):
        return test_client.post("/api/commands", json={
            "match_id": match_id,
            "context_id": ctx_id,
            "command_type": "place_mark",
            "payload": {"cell": cell},
        }, headers=headers)

    cmd(p1, 4)  # p1 places in centre
    r = cmd(p2, 4)  # p2 tries same cell
    assert r.json()["status"] == "rejected"


# ──────────────────────────────────────────────────────
# Tests for pre-initialized context state (bug fix for
# "both players see waiting for opponent")
# ──────────────────────────────────────────────────────

def test_tictactoe_context_preinitialized(test_client):
    """Context created for a 2-player match should have player_marks and
    current_player_id set immediately — no moves required first."""
    p1 = _register_and_login(test_client, "ttt_pi1", "ttt_pi1@test.com", "pass1234")
    p2 = _register_and_login(test_client, "ttt_pi2", "ttt_pi2@test.com", "pass1234")

    match = test_client.post("/api/matches", json={"game_id": "tictactoe"}, headers=p1).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/join", json={}, headers=p2)
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts",
        json={"match_id": match_id, "context_type": "tictactoe"},
        headers=p1,
    ).json()

    state = ctx["state_blob"]
    assert len(state["player_marks"]) == 2, "Both players should be pre-assigned marks"
    assert state["current_player_id"] is not None, "current_player_id should be set from the start"
    marks = list(state["player_marks"].values())
    assert "X" in marks and "O" in marks, "One player is X, the other is O"

    # The player with the first turn (X) should be able to place immediately
    r = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx["id"],
        "command_type": "place_mark",
        "payload": {"cell": 4},
    }, headers=p1)  # p1 created the match, so p1 is assigned X and goes first
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"


def test_tictactoe_second_player_cannot_go_first(test_client):
    """With pre-initialized state, p2 (second joiner) cannot make the first move."""
    p1 = _register_and_login(test_client, "ttt_sg1", "ttt_sg1@test.com", "pass1234")
    p2 = _register_and_login(test_client, "ttt_sg2", "ttt_sg2@test.com", "pass1234")

    match = test_client.post("/api/matches", json={"game_id": "tictactoe"}, headers=p1).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/join", json={}, headers=p2)
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)
    ctx = test_client.post(
        "/api/contexts",
        json={"match_id": match_id, "context_type": "tictactoe"},
        headers=p1,
    ).json()

    # p2 attempts to play before p1 — must be rejected
    r = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx["id"],
        "command_type": "place_mark",
        "payload": {"cell": 0},
    }, headers=p2)
    assert r.json()["status"] == "rejected"
