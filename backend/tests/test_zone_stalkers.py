"""Tests for the Zone Stalkers game."""
import pytest


class TestZoneGenerator:
    def _gen(self, seed=42, players=1):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        return generate_zone(seed=seed, num_players=players, num_ai_stalkers=3, num_mutants=3, num_traders=1)

    def test_deterministic(self):
        s1 = self._gen(seed=1)
        s2 = self._gen(seed=1)
        assert s1["locations"] == s2["locations"]

    def test_different_seeds_differ(self):
        s1 = self._gen(seed=1)
        s2 = self._gen(seed=99)
        assert s1 != s2

    def test_minimum_locations(self):
        assert len(self._gen()["locations"]) >= 8

    def test_locations_connected(self):
        state = self._gen()
        for loc in state["locations"].values():
            assert len(loc["connections"]) >= 1

    def test_player_agents_created(self):
        state = self._gen(players=2)
        assert "agent_p0" in state["agents"]
        assert "agent_p1" in state["agents"]

    def test_context_type_field(self):
        assert self._gen()["context_type"] == "zone_map"

    def test_traders_present(self):
        assert len(self._gen()["traders"]) >= 1

    def test_mutants_present(self):
        assert len(self._gen()["mutants"]) >= 1

    def test_agent_has_required_fields(self):
        agent = self._gen()["agents"]["agent_p0"]
        for field in ("id", "archetype", "name", "location_id", "hp", "max_hp", "inventory", "equipment"):
            assert field in agent

    def test_location_connections_valid(self):
        state = self._gen()
        loc_ids = set(state["locations"].keys())
        for loc in state["locations"].values():
            for conn in loc["connections"]:
                assert conn["to"] in loc_ids


def _world_state_with_player(seed=42):
    from app.games.zone_stalkers.generators.zone_generator import generate_zone
    state = generate_zone(seed=seed, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
    state["player_agents"]["player1"] = "agent_p0"
    state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
    return state


class TestWorldRules:
    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "player1")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "player1")

    def test_end_turn_valid(self):
        assert self._v("end_turn", {}, _world_state_with_player()).valid

    def test_move_to_adjacent_valid(self):
        state = _world_state_with_player()
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        target = state["locations"][loc_id]["connections"][0]["to"]
        assert self._v("move_agent", {"target_location_id": target}, state).valid

    def test_move_to_nonexistent_invalid(self):
        state = _world_state_with_player()
        assert not self._v("move_agent", {"target_location_id": "no_loc"}, state).valid

    def test_move_updates_location(self):
        state = _world_state_with_player()
        loc_id = state["agents"]["agent_p0"]["location_id"]
        target = state["locations"][loc_id]["connections"][0]["to"]
        new_state, events = self._r("move_agent", {"target_location_id": target}, state)
        assert new_state["agents"]["agent_p0"]["location_id"] == target
        assert any(e["event_type"] == "agent_moved" for e in events)

    def test_move_updates_agents_list(self):
        state = _world_state_with_player()
        old_loc = state["agents"]["agent_p0"]["location_id"]
        target = state["locations"][old_loc]["connections"][0]["to"]
        new_state, _ = self._r("move_agent", {"target_location_id": target}, state)
        assert "agent_p0" not in new_state["locations"][old_loc]["agents"]
        assert "agent_p0" in new_state["locations"][target]["agents"]

    def test_pick_up_artifact_valid(self):
        state = _world_state_with_player()
        loc_id = state["agents"]["agent_p0"]["location_id"]
        state["locations"][loc_id]["artifacts"] = [
            {"id": "art_test", "type": "soul", "name": "Soul", "value": 2000}
        ]
        assert self._v("pick_up_artifact", {"artifact_id": "art_test"}, state).valid

    def test_pick_up_artifact_not_found(self):
        state = _world_state_with_player()
        assert not self._v("pick_up_artifact", {"artifact_id": "none"}, state).valid

    def test_pick_up_artifact_removes_from_location(self):
        state = _world_state_with_player()
        loc_id = state["agents"]["agent_p0"]["location_id"]
        state["locations"][loc_id]["artifacts"] = [
            {"id": "art_t", "type": "soul", "name": "Soul", "value": 2000}
        ]
        new_state, events = self._r("pick_up_artifact", {"artifact_id": "art_t"}, state)
        assert len(new_state["locations"][loc_id]["artifacts"]) == 0
        assert any(e["event_type"] == "artifact_picked_up" for e in events)
        assert any(i["id"] == "art_t" for i in new_state["agents"]["agent_p0"]["inventory"])

    def test_unknown_player_invalid(self):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        state = _world_state_with_player()
        assert not validate_world_command("move_agent", {}, state, "stranger").valid

    def test_second_action_blocked(self):
        state = _world_state_with_player()
        loc_id = state["agents"]["agent_p0"]["location_id"]
        target = state["locations"][loc_id]["connections"][0]["to"]
        new_state, _ = self._r("move_agent", {"target_location_id": target}, state)
        assert not self._v("move_agent", {"target_location_id": target}, new_state).valid


def _combat_state():
    return {
        "context_type": "encounter_combat",
        "combat_over": False,
        "turn_number": 1,
        "max_turns": 20,
        "active_agent_id": "stalker_0",
        "initiative_order": ["stalker_0", "mutant_0"],
        "player_agents": {"player1": "stalker_0"},
        "participants": {
            "stalker_0": {
                "id": "stalker_0", "side": "stalker", "is_alive": True,
                "hp": 100, "max_hp": 100, "defense": 5, "money": 200, "money_drop": 0,
                "inventory": [{"id": "item_medkit", "type": "medkit", "name": "First Aid Kit", "weight": 0.5, "value": 200}],
                "equipment": {"weapon": {"type": "pistol"}},
            },
            "mutant_0": {
                "id": "mutant_0", "side": "mutant", "is_alive": True,
                "hp": 40, "max_hp": 40, "defense": 0, "money_drop": 50,
                "inventory": [], "equipment": {},
            },
        },
    }


class TestCombatRules:
    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.combat_rules import validate_combat_command
        return validate_combat_command(cmd, payload, state, "player1")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.combat_rules import resolve_combat_command
        return resolve_combat_command(cmd, payload, state, "player1")

    def test_end_turn_valid(self):
        assert self._v("end_turn", {}, _combat_state()).valid

    def test_attack_valid(self):
        assert self._v("attack", {"target_id": "mutant_0"}, _combat_state()).valid

    def test_attack_nonexistent(self):
        assert not self._v("attack", {"target_id": "nobody"}, _combat_state()).valid

    def test_attack_own_side(self):
        state = _combat_state()
        state["participants"]["ally"] = {
            "id": "ally", "side": "stalker", "is_alive": True, "hp": 50, "max_hp": 50,
            "defense": 0, "money_drop": 0, "inventory": [], "equipment": {},
        }
        assert not self._v("attack", {"target_id": "ally"}, state).valid

    def test_attack_dead_target(self):
        state = _combat_state()
        state["participants"]["mutant_0"]["is_alive"] = False
        assert not self._v("attack", {"target_id": "mutant_0"}, state).valid

    def test_attack_reduces_hp(self):
        state = _combat_state()
        new_state, events = self._r("attack", {"target_id": "mutant_0"}, state)
        assert new_state["participants"]["mutant_0"]["hp"] < 40
        assert any(e["event_type"] == "attack_resolved" for e in events)

    def test_attack_kills(self):
        state = _combat_state()
        state["participants"]["mutant_0"]["hp"] = 1
        new_state, events = self._r("attack", {"target_id": "mutant_0"}, state)
        assert not new_state["participants"]["mutant_0"]["is_alive"]
        assert any(e["event_type"] == "participant_killed" for e in events)

    def test_combat_ends_on_kill(self):
        state = _combat_state()
        state["participants"]["mutant_0"]["hp"] = 1
        new_state, events = self._r("attack", {"target_id": "mutant_0"}, state)
        assert new_state["combat_over"]
        assert any(e["event_type"] == "combat_ended" for e in events)

    def test_retreat(self):
        state = _combat_state()
        new_state, events = self._r("retreat", {}, state)
        assert new_state["participants"]["stalker_0"]["retreated"]
        assert any(e["event_type"] == "agent_retreated" for e in events)

    def test_use_medkit(self):
        state = _combat_state()
        state["participants"]["stalker_0"]["hp"] = 50
        new_state, events = self._r("use_item", {"item_id": "item_medkit"}, state)
        assert new_state["participants"]["stalker_0"]["hp"] == 100
        assert any(e["event_type"] == "item_used" for e in events)

    def test_use_item_not_in_inv(self):
        assert not self._v("use_item", {"item_id": "fake"}, _combat_state()).valid

    def test_wrong_turn(self):
        state = _combat_state()
        state["active_agent_id"] = "mutant_0"
        assert not self._v("attack", {"target_id": "mutant_0"}, state).valid

    def test_combat_over_blocks(self):
        state = _combat_state()
        state["combat_over"] = True
        assert not self._v("attack", {"target_id": "mutant_0"}, state).valid


def _trade_state():
    return {
        "context_type": "trade_session",
        "trade_over": False,
        "buyer_id": "player1",
        "buyer_money": 1000,
        "trader_money": 5000,
        "buyer_inventory": [
            {"id": "item_art", "type": "soul", "name": "Soul", "weight": 0.1, "value": 2000}
        ],
        "trader_inventory": [
            {"id": "item_ak", "type": "ak74", "name": "AK-74", "weight": 3.5, "value": 1500, "stock": 2},
            {"id": "item_med", "type": "medkit", "name": "First Aid Kit", "weight": 0.5, "value": 200, "stock": 3},
        ],
    }


class TestTradeRules:
    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.trade_rules import validate_trade_command
        return validate_trade_command(cmd, payload, state, "player1")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.trade_rules import resolve_trade_command
        return resolve_trade_command(cmd, payload, state, "player1")

    def test_buy_valid(self):
        assert self._v("buy_item", {"item_id": "item_med"}, _trade_state()).valid

    def test_buy_no_money(self):
        state = _trade_state()
        state["buyer_money"] = 10
        assert not self._v("buy_item", {"item_id": "item_ak"}, state).valid

    def test_buy_not_in_stock(self):
        assert not self._v("buy_item", {"item_id": "none"}, _trade_state()).valid

    def test_buy_transfers(self):
        state = _trade_state()
        new_state, events = self._r("buy_item", {"item_id": "item_med"}, state)
        assert new_state["buyer_money"] == 800
        assert new_state["trader_money"] == 5200
        assert any(i["type"] == "medkit" for i in new_state["buyer_inventory"])
        assert any(e["event_type"] == "item_bought" for e in events)

    def test_sell_valid(self):
        assert self._v("sell_item", {"item_id": "item_art"}, _trade_state()).valid

    def test_sell_not_in_inv(self):
        assert not self._v("sell_item", {"item_id": "none"}, _trade_state()).valid

    def test_sell_transfers(self):
        state = _trade_state()
        new_state, events = self._r("sell_item", {"item_id": "item_art"}, state)
        assert new_state["buyer_money"] == 2200  # 1000 + 0.6*2000
        assert not any(i["id"] == "item_art" for i in new_state["buyer_inventory"])
        assert any(e["event_type"] == "item_sold" for e in events)

    def test_end_trade(self):
        state = _trade_state()
        new_state, events = self._r("end_trade", {}, state)
        assert new_state["trade_over"]
        assert any(e["event_type"] == "trade_ended" for e in events)

    def test_trade_over_blocks(self):
        state = _trade_state()
        state["trade_over"] = True
        assert not self._v("buy_item", {"item_id": "item_med"}, state).valid

    def test_wrong_player(self):
        from app.games.zone_stalkers.rules.trade_rules import validate_trade_command
        assert not validate_trade_command("buy_item", {"item_id": "item_med"}, _trade_state(), "other").valid


def _exploration_state():
    return {
        "context_type": "location_exploration",
        "location_id": "loc_0",
        "grid_size": 8,
        "exploration_over": False,
        "player_agents": {"player1": "agent_0"},
        "local_agents": {
            "agent_0": {
                "id": "agent_0", "is_alive": True,
                "hp": 100, "max_hp": 100,
                "position": {"x": 3, "y": 3},
                "inventory": [], "equipment": {},
            }
        },
        "local_items": [
            {"id": "item_1", "type": "medkit", "name": "Medkit",
             "position": {"x": 4, "y": 3}, "weight": 0.5, "value": 200}
        ],
        "local_anomalies": [],
        "containers": [],
    }


class TestExplorationRules:
    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.exploration_rules import validate_exploration_command
        return validate_exploration_command(cmd, payload, state, "player1")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.exploration_rules import resolve_exploration_command
        return resolve_exploration_command(cmd, payload, state, "player1")

    def test_end_turn_valid(self):
        assert self._v("end_turn", {}, _exploration_state()).valid

    def test_move_valid(self):
        assert self._v("explore_move", {"direction": "n"}, _exploration_state()).valid

    def test_move_invalid_dir(self):
        assert not self._v("explore_move", {"direction": "up"}, _exploration_state()).valid

    def test_move_out_of_bounds(self):
        state = _exploration_state()
        state["local_agents"]["agent_0"]["position"] = {"x": 0, "y": 0}
        assert not self._v("explore_move", {"direction": "n"}, state).valid

    def test_move_changes_position(self):
        state = _exploration_state()
        new_state, events = self._r("explore_move", {"direction": "e"}, state)
        assert new_state["local_agents"]["agent_0"]["position"] == {"x": 4, "y": 3}
        assert any(e["event_type"] == "agent_moved" for e in events)

    def test_pick_up_item_at_same_pos(self):
        state = _exploration_state()
        state["local_agents"]["agent_0"]["position"] = {"x": 4, "y": 3}
        assert self._v("pick_up_item", {"item_id": "item_1"}, state).valid

    def test_pick_up_item_wrong_pos(self):
        assert not self._v("pick_up_item", {"item_id": "item_1"}, _exploration_state()).valid

    def test_leave_location(self):
        state = _exploration_state()
        new_state, events = self._r("leave_location", {}, state)
        assert new_state["exploration_over"]
        assert any(e["event_type"] == "location_left" for e in events)

    def test_anomaly_damage_on_move(self):
        state = _exploration_state()
        state["local_anomalies"] = [
            {"id": "a1", "type": "electro", "position": {"x": 4, "y": 3}}
        ]
        new_state, events = self._r("explore_move", {"direction": "e"}, state)
        assert new_state["local_agents"]["agent_0"]["hp"] < 100
        assert any(e["event_type"] == "anomaly_damage" for e in events)


class TestZoneStalkerRuleSet:
    def _rs(self):
        from app.games.zone_stalkers.ruleset import ZoneStalkerRuleSet
        return ZoneStalkerRuleSet()

    def test_dispatches_zone_map(self):
        assert self._rs().validate_command("end_turn", {}, _world_state_with_player(), [], "player1").valid

    def test_dispatches_combat(self):
        assert self._rs().validate_command("end_turn", {}, _combat_state(), [], "player1").valid

    def test_dispatches_exploration(self):
        assert self._rs().validate_command("end_turn", {}, _exploration_state(), [], "player1").valid

    def test_unknown_context_end_turn(self):
        assert self._rs().validate_command("end_turn", {}, {"context_type": "other"}, [], "p1").valid

    def test_unknown_context_other_cmd(self):
        assert not self._rs().validate_command("do_x", {}, {"context_type": "other"}, [], "p1").valid


def _register_and_login(client, username, email, password):
    client.post("/api/auth/register", json={"username": username, "email": email, "password": password})
    resp = client.post("/api/auth/login", data={"username": username, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_zone_stalkers_create_context(test_client):
    p1 = _register_and_login(test_client, "zs_p1", "zs_p1@test.com", "pass1234")
    p2 = _register_and_login(test_client, "zs_p2", "zs_p2@test.com", "pass1234")

    match = test_client.post(
        "/api/matches", json={"game_id": "zone_stalkers", "title": "Test ZS"}, headers=p1
    ).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/join", json={}, headers=p2)
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=p1
    ).json()
    assert ctx["context_type"] == "zone_map"
    state = ctx["state_blob"]
    assert state["context_type"] == "zone_map"
    assert len(state["locations"]) >= 8
    assert "agents" in state


def test_zone_stalkers_player_agents_assigned(test_client):
    p1 = _register_and_login(test_client, "zs_pa1", "zs_pa1@test.com", "pass1234")
    p2 = _register_and_login(test_client, "zs_pa2", "zs_pa2@test.com", "pass1234")

    match = test_client.post(
        "/api/matches", json={"game_id": "zone_stalkers"}, headers=p1
    ).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/join", json={}, headers=p2)
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=p1
    ).json()
    assert len(ctx["state_blob"]["player_agents"]) >= 1


def test_zone_stalkers_move_command(test_client):
    p1 = _register_and_login(test_client, "zs_mv1", "zs_mv1@test.com", "pass1234")

    match = test_client.post(
        "/api/matches", json={"game_id": "zone_stalkers"}, headers=p1
    ).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=p1
    ).json()
    ctx_id = ctx["id"]
    state = ctx["state_blob"]

    player_agent_id = list(state["player_agents"].values())[0]
    agent = state["agents"][player_agent_id]
    current_loc = agent["location_id"]
    target_loc = state["locations"][current_loc]["connections"][0]["to"]

    resp = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx_id,
        "command_type": "move_agent",
        "payload": {"target_location_id": target_loc},
    }, headers=p1)

    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert any(e["event_type"] == "agent_moved" for e in resp.json()["events"])

    updated_ctx = test_client.get(f"/api/contexts/{ctx_id}", headers=p1).json()
    assert updated_ctx["state_blob"]["agents"][player_agent_id]["location_id"] == target_loc


def test_zone_stalkers_move_nonadjacent_rejected(test_client):
    p1 = _register_and_login(test_client, "zs_na1", "zs_na1@test.com", "pass1234")

    match = test_client.post(
        "/api/matches", json={"game_id": "zone_stalkers"}, headers=p1
    ).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=p1
    ).json()
    ctx_id = ctx["id"]

    resp = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx_id,
        "command_type": "move_agent",
        "payload": {"target_location_id": "nonexistent_location"},
    }, headers=p1)
    assert resp.json()["status"] == "rejected"


def test_zone_stalkers_end_turn(test_client):
    p1 = _register_and_login(test_client, "zs_et1", "zs_et1@test.com", "pass1234")

    match = test_client.post(
        "/api/matches", json={"game_id": "zone_stalkers"}, headers=p1
    ).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=p1
    ).json()
    ctx_id = ctx["id"]

    resp = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx_id,
        "command_type": "end_turn",
        "payload": {},
    }, headers=p1)
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


def test_zone_stalkers_end_turn_advances_world_clock(test_client):
    """end_turn by the sole human player should trigger a tick and advance world_turn by 1."""
    p1 = _register_and_login(test_client, "zs_et_adv1", "zs_et_adv1@test.com", "pass1234")

    match = test_client.post(
        "/api/matches", json={"game_id": "zone_stalkers"}, headers=p1
    ).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=p1
    ).json()
    ctx_id = ctx["id"]
    turn_before = ctx["state_blob"]["world_turn"]

    resp = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx_id,
        "command_type": "end_turn",
        "payload": {},
    }, headers=p1)
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"

    # Confirm world_turn advanced
    new_state = test_client.get(f"/api/contexts/{ctx_id}", headers=p1).json()["state_blob"]
    assert new_state["world_turn"] == turn_before + 1

    # Confirm at least one world_turn_advanced event was emitted
    events = resp.json().get("events", [])
    assert any(e.get("event_type") == "world_turn_advanced" for e in events)



    p1 = _register_and_login(test_client, "zs_sa1", "zs_sa1@test.com", "pass1234")

    match = test_client.post(
        "/api/matches", json={"game_id": "zone_stalkers"}, headers=p1
    ).json()
    match_id = match["id"]
    test_client.post(f"/api/matches/{match_id}/start", headers=p1)

    ctx = test_client.post(
        "/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=p1
    ).json()
    ctx_id = ctx["id"]
    state = ctx["state_blob"]

    player_agent_id = list(state["player_agents"].values())[0]
    agent = state["agents"][player_agent_id]
    current_loc = agent["location_id"]
    target_loc = state["locations"][current_loc]["connections"][0]["to"]

    resp1 = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx_id,
        "command_type": "move_agent",
        "payload": {"target_location_id": target_loc},
    }, headers=p1)
    assert resp1.json()["status"] == "resolved"

    new_state = test_client.get(f"/api/contexts/{ctx_id}", headers=p1).json()["state_blob"]
    new_loc = new_state["agents"][player_agent_id]["location_id"]
    new_connections = new_state["locations"][new_loc]["connections"]
    if not new_connections:
        pytest.skip("No connections from new location")
    next_target = new_connections[0]["to"]

    resp2 = test_client.post("/api/commands", json={
        "match_id": match_id,
        "context_id": ctx_id,
        "command_type": "move_agent",
        "payload": {"target_location_id": next_target},
    }, headers=p1)
    assert resp2.json()["status"] == "rejected"


# ─────────────────────────────────────────────────────────────────
# New field tests
# ─────────────────────────────────────────────────────────────────

class TestNewAgentFields:
    """Verify new fields added in GDD Phase-1 are present on generated agents."""

    def _agent(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=7, num_players=1, num_ai_stalkers=1, num_mutants=0, num_traders=0)
        return state["agents"]["agent_p0"]

    def test_hunger_present(self):
        agent = self._agent()
        assert "hunger" in agent
        assert 0 <= agent["hunger"] <= 100

    def test_thirst_present(self):
        agent = self._agent()
        assert "thirst" in agent
        assert 0 <= agent["thirst"] <= 100

    def test_sleepiness_present(self):
        agent = self._agent()
        assert "sleepiness" in agent
        assert 0 <= agent["sleepiness"] <= 100

    def test_action_queue_present(self):
        agent = self._agent()
        assert "action_queue" in agent
        assert isinstance(agent["action_queue"], list)
        assert agent["action_queue"] == []

    def test_experience_present(self):
        agent = self._agent()
        assert "experience" in agent
        assert agent["experience"] == 0

    def test_skills_present(self):
        agent = self._agent()
        for skill in ("skill_combat", "skill_stalker", "skill_trade", "skill_medicine", "skill_social"):
            assert skill in agent, f"{skill} missing"
            assert agent[skill] >= 1

    def test_global_goal_present(self):
        agent = self._agent()
        assert "global_goal" in agent
        assert agent["global_goal"] in ("survive", "get_rich", "explore", "serve_faction")

    def test_risk_tolerance_present(self):
        agent = self._agent()
        assert "risk_tolerance" in agent
        assert 0.0 <= agent["risk_tolerance"] <= 1.0


# ─────────────────────────────────────────────────────────────────
# consume_item command tests
# ─────────────────────────────────────────────────────────────────

class TestConsumeItem:
    """Unit tests for the consume_item zone_map command."""

    def _state_with_item(self, item_type: str = "medkit"):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state = generate_zone(seed=3, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        state["player_agents"]["player1"] = "agent_p0"
        state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
        # Give a known item in inventory
        item_info = ITEM_TYPES[item_type]
        state["agents"]["agent_p0"]["inventory"] = [{
            "id": "test_item_1", "type": item_type,
            "name": item_info["name"], "weight": item_info.get("weight", 0), "value": item_info.get("value", 0),
        }]
        return state

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "player1")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "player1")

    def test_consume_medkit_valid(self):
        state = self._state_with_item("medkit")
        assert self._v("consume_item", {"item_id": "test_item_1"}, state).valid

    def test_consume_bread_valid(self):
        state = self._state_with_item("bread")
        assert self._v("consume_item", {"item_id": "test_item_1"}, state).valid

    def test_consume_missing_item_invalid(self):
        state = self._state_with_item("medkit")
        result = self._v("consume_item", {"item_id": "no_such_item"}, state)
        assert not result.valid
        assert "inventory" in result.error.lower()

    def test_consume_weapon_invalid(self):
        state = self._state_with_item("ak74")
        result = self._v("consume_item", {"item_id": "test_item_1"}, state)
        assert not result.valid

    def test_consume_medkit_heals_hp(self):
        state = self._state_with_item("medkit")
        state["agents"]["agent_p0"]["hp"] = 50
        new_state, events = self._r("consume_item", {"item_id": "test_item_1"}, state)
        assert new_state["agents"]["agent_p0"]["hp"] == 100
        assert any(e["event_type"] == "item_consumed" for e in events)

    def test_consume_removes_from_inventory(self):
        state = self._state_with_item("medkit")
        new_state, _ = self._r("consume_item", {"item_id": "test_item_1"}, state)
        assert not any(i["id"] == "test_item_1" for i in new_state["agents"]["agent_p0"]["inventory"])

    def test_consume_bread_reduces_hunger(self):
        state = self._state_with_item("bread")
        state["agents"]["agent_p0"]["hunger"] = 80
        new_state, _ = self._r("consume_item", {"item_id": "test_item_1"}, state)
        assert new_state["agents"]["agent_p0"]["hunger"] < 80

    def test_consume_energy_drink_reduces_thirst(self):
        state = self._state_with_item("energy_drink")
        state["agents"]["agent_p0"]["thirst"] = 80
        new_state, _ = self._r("consume_item", {"item_id": "test_item_1"}, state)
        assert new_state["agents"]["agent_p0"]["thirst"] < 80

    def test_hp_not_exceed_max(self):
        state = self._state_with_item("medkit")
        state["agents"]["agent_p0"]["hp"] = 100  # already full
        new_state, _ = self._r("consume_item", {"item_id": "test_item_1"}, state)
        assert new_state["agents"]["agent_p0"]["hp"] == 100

    def test_consume_no_item_id_invalid(self):
        state = self._state_with_item()
        assert not self._v("consume_item", {}, state).valid


class TestDebugUpdateMap:
    """Tests for the debug_update_map meta-command (no player agent required)."""

    def _v(self, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command("debug_update_map", payload, state, "any_user")

    def _r(self, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command("debug_update_map", payload, state, "any_user")

    def _state(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        return generate_zone(seed=42, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)

    def test_valid_with_positions_and_connections(self):
        state = self._state()
        loc_ids = list(state["locations"].keys())
        payload = {
            "positions": {loc_ids[0]: {"x": 300, "y": 200}},
            "connections": {
                loc_id: [{"to": c["to"], "type": c.get("type", "normal")}
                          for c in loc["connections"]]
                for loc_id, loc in state["locations"].items()
            },
        }
        assert self._v(payload, state).valid

    def test_valid_with_empty_payload(self):
        assert self._v({}, self._state()).valid

    def test_valid_positions_only(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        assert self._v({"positions": {loc_id: {"x": 100, "y": 200}}}, state).valid

    def test_invalid_unknown_location_in_connections(self):
        state = self._state()
        result = self._v({
            "connections": {"nonexistent_loc": []}
        }, state)
        assert not result.valid

    def test_invalid_connection_target_not_in_locations(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v({
            "connections": {loc_id: [{"to": "nonexistent_target", "type": "normal"}]}
        }, state)
        assert not result.valid

    def test_positions_are_persisted(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        payload = {"positions": {loc_id: {"x": 123.0, "y": 456.0}}}
        new_state, _ = self._r(payload, state)
        assert new_state["debug_layout"]["positions"][loc_id] == {"x": 123.0, "y": 456.0}

    def test_connections_are_persisted(self):
        state = self._state()
        loc_ids = list(state["locations"].keys())
        a, b = loc_ids[0], loc_ids[1]
        payload = {
            "connections": {
                a: [{"to": b, "type": "normal"}],
                b: [{"to": a, "type": "normal"}],
            }
        }
        new_state, _ = self._r(payload, state)
        assert any(c["to"] == b for c in new_state["locations"][a]["connections"])
        assert any(c["to"] == a for c in new_state["locations"][b]["connections"])

    def test_debug_layout_overwrites_on_second_save(self):
        """Re-saving overwrites positions rather than merging — only saved positions survive."""
        state = self._state()
        loc_ids = list(state["locations"].keys())
        # First save: two positions
        state, _ = self._r({"positions": {loc_ids[0]: {"x": 1, "y": 1}, loc_ids[1]: {"x": 2, "y": 2}}}, state)
        # Second save: only one position
        state, _ = self._r({"positions": {loc_ids[0]: {"x": 99, "y": 99}}}, state)
        assert state["debug_layout"]["positions"][loc_ids[0]] == {"x": 99, "y": 99}
        # loc_ids[1] was NOT included in the second save — only positions explicitly sent survive
        assert loc_ids[1] not in state["debug_layout"]["positions"]

    def test_original_state_not_mutated(self):
        state = self._state()
        import copy
        original = copy.deepcopy(state)
        loc_id = next(iter(state["locations"]))
        self._r({"positions": {loc_id: {"x": 1, "y": 1}}}, state)
        assert state == original  # resolve_world_command deep-copies; original is unchanged

    def test_event_emitted(self):
        state = self._state()
        _, events = self._r({}, state)
        assert any(e["event_type"] == "debug_map_updated" for e in events)


class TestDebugLocationCommands:
    """Tests for debug_update_location and debug_create_location meta-commands."""

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "any_user")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "any_user")

    def _state(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        return generate_zone(seed=42, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)

    # ── debug_update_location ────────────────────────────────────────────────

    def test_update_location_valid(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        payload = {"loc_id": loc_id, "name": "Updated Name", "type": "ruins", "danger_level": 3}
        assert self._v("debug_update_location", payload, state).valid

    def test_update_location_name_changed(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        new_state, _ = self._r("debug_update_location", {"loc_id": loc_id, "name": "Changed", "type": "ruins", "danger_level": 2}, state)
        assert new_state["locations"][loc_id]["name"] == "Changed"

    def test_update_location_type_and_danger_level(self):
        # type and danger_level are no longer part of the location data model
        state = self._state()
        loc_id = next(iter(state["locations"]))
        new_state, _ = self._r("debug_update_location", {"loc_id": loc_id, "name": "X"}, state)
        assert "type" not in new_state["locations"][loc_id]
        assert "danger_level" not in new_state["locations"][loc_id]

    def test_update_location_event_emitted(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        _, events = self._r("debug_update_location", {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1}, state)
        assert any(e["event_type"] == "debug_location_updated" for e in events)

    def test_update_location_invalid_no_loc_id(self):
        state = self._state()
        result = self._v("debug_update_location", {"name": "X", "type": "ruins", "danger_level": 1}, state)
        assert not result.valid

    def test_update_location_invalid_bad_loc_id(self):
        state = self._state()
        result = self._v("debug_update_location", {"loc_id": "nonexistent", "name": "X", "type": "ruins", "danger_level": 1}, state)
        assert not result.valid

    def test_update_location_invalid_empty_name(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "   ", "type": "ruins", "danger_level": 1}, state)
        assert not result.valid

    def test_update_location_invalid_bad_type(self):
        # type is no longer validated; unknown type keys are silently ignored
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X"}, state)
        assert result.valid

    def test_update_location_invalid_bad_danger_level(self):
        # danger_level is no longer validated; the field is ignored
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X"}, state)
        assert result.valid

    def test_update_location_does_not_change_connections(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        orig_conns = list(state["locations"][loc_id]["connections"])
        new_state, _ = self._r("debug_update_location", {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1}, state)
        assert new_state["locations"][loc_id]["connections"] == orig_conns

    def test_update_location_original_state_not_mutated(self):
        import copy
        state = self._state()
        original = copy.deepcopy(state)
        loc_id = next(iter(state["locations"]))
        self._r("debug_update_location", {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1}, state)
        assert state == original

    # ── debug_create_location ────────────────────────────────────────────────

    def test_create_location_valid(self):
        state = self._state()
        assert self._v("debug_create_location", {"name": "CNPP", "type": "anomaly_cluster", "danger_level": 5}, state).valid

    def test_create_location_appears_in_state(self):
        state = self._state()
        new_state, _ = self._r("debug_create_location", {"name": "CNPP"}, state)
        assert len(new_state["locations"]) == len(state["locations"]) + 1
        new_ids = set(new_state["locations"].keys()) - set(state["locations"].keys())
        assert len(new_ids) == 1
        new_id = list(new_ids)[0]
        loc = new_state["locations"][new_id]
        assert loc["name"] == "CNPP"
        assert "type" not in loc
        assert "danger_level" not in loc
        assert loc["connections"] == []
        assert loc["agents"] == []

    def test_create_location_with_position_saved(self):
        state = self._state()
        payload = {"name": "CNPP", "type": "ruins", "danger_level": 4, "position": {"x": 123.0, "y": 456.0}}
        new_state, _ = self._r("debug_create_location", payload, state)
        new_id = list(set(new_state["locations"].keys()) - set(state["locations"].keys()))[0]
        assert new_state["debug_layout"]["positions"][new_id] == {"x": 123.0, "y": 456.0}

    def test_create_location_without_position_no_debug_layout_entry(self):
        state = self._state()
        new_state, _ = self._r("debug_create_location", {"name": "X", "type": "ruins", "danger_level": 1}, state)
        new_id = list(set(new_state["locations"].keys()) - set(state["locations"].keys()))[0]
        positions = new_state.get("debug_layout", {}).get("positions", {})
        assert new_id not in positions

    def test_create_location_id_no_collision(self):
        state = self._state()
        # Force existing "loc_debug_N" IDs to trigger the collision-avoidance path
        n = len(state["locations"])
        state["locations"][f"loc_debug_{n}"] = {"id": f"loc_debug_{n}", "name": "X", "type": "ruins", "danger_level": 1, "connections": [], "artifacts": [], "agents": [], "items": []}
        new_state, evts = self._r("debug_create_location", {"name": "Y", "type": "ruins", "danger_level": 2}, state)
        assert len(set(new_state["locations"].keys())) == len(new_state["locations"])  # no duplicate ids
        assert any(e["event_type"] == "debug_location_created" for e in evts)

    def test_create_location_invalid_empty_name(self):
        state = self._state()
        result = self._v("debug_create_location", {"name": "", "type": "ruins", "danger_level": 1}, state)
        assert not result.valid

    def test_create_location_invalid_bad_type(self):
        # type is no longer accepted as input; unknown type keys are silently ignored
        state = self._state()
        result = self._v("debug_create_location", {"name": "X"}, state)
        assert result.valid

    def test_create_location_invalid_danger_zero(self):
        # danger_level is no longer accepted as input; the field is ignored
        state = self._state()
        result = self._v("debug_create_location", {"name": "X"}, state)
        assert result.valid

    def test_create_location_original_state_not_mutated(self):
        import copy
        state = self._state()
        original = copy.deepcopy(state)
        self._r("debug_create_location", {"name": "X", "type": "ruins", "danger_level": 1}, state)
        assert state == original

    # ── new location fields (terrain_type, anomaly_activity, dominant_anomaly_type) ──

    def test_generator_sets_new_location_fields(self):
        state = self._state()
        for loc in state["locations"].values():
            assert "terrain_type" in loc
            assert loc["terrain_type"] in {"plain", "hills", "slag_heaps", "industrial", "buildings", "military_buildings", "hamlet", "farm", "field_camp"}
            assert "anomaly_activity" in loc
            assert isinstance(loc["anomaly_activity"], int)
            assert 0 <= loc["anomaly_activity"] <= 10
            assert "dominant_anomaly_type" in loc  # may be None

    def test_update_location_terrain_type(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        payload = {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1, "terrain_type": "hills"}
        new_state, _ = self._r("debug_update_location", payload, state)
        assert new_state["locations"][loc_id]["terrain_type"] == "hills"

    def test_update_location_anomaly_activity(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        payload = {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1, "anomaly_activity": 7}
        new_state, _ = self._r("debug_update_location", payload, state)
        assert new_state["locations"][loc_id]["anomaly_activity"] == 7

    def test_update_location_dominant_anomaly_type(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        payload = {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1, "dominant_anomaly_type": "chemical"}
        new_state, _ = self._r("debug_update_location", payload, state)
        assert new_state["locations"][loc_id]["dominant_anomaly_type"] == "chemical"

    def test_update_location_clear_dominant_anomaly_type(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        # first set it
        state["locations"][loc_id]["dominant_anomaly_type"] = "fire"
        payload = {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1, "dominant_anomaly_type": ""}
        new_state, _ = self._r("debug_update_location", payload, state)
        assert new_state["locations"][loc_id]["dominant_anomaly_type"] is None

    def test_update_location_invalid_terrain_type(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1, "terrain_type": "ocean"}, state)
        assert not result.valid

    def test_update_location_invalid_anomaly_activity_out_of_range(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "type": "ruins", "danger_level": 1, "anomaly_activity": 11}, state)
        assert not result.valid

    def test_create_location_default_new_fields(self):
        state = self._state()
        new_state, _ = self._r("debug_create_location", {"name": "TestLoc", "type": "wild_area", "danger_level": 2}, state)
        new_id = list(set(new_state["locations"].keys()) - set(state["locations"].keys()))[0]
        loc = new_state["locations"][new_id]
        assert loc["terrain_type"] == "plain"
        assert loc["anomaly_activity"] == 0
        assert loc["dominant_anomaly_type"] is None

    def test_create_location_with_new_fields(self):
        state = self._state()
        payload = {
            "name": "TestLoc", "type": "anomaly_cluster", "danger_level": 4,
            "terrain_type": "slag_heaps", "anomaly_activity": 8, "dominant_anomaly_type": "gravitational",
        }
        new_state, _ = self._r("debug_create_location", payload, state)
        new_id = list(set(new_state["locations"].keys()) - set(state["locations"].keys()))[0]
        loc = new_state["locations"][new_id]
        assert loc["terrain_type"] == "slag_heaps"
        assert loc["anomaly_activity"] == 8
        assert loc["dominant_anomaly_type"] == "gravitational"

    # ── debug_spawn_stalker ──────────────────────────────────────────────────

    def test_spawn_stalker_valid(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_spawn_stalker", {"loc_id": loc_id}, state)
        assert result.valid

    def test_spawn_stalker_appears_in_state(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        old_agent_count = len(state["agents"])
        old_loc_agents = list(state["locations"][loc_id]["agents"])
        new_state, events = self._r("debug_spawn_stalker", {"loc_id": loc_id, "name": "Test Stalker"}, state)
        assert len(new_state["agents"]) == old_agent_count + 1
        new_ids = set(new_state["agents"].keys()) - set(state["agents"].keys())
        assert len(new_ids) == 1
        new_agent_id = list(new_ids)[0]
        agent = new_state["agents"][new_agent_id]
        assert agent["name"] == "Test Stalker"
        assert agent["location_id"] == loc_id
        assert agent["is_alive"] is True
        assert new_agent_id in new_state["locations"][loc_id]["agents"]
        assert any(e["event_type"] == "debug_stalker_spawned" for e in events)

    def test_spawn_stalker_default_name(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        new_state, _ = self._r("debug_spawn_stalker", {"loc_id": loc_id}, state)
        new_ids = set(new_state["agents"].keys()) - set(state["agents"].keys())
        agent = new_state["agents"][list(new_ids)[0]]
        assert agent["name"]  # non-empty default name

    def test_spawn_stalker_invalid_no_loc_id(self):
        state = self._state()
        result = self._v("debug_spawn_stalker", {}, state)
        assert not result.valid

    def test_spawn_stalker_invalid_bad_loc_id(self):
        state = self._state()
        result = self._v("debug_spawn_stalker", {"loc_id": "nonexistent"}, state)
        assert not result.valid

    # ── debug_spawn_mutant ───────────────────────────────────────────────────

    def test_spawn_mutant_valid(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_spawn_mutant", {"loc_id": loc_id, "mutant_type": "blind_dog"}, state)
        assert result.valid

    def test_spawn_mutant_appears_in_state(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        old_mutant_count = len(state["mutants"])
        new_state, events = self._r("debug_spawn_mutant", {"loc_id": loc_id, "mutant_type": "bloodsucker"}, state)
        assert len(new_state["mutants"]) == old_mutant_count + 1
        new_ids = set(new_state["mutants"].keys()) - set(state["mutants"].keys())
        assert len(new_ids) == 1
        new_mutant_id = list(new_ids)[0]
        mutant = new_state["mutants"][new_mutant_id]
        assert mutant["type"] == "bloodsucker"
        assert mutant["location_id"] == loc_id
        assert mutant["is_alive"] is True
        assert new_mutant_id in new_state["locations"][loc_id]["agents"]
        assert any(e["event_type"] == "debug_mutant_spawned" for e in events)

    def test_spawn_mutant_invalid_no_loc_id(self):
        state = self._state()
        result = self._v("debug_spawn_mutant", {"mutant_type": "zombie"}, state)
        assert not result.valid

    def test_spawn_mutant_invalid_bad_loc_id(self):
        state = self._state()
        result = self._v("debug_spawn_mutant", {"loc_id": "nope", "mutant_type": "zombie"}, state)
        assert not result.valid

    def test_spawn_mutant_invalid_bad_type(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_spawn_mutant", {"loc_id": loc_id, "mutant_type": "dragon"}, state)
        assert not result.valid

    def test_spawn_mutant_all_types(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        for mt in ("blind_dog", "flesh", "zombie", "bloodsucker", "psi_controller"):
            result = self._v("debug_spawn_mutant", {"loc_id": loc_id, "mutant_type": mt}, state)
            assert result.valid, f"Expected valid for mutant_type={mt}"

    # ── debug_spawn_artifact ─────────────────────────────────────────────────

    def test_spawn_artifact_valid(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_spawn_artifact", {"loc_id": loc_id, "artifact_type": "soul"}, state)
        assert result.valid

    def test_spawn_artifact_no_type_is_valid(self):
        """artifact_type is optional; omitting it picks a random type."""
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_spawn_artifact", {"loc_id": loc_id}, state)
        assert result.valid

    def test_spawn_artifact_adds_to_location(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        before = len(state["locations"][loc_id]["artifacts"])
        new_state, events = self._r("debug_spawn_artifact", {"loc_id": loc_id, "artifact_type": "gravi"}, state)
        after = len(new_state["locations"][loc_id]["artifacts"])
        assert after == before + 1
        art = new_state["locations"][loc_id]["artifacts"][-1]
        assert art["type"] == "gravi"
        assert art["value"] > 0
        assert art["id"].startswith("art_debug_")
        assert any(e["event_type"] == "debug_artifact_spawned" for e in events)

    def test_spawn_artifact_random_type_when_omitted(self):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        state = self._state()
        loc_id = next(iter(state["locations"]))
        new_state, _ = self._r("debug_spawn_artifact", {"loc_id": loc_id}, state)
        art = new_state["locations"][loc_id]["artifacts"][-1]
        assert art["type"] in ARTIFACT_TYPES

    def test_spawn_artifact_invalid_type(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        result = self._v("debug_spawn_artifact", {"loc_id": loc_id, "artifact_type": "dragon_egg"}, state)
        assert not result.valid

    def test_spawn_artifact_invalid_no_loc(self):
        state = self._state()
        result = self._v("debug_spawn_artifact", {}, state)
        assert not result.valid

    def test_spawn_artifact_all_types(self):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        state = self._state()
        loc_id = next(iter(state["locations"]))
        for art_type in ARTIFACT_TYPES:
            result = self._v("debug_spawn_artifact", {"loc_id": loc_id, "artifact_type": art_type}, state)
            assert result.valid, f"Expected valid for artifact_type={art_type}"

    # ── debug_delete_agent ───────────────────────────────────────────────────

    def test_delete_agent_valid(self):
        state = self._state()
        agent_id = next(iter(state["agents"]))
        result = self._v("debug_delete_agent", {"agent_id": agent_id}, state)
        assert result.valid

    def test_delete_agent_removes_from_state(self):
        state = self._state()
        agent_id = next(iter(state["agents"]))
        new_state, events = self._r("debug_delete_agent", {"agent_id": agent_id}, state)
        assert agent_id not in new_state["agents"]
        assert any(e["event_type"] == "debug_agent_deleted" for e in events)

    def test_delete_agent_removes_from_location(self):
        state = self._state()
        agent_id = next(iter(state["agents"]))
        loc_id = state["agents"][agent_id]["location_id"]
        assert agent_id in state["locations"][loc_id]["agents"]
        new_state, _ = self._r("debug_delete_agent", {"agent_id": agent_id}, state)
        assert agent_id not in new_state["locations"][loc_id]["agents"]

    def test_delete_agent_invalid_no_id(self):
        state = self._state()
        result = self._v("debug_delete_agent", {}, state)
        assert not result.valid

    def test_delete_agent_invalid_not_found(self):
        state = self._state()
        result = self._v("debug_delete_agent", {"agent_id": "nonexistent"}, state)
        assert not result.valid

    def test_delete_mutant(self):
        state = self._state()
        new_state, _ = self._r(
            "debug_spawn_mutant", {"loc_id": next(iter(state["locations"])), "mutant_type": "zombie"}, state
        )
        mutant_id = next(iter(new_state["mutants"]))
        new_state2, events = self._r("debug_delete_agent", {"agent_id": mutant_id}, new_state)
        assert mutant_id not in new_state2["mutants"]
        assert any(e["event_type"] == "debug_agent_deleted" for e in events)

    def test_delete_trader(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        new_state, _ = self._r("debug_spawn_trader", {"loc_id": loc_id}, state)
        trader_id = next(iter(new_state["traders"]))
        new_state2, events = self._r("debug_delete_agent", {"agent_id": trader_id}, new_state)
        assert trader_id not in new_state2["traders"]
        assert any(e["event_type"] == "debug_agent_deleted" for e in events)

    # ── debug_advance_turns ──────────────────────────────────────────────────

    def test_advance_turns_valid(self):
        state = self._state()
        result = self._v("debug_advance_turns", {"max_n": 5}, state)
        assert result.valid

    def test_advance_turns_advances_world_turn(self):
        state = self._state()
        initial_turn = state["world_turn"]
        new_state, events = self._r("debug_advance_turns", {"max_n": 3, "stop_on_decision": False}, state)
        assert new_state["world_turn"] == initial_turn + 3
        assert any(e["event_type"] == "debug_turns_advanced" for e in events)
        adv_ev = next(e for e in events if e["event_type"] == "debug_turns_advanced")
        assert adv_ev["payload"]["turns_advanced"] == 3

    def test_advance_turns_no_game_over_when_unlimited(self):
        """With max_turns=0 (unlimited), advancing many turns should not trigger game_over."""
        state = self._state()
        assert state.get("max_turns") == 0  # generator sets 0 = unlimited
        new_state, events = self._r("debug_advance_turns", {"max_n": 10, "stop_on_decision": False}, state)
        assert not new_state.get("game_over"), "game_over should not be triggered with unlimited turns"

    # ── debug_preview_bot_decision ───────────────────────────────────────────

    def test_preview_bot_decision_valid(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        state, _ = self._r("debug_spawn_stalker", {"loc_id": loc_id}, state)
        bot_id = next(
            aid for aid, a in state["agents"].items()
            if a.get("controller", {}).get("kind") == "bot"
        )
        result = self._v("debug_preview_bot_decision", {"agent_id": bot_id}, state)
        assert result.valid

    def test_preview_bot_decision_invalid_human(self):
        state = self._state()
        human_id = next(
            aid for aid, a in state["agents"].items()
            if a.get("controller", {}).get("kind") == "human"
        )
        result = self._v("debug_preview_bot_decision", {"agent_id": human_id}, state)
        assert not result.valid

    def test_preview_bot_decision_returns_decision(self):
        state = self._state()
        loc_id = next(iter(state["locations"]))
        state, _ = self._r("debug_spawn_stalker", {"loc_id": loc_id}, state)
        bot_id = next(
            aid for aid, a in state["agents"].items()
            if a.get("controller", {}).get("kind") == "bot"
        )
        new_state, events = self._r("debug_preview_bot_decision", {"agent_id": bot_id}, state)
        preview_ev = next((e for e in events if e["event_type"] == "debug_bot_decision_preview"), None)
        assert preview_ev is not None
        decision = preview_ev["payload"]["decision"]
        assert "goal" in decision
        assert "action" in decision
        assert "reason" in decision
        # State must NOT be mutated (agent still present)
        assert bot_id in new_state["agents"]

    def test_preview_bot_decision_does_not_mutate_world_turn(self):
        """Dry-running the bot decision must not advance the world turn."""
        state = self._state()
        initial_turn = state["world_turn"]
        loc_id = next(iter(state["locations"]))
        state, _ = self._r("debug_spawn_stalker", {"loc_id": loc_id}, state)
        bot_id = next(
            aid for aid, a in state["agents"].items()
            if a.get("controller", {}).get("kind") == "bot"
        )
        new_state, _ = self._r("debug_preview_bot_decision", {"agent_id": bot_id}, state)
        assert new_state["world_turn"] == initial_turn


# ─────────────────────────────────────────────────────────────────
# Unified stalker model — controller.kind consistency
# ─────────────────────────────────────────────────────────────────

class TestUnifiedStalkerModel:
    """
    All stalker agents (player and NPC) use the same _make_stalker_agent factory.
    The only distinction is controller.kind = 'human' | 'bot'.
    """

    def _state(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        return generate_zone(seed=42, num_players=1, num_ai_stalkers=2, num_mutants=0, num_traders=0)

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "player1")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "player1")

    # ── controller.kind values ────────────────────────────────────────────────

    def test_player_agent_controller_kind_is_human(self):
        state = self._state()
        agent = state["agents"]["agent_p0"]
        assert agent["controller"]["kind"] == "human"

    def test_npc_agent_controller_kind_is_bot(self):
        state = self._state()
        agent = state["agents"]["agent_ai_0"]
        assert agent["controller"]["kind"] == "bot"

    def test_debug_spawn_stalker_controller_kind_is_bot(self):
        state = self._state()
        state["player_agents"]["player1"] = "agent_p0"
        state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
        loc_id = next(iter(state["locations"]))
        new_state, _ = self._r("debug_spawn_stalker", {"loc_id": loc_id}, state)
        new_id = (set(new_state["agents"]) - set(state["agents"])).pop()
        assert new_state["agents"][new_id]["controller"]["kind"] == "bot"

    def test_take_control_sets_controller_to_human(self):
        state = self._state()
        state["player_agents"]["player1"] = "agent_p0"
        state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
        npc_id = "agent_ai_0"
        new_state, events = self._r("take_control", {"agent_id": npc_id}, state)
        assert new_state["agents"][npc_id]["controller"]["kind"] == "human"
        assert new_state["agents"][npc_id]["controller"]["participant_id"] == "player1"
        assert any(e["event_type"] == "agent_control_taken" for e in events)

    def test_take_control_releases_previous_agent_to_bot(self):
        state = self._state()
        state["player_agents"]["player1"] = "agent_p0"
        state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
        npc_id = "agent_ai_0"
        new_state, _ = self._r("take_control", {"agent_id": npc_id}, state)
        # Previous player agent should be released back to bot
        assert new_state["agents"]["agent_p0"]["controller"]["kind"] == "bot"
        assert new_state["agents"]["agent_p0"]["controller"]["participant_id"] is None

    def test_take_control_validates_bot_only(self):
        state = self._state()
        state["player_agents"]["player1"] = "agent_p0"
        state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
        # Taking control of a human-controlled agent should fail
        result = self._v("take_control", {"agent_id": "agent_p0"}, state)
        assert not result.valid
        assert "player" in result.error.lower()

    # ── same fields on player and NPC agents ─────────────────────────────────

    _REQUIRED_FIELDS = [
        "id", "archetype", "name", "location_id", "hp", "max_hp", "radiation",
        "hunger", "thirst", "sleepiness", "money", "inventory", "equipment",
        "faction", "controller", "is_alive", "action_used",
        "experience", "skill_combat", "skill_stalker", "skill_trade",
        "skill_medicine", "skill_social",
        "global_goal", "current_goal", "risk_tolerance", "material_threshold",
        "scheduled_action", "action_queue", "memory",
    ]

    def test_player_agent_has_all_required_fields(self):
        state = self._state()
        agent = state["agents"]["agent_p0"]
        for field in self._REQUIRED_FIELDS:
            assert field in agent, f"Player agent missing field: {field}"

    def test_npc_agent_has_all_required_fields(self):
        state = self._state()
        agent = state["agents"]["agent_ai_0"]
        for field in self._REQUIRED_FIELDS:
            assert field in agent, f"NPC agent missing field: {field}"

    def test_player_and_npc_have_same_field_set(self):
        state = self._state()
        player_fields = set(state["agents"]["agent_p0"].keys())
        npc_fields = set(state["agents"]["agent_ai_0"].keys())
        assert player_fields == npc_fields, (
            f"Field mismatch — only in player: {player_fields - npc_fields}, "
            f"only in NPC: {npc_fields - player_fields}"
        )

    # ── global_goal present for all agents ────────────────────────────────────

    def test_player_agent_has_global_goal(self):
        state = self._state()
        agent = state["agents"]["agent_p0"]
        assert agent["global_goal"] in ("survive", "get_rich", "explore", "serve_faction")

    def test_npc_agent_has_global_goal(self):
        state = self._state()
        agent = state["agents"]["agent_ai_0"]
        assert agent["global_goal"] in ("survive", "get_rich", "explore", "serve_faction")

    def test_all_agents_have_material_threshold(self):
        state = self._state()
        for aid, agent in state["agents"].items():
            assert "material_threshold" in agent, f"Agent {aid} missing material_threshold"
            assert isinstance(agent["material_threshold"], int)
            assert agent["material_threshold"] > 0


class TestDebugAutoTick:
    """Tests for the debug_set_auto_tick command."""

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "any_user")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "any_user")

    def _state(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        return generate_zone(seed=42, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)

    def test_set_auto_tick_true_valid(self):
        state = self._state()
        assert self._v("debug_set_auto_tick", {"enabled": True}, state).valid

    def test_set_auto_tick_false_valid(self):
        state = self._state()
        assert self._v("debug_set_auto_tick", {"enabled": False}, state).valid

    def test_set_auto_tick_stores_flag(self):
        state = self._state()
        new_state, _ = self._r("debug_set_auto_tick", {"enabled": True}, state)
        assert new_state["debug_auto_tick"] is True

    def test_set_auto_tick_disable_stores_flag(self):
        state = self._state()
        new_state, _ = self._r("debug_set_auto_tick", {"enabled": False}, state)
        assert new_state["debug_auto_tick"] is False

    def test_set_auto_tick_emits_event(self):
        state = self._state()
        _, events = self._r("debug_set_auto_tick", {"enabled": True}, state)
        assert any(e["event_type"] == "debug_auto_tick_changed" for e in events)

    def test_set_auto_tick_event_payload(self):
        state = self._state()
        _, events = self._r("debug_set_auto_tick", {"enabled": True}, state)
        ev = next(e for e in events if e["event_type"] == "debug_auto_tick_changed")
        assert ev["payload"]["enabled"] is True

    def test_set_auto_tick_does_not_mutate_original_state(self):
        import copy
        state = self._state()
        original = copy.deepcopy(state)
        self._r("debug_set_auto_tick", {"enabled": True}, state)
        assert state == original
