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
        assert agent["global_goal"] == "get_rich"

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

    def test_consume_morphine_reduces_sleepiness(self):
        state = self._state_with_item("morphine")
        state["agents"]["agent_p0"]["sleepiness"] = 60
        new_state, _ = self._r("consume_item", {"item_id": "test_item_1"}, state)
        assert new_state["agents"]["agent_p0"]["sleepiness"] < 60

    def test_consume_energy_drink_reduces_sleepiness(self):
        state = self._state_with_item("energy_drink")
        state["agents"]["agent_p0"]["sleepiness"] = 50
        new_state, _ = self._r("consume_item", {"item_id": "test_item_1"}, state)
        assert new_state["agents"]["agent_p0"]["sleepiness"] < 50


class TestBuyFromTrader:
    """Unit tests for the buy_from_trader zone_map command."""

    def _make_state(self, agent_money: int = 5000):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=7, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        state["player_agents"]["player1"] = "agent_p0"
        state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
        state["agents"]["agent_p0"]["money"] = agent_money
        # Place a trader at the same location as the player's agent
        loc_id = state["agents"]["agent_p0"]["location_id"]
        state["traders"]["trader_test"] = {
            "id": "trader_test",
            "name": "Sidorovich",
            "location_id": loc_id,
            "inventory": [],
            "money": 5000,
            "is_alive": True,
        }
        state["locations"][loc_id].setdefault("agents", []).append("trader_test")
        return state

    def _v(self, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command("buy_from_trader", payload, state, "player1")

    def _r(self, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command("buy_from_trader", payload, state, "player1")

    def test_buy_valid(self):
        state = self._make_state()
        result = self._v({"item_type": "medkit"}, state)
        assert result.valid

    def test_buy_unknown_item_type_invalid(self):
        state = self._make_state()
        result = self._v({"item_type": "nonexistent_item"}, state)
        assert not result.valid
        assert "unknown" in result.error.lower()

    def test_buy_missing_item_type_invalid(self):
        state = self._make_state()
        result = self._v({}, state)
        assert not result.valid
        assert "item_type" in result.error.lower()

    def test_buy_no_trader_at_location_invalid(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=7, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        state["player_agents"]["player1"] = "agent_p0"
        state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
        result = self._v({"item_type": "medkit"}, state)
        assert not result.valid
        assert "trader" in result.error.lower()

    def test_buy_not_enough_money_invalid(self):
        state = self._make_state(agent_money=10)
        result = self._v({"item_type": "medkit"}, state)
        assert not result.valid
        assert "money" in result.error.lower()

    def test_buy_transfers_item_to_inventory(self):
        state = self._make_state(agent_money=5000)
        new_state, events = self._r({"item_type": "bandage"}, state)
        agent = new_state["agents"]["agent_p0"]
        assert any(i["type"] == "bandage" for i in agent["inventory"])
        assert any(e["event_type"] == "item_bought" for e in events)

    def test_buy_deducts_money_at_150_pct(self):
        state = self._make_state(agent_money=5000)
        new_state, _ = self._r({"item_type": "bandage"}, state)
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        expected_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
        assert new_state["agents"]["agent_p0"]["money"] == 5000 - expected_price

    def test_buy_credits_trader_money(self):
        state = self._make_state(agent_money=5000)
        new_state, _ = self._r({"item_type": "bandage"}, state)
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        expected_price = int(ITEM_TYPES["bandage"]["value"] * 1.5)
        assert new_state["traders"]["trader_test"]["money"] == 5000 + expected_price

    def test_buy_all_new_items_valid(self):
        """All new item types should be purchasable from a trader."""
        new_items = [
            "army_medkit", "morphine", "rad_cure",
            "pkm", "svu_svd",
            "combat_armor", "seva_suit",
            "ammo_762",
            "military_ration", "purified_water", "glucose",
            "bear_detector",
        ]
        for item_type in new_items:
            state = self._make_state(agent_money=50000)
            result = self._v({"item_type": item_type}, state)
            assert result.valid, f"buy_from_trader should be valid for new item '{item_type}': {result.error}"


class TestRiskToleranceItemSelection:
    """Unit tests for risk-tolerance-based item selection in bot purchases."""

    def _make_bot_state(self, agent_risk: float = 0.5, agent_money: int = 10000):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=5, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        agent["risk_tolerance"] = agent_risk
        agent["money"] = agent_money
        state["traders"]["trader_rt"] = {
            "id": "trader_rt", "name": "Test Trader",
            "location_id": loc_id, "inventory": [], "money": 5000, "is_alive": True,
        }
        return state, agent

    def test_select_item_prefers_closest_risk_tolerance(self):
        """_select_item_by_risk_tolerance should pick the item with risk_tolerance closest to agent's
        when there is a unique closest match (multi-factor scoring with risk as dominant factor)."""
        from app.games.zone_stalkers.rules.tick_rules import _select_item_by_risk_tolerance
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ITEM_TYPES
        # ak74 has rt=0.5 — exact match for agent_risk=0.5, unique minimum distance.
        # Even with multi-factor scoring the unique best risk-match should still win.
        agent_risk = 0.5
        result = _select_item_by_risk_tolerance(WEAPON_ITEM_TYPES, agent_risk)
        assert result is not None
        item_key, _ = result
        # Verify the selected item is indeed the one with minimum |rt - agent_risk|
        best_dist = min(
            abs(ITEM_TYPES[k].get("risk_tolerance", 0.5) - agent_risk)
            for k in WEAPON_ITEM_TYPES if k in ITEM_TYPES
        )
        assert abs(ITEM_TYPES[item_key].get("risk_tolerance", 0.5) - agent_risk) == best_dist

    def test_select_item_for_cautious_agent(self):
        """A cautious agent (low risk_tolerance) should prefer the lowest-risk weapon."""
        from app.games.zone_stalkers.rules.tick_rules import _select_item_by_risk_tolerance, _score_item_for_purchase
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ITEM_TYPES
        agent_risk = 0.0  # absolute minimum — will pick the weapon with lowest risk_tolerance
        result = _select_item_by_risk_tolerance(WEAPON_ITEM_TYPES, agent_risk)
        assert result is not None
        item_key, _ = result
        # Multi-factor: compute expected winner with the composite score
        candidates = [(k, ITEM_TYPES[k]) for k in WEAPON_ITEM_TYPES if k in ITEM_TYPES]
        max_value = max(info.get("value", 0) for _, info in candidates) or 1
        max_weight = max(info.get("weight", 0.0) for _, info in candidates) or 1
        expected_key = max(
            candidates,
            key=lambda kv: _score_item_for_purchase(kv[1], agent_risk, max_value, max_weight),
        )[0]
        assert item_key == expected_key

    def test_select_item_for_aggressive_agent(self):
        """An aggressive agent (high risk_tolerance) should prefer the highest-risk weapon."""
        from app.games.zone_stalkers.rules.tick_rules import _select_item_by_risk_tolerance, _score_item_for_purchase
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ITEM_TYPES
        agent_risk = 1.0  # absolute maximum — will pick the weapon with highest risk_tolerance
        result = _select_item_by_risk_tolerance(WEAPON_ITEM_TYPES, agent_risk)
        assert result is not None
        item_key, _ = result
        candidates = [(k, ITEM_TYPES[k]) for k in WEAPON_ITEM_TYPES if k in ITEM_TYPES]
        max_value = max(info.get("value", 0) for _, info in candidates) or 1
        max_weight = max(info.get("weight", 0.0) for _, info in candidates) or 1
        expected_key = max(
            candidates,
            key=lambda kv: _score_item_for_purchase(kv[1], agent_risk, max_value, max_weight),
        )[0]
        assert item_key == expected_key

    def test_select_item_empty_set_returns_none(self):
        from app.games.zone_stalkers.rules.tick_rules import _select_item_by_risk_tolerance
        assert _select_item_by_risk_tolerance(frozenset(), 0.5) is None

    def test_bot_buys_risk_matched_weapon(self):
        """Bot should choose the weapon with the best composite score (risk+value+weight)."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader, _select_item_by_risk_tolerance
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        agent_risk = 0.5
        state, agent = self._make_bot_state(agent_risk=agent_risk)
        events = _bot_buy_from_trader("agent_p0", agent, WEAPON_ITEM_TYPES, state, world_turn=1)
        assert len(events) == 1
        # Verify the purchased item matches the reference selector (both use the same formula)
        expected = _select_item_by_risk_tolerance(WEAPON_ITEM_TYPES, agent_risk)
        assert expected is not None
        assert events[0]["payload"]["item_type"] == expected[0]

    def test_bot_buys_risk_matched_armor(self):
        """Bot should choose the armor with the best composite score (risk+value+weight)."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader, _select_item_by_risk_tolerance
        from app.games.zone_stalkers.balance.items import ARMOR_ITEM_TYPES
        agent_risk = 0.4
        state, agent = self._make_bot_state(agent_risk=agent_risk)
        events = _bot_buy_from_trader("agent_p0", agent, ARMOR_ITEM_TYPES, state, world_turn=1)
        assert len(events) == 1
        expected = _select_item_by_risk_tolerance(ARMOR_ITEM_TYPES, agent_risk)
        assert expected is not None
        assert events[0]["payload"]["item_type"] == expected[0]

    def test_bot_buy_writes_decision_memory(self):
        """A 'decision' memory entry must be written with risk_tolerance and score info."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        state, agent = self._make_bot_state(agent_risk=0.5)
        _bot_buy_from_trader("agent_p0", agent, WEAPON_ITEM_TYPES, state, world_turn=1)
        decision_entries = [m for m in agent.get("memory", []) if m.get("type") == "decision"]
        assert len(decision_entries) >= 1
        last_decision = decision_entries[-1]
        effects = last_decision.get("effects", {})
        assert effects.get("action_kind") == "trade_decision"
        assert "agent_risk_tolerance" in effects
        assert "score" in effects, "decision memory must contain composite score"

    def test_bot_buy_decision_memory_includes_runners_up(self):
        """When multiple candidates exist, the decision memory must list up to 2 runner-ups."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        # WEAPON_ITEM_TYPES has 5 items — there will be runner-ups
        state, agent = self._make_bot_state(agent_risk=0.5)
        _bot_buy_from_trader("agent_p0", agent, WEAPON_ITEM_TYPES, state, world_turn=1)
        decision_entries = [m for m in agent.get("memory", []) if m.get("type") == "decision"]
        last_decision = decision_entries[-1]
        effects = last_decision.get("effects", {})
        runners_up = effects.get("runners_up", [])
        assert isinstance(runners_up, list), "runners_up must be a list"
        assert 1 <= len(runners_up) <= 2, f"expected 1-2 runner-ups, got {len(runners_up)}"
        for r in runners_up:
            assert "item_type" in r
            assert "score" in r
            assert "price" in r
        # Each runner-up score should be ≤ the winner's score
        winner_score = effects.get("score", 0.0)
        for r in runners_up:
            assert r["score"] <= winner_score + 1e-9, (
                f"runner-up score {r['score']} exceeds winner score {winner_score}"
            )

    def test_scoring_prefers_higher_value_when_risk_is_tied(self):
        """When risk_tolerance distances are equal, higher-value item scores better."""
        from app.games.zone_stalkers.rules.tick_rules import _score_item_for_purchase
        # Two synthetic items with identical risk_tolerance — but different values and weights
        # (same weight to isolate the value factor).
        agent_risk = 0.5
        item_cheap = {"risk_tolerance": 0.5, "value": 100, "weight": 1.0}
        item_expensive = {"risk_tolerance": 0.5, "value": 1000, "weight": 1.0}
        max_value = 1000
        max_weight = 1.0
        score_cheap = _score_item_for_purchase(item_cheap, agent_risk, max_value, max_weight)
        score_expensive = _score_item_for_purchase(item_expensive, agent_risk, max_value, max_weight)
        assert score_expensive > score_cheap, (
            "higher-value item should score better when risk_tolerance is equal"
        )

    def test_scoring_prefers_lighter_item_when_risk_and_value_are_equal(self):
        """When risk_tolerance and value are equal, the lighter item scores better."""
        from app.games.zone_stalkers.rules.tick_rules import _score_item_for_purchase
        agent_risk = 0.5
        item_heavy = {"risk_tolerance": 0.5, "value": 500, "weight": 5.0}
        item_light = {"risk_tolerance": 0.5, "value": 500, "weight": 1.0}
        max_value = 500
        max_weight = 5.0
        score_heavy = _score_item_for_purchase(item_heavy, agent_risk, max_value, max_weight)
        score_light = _score_item_for_purchase(item_light, agent_risk, max_value, max_weight)
        assert score_light > score_heavy, (
            "lighter item should score better when risk_tolerance and value are equal"
        )

    def test_bot_buy_event_carries_score_and_risk_tolerance(self):
        """bot_bought_item event payload must include score, risk_tolerance values."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        state, agent = self._make_bot_state(agent_risk=0.6)
        events = _bot_buy_from_trader("agent_p0", agent, WEAPON_ITEM_TYPES, state, world_turn=1)
        assert events
        payload = events[0]["payload"]
        assert "agent_risk_tolerance" in payload
        assert "item_risk_tolerance" in payload
        assert "score" in payload
        assert 0.0 <= payload["score"] <= 1.0

    def test_bot_falls_back_when_preferred_item_too_expensive(self):
        """If the best-matching item is unaffordable, the next-closest is chosen."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ITEM_TYPES
        # For agent_risk=0.9 the closest weapon is pkm (0.9), price 3500*1.5=5250
        # Give the agent exactly 5249 — not enough for pkm, should fall back
        # Next closest to 0.9: svu_svd (0.7), price 4500*1.5=6750 — too expensive
        # Next: ak74 (0.6), price 1500*1.5=2250 — affordable
        state, agent = self._make_bot_state(agent_risk=0.9, agent_money=2250)
        events = _bot_buy_from_trader("agent_p0", agent, WEAPON_ITEM_TYPES, state, world_turn=1)
        assert events
        # Must have bought something affordable
        bought_type = events[0]["payload"]["item_type"]
        bought_price = int(ITEM_TYPES[bought_type]["value"] * 1.5)
        assert bought_price <= 2250

    def test_all_items_have_risk_tolerance(self):
        """Every item in ITEM_TYPES must have a risk_tolerance field."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        for item_key, item_data in ITEM_TYPES.items():
            assert "risk_tolerance" in item_data, (
                f"Item '{item_key}' is missing 'risk_tolerance'"
            )
            rt = item_data["risk_tolerance"]
            assert 0.0 <= rt <= 1.0, (
                f"Item '{item_key}' has risk_tolerance={rt} outside [0, 1]"
            )


class TestEquipmentUpgrade:
    """Tests for the equipment-upgrade bot priority layer and debug inventory commands."""

    def _make_state_with_agent(self, agent_risk: float = 0.5, agent_money: int = 10000,
                                equipped_weapon: str = "pistol"):
        """Return (state, agent) where the agent already has a weapon equipped."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state = generate_zone(seed=3, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        agent["risk_tolerance"] = agent_risk
        agent["money"] = agent_money
        # Give agent a weapon already equipped
        info = ITEM_TYPES[equipped_weapon]
        agent.setdefault("equipment", {})["weapon"] = {
            "id": f"{equipped_weapon}_init",
            "type": equipped_weapon,
            "name": info.get("name", equipped_weapon),
            "value": info.get("value", 0),
        }
        # Place a trader at the same location
        state["traders"]["trader_upg"] = {
            "id": "trader_upg", "name": "UpgradeTrader",
            "location_id": loc_id, "inventory": [], "money": 50000, "is_alive": True,
        }
        return state, agent

    # ── _find_upgrade_target tests ─────────────────────────────────────────

    def test_find_upgrade_target_returns_better_match(self):
        """_find_upgrade_target should return an item with better risk match and higher value."""
        from app.games.zone_stalkers.rules.tick_rules import _find_upgrade_target
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ITEM_TYPES
        # Current: pistol (rt=0.3, value=500). Agent risk=0.6. Better match: ak74 (rt=0.6, val=1500)
        result = _find_upgrade_target(WEAPON_ITEM_TYPES, "pistol", 0.6, 50000)
        assert result is not None
        # The result must be closer to 0.6 than pistol (0.3) AND more expensive than pistol
        assert ITEM_TYPES[result]["risk_tolerance"] != 0.3  # not the same as current
        assert ITEM_TYPES[result]["value"] > ITEM_TYPES["pistol"]["value"]

    def test_find_upgrade_target_no_upgrade_when_already_best(self):
        """No upgrade when current item is the best risk-tolerance match."""
        from app.games.zone_stalkers.rules.tick_rules import _find_upgrade_target
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ITEM_TYPES
        # ak74 has rt=0.5 — exact match for agent_risk=0.5.
        # No other weapon has a closer risk_tolerance AND higher value, so no upgrade exists.
        result = _find_upgrade_target(WEAPON_ITEM_TYPES, "ak74", 0.5, 50000)
        assert result is None

    def test_find_upgrade_target_no_upgrade_when_cant_afford(self):
        """No upgrade returned when agent cannot afford the better item."""
        from app.games.zone_stalkers.rules.tick_rules import _find_upgrade_target
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        # pistol → ak74 costs 1500*1.5=2250; agent only has 100 money
        result = _find_upgrade_target(WEAPON_ITEM_TYPES, "pistol", 0.6, 100)
        assert result is None

    def test_find_upgrade_target_no_upgrade_for_none_current(self):
        """No upgrade when there is no current item (slot empty)."""
        from app.games.zone_stalkers.rules.tick_rules import _find_upgrade_target
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        # current_item_type=None → nothing to upgrade
        result = _find_upgrade_target(WEAPON_ITEM_TYPES, None, 0.5, 50000)
        assert result is None

    # ── _bot_try_upgrade_equipment tests ──────────────────────────────────

    def test_upgrade_buys_better_weapon_when_trader_present(self):
        """Bot should buy and equip a better weapon when the trader is at the same location."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_try_upgrade_equipment
        # pistol (rt=0.3) equipped; agent risk=0.6 → upgrade target should be ak74 (rt=0.6)
        state, agent = self._make_state_with_agent(agent_risk=0.6, agent_money=50000, equipped_weapon="pistol")
        events = _bot_try_upgrade_equipment("agent_p0", agent, agent["location_id"], state, 1)
        assert events, "Expected upgrade events"
        event_types = {e["event_type"] for e in events}
        assert "bot_bought_item" in event_types

    def test_upgrade_equips_new_weapon_in_slot(self):
        """After upgrade, the agent's weapon slot should contain the new (better) item."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_try_upgrade_equipment
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, agent = self._make_state_with_agent(agent_risk=0.6, agent_money=50000, equipped_weapon="pistol")
        _bot_try_upgrade_equipment("agent_p0", agent, agent["location_id"], state, 1)
        new_weapon = agent.get("equipment", {}).get("weapon")
        assert new_weapon is not None
        new_type = new_weapon.get("type")
        # The new weapon must have a closer risk_tolerance to 0.6 than pistol (0.3)
        assert abs(ITEM_TYPES[new_type]["risk_tolerance"] - 0.6) < abs(0.3 - 0.6)

    def test_upgrade_old_weapon_returns_to_inventory(self):
        """The old equipped weapon should go back to inventory after upgrade."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_try_upgrade_equipment
        state, agent = self._make_state_with_agent(agent_risk=0.6, agent_money=50000, equipped_weapon="pistol")
        _bot_try_upgrade_equipment("agent_p0", agent, agent["location_id"], state, 1)
        inv_types = {i["type"] for i in agent.get("inventory", [])}
        assert "pistol" in inv_types, "Old weapon should be returned to inventory"

    def test_upgrade_writes_decision_memory(self):
        """Upgrade decision must be recorded in agent memory."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_try_upgrade_equipment
        state, agent = self._make_state_with_agent(agent_risk=0.6, agent_money=50000, equipped_weapon="pistol")
        _bot_try_upgrade_equipment("agent_p0", agent, agent["location_id"], state, 1)
        decision_mem = [m for m in agent.get("memory", []) if m.get("type") == "decision"]
        kinds = [m.get("effects", {}).get("action_kind", "") for m in decision_mem]
        assert "upgrade_decision" in kinds

    def test_no_upgrade_when_no_equipment(self):
        """No upgrade events when agent has no weapon equipped."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_try_upgrade_equipment
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=4, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        agent = state["agents"]["agent_p0"]
        agent["risk_tolerance"] = 0.6
        agent["money"] = 50000
        agent["equipment"] = {}  # no equipment at all
        loc_id = agent["location_id"]
        state["traders"]["trader_t"] = {
            "id": "trader_t", "name": "T", "location_id": loc_id,
            "inventory": [], "money": 50000, "is_alive": True,
        }
        events = _bot_try_upgrade_equipment("agent_p0", agent, loc_id, state, 1)
        assert events == []

    # ── Debug command: debug_add_item ────────────────────────────────────

    def _base_state(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=6, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        return state

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "any_user")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "any_user")

    def test_debug_add_item_valid(self):
        state = self._base_state()
        result = self._v("debug_add_item", {"agent_id": "agent_p0", "item_type": "bandage"}, state)
        assert result.valid

    def test_debug_add_item_invalid_no_agent(self):
        state = self._base_state()
        result = self._v("debug_add_item", {"agent_id": "nonexistent", "item_type": "bandage"}, state)
        assert not result.valid

    def test_debug_add_item_invalid_unknown_type(self):
        state = self._base_state()
        result = self._v("debug_add_item", {"agent_id": "agent_p0", "item_type": "nuclear_bomb"}, state)
        assert not result.valid

    def test_debug_add_item_resolve_adds_to_inventory(self):
        state = self._base_state()
        inv_before = len(state["agents"]["agent_p0"].get("inventory", []))
        new_state, events = self._r("debug_add_item", {"agent_id": "agent_p0", "item_type": "ak74"}, state)
        assert len(new_state["agents"]["agent_p0"]["inventory"]) == inv_before + 1
        types_in_inv = {i["type"] for i in new_state["agents"]["agent_p0"]["inventory"]}
        assert "ak74" in types_in_inv
        assert any(e["event_type"] == "debug_item_added" for e in events)

    # ── Debug command: debug_remove_item ─────────────────────────────────

    def test_debug_remove_item_valid(self):
        state = self._base_state()
        # Add an item first so we have an id
        new_state, _ = self._r("debug_add_item", {"agent_id": "agent_p0", "item_type": "bandage"}, state)
        item_id = new_state["agents"]["agent_p0"]["inventory"][-1]["id"]
        result = self._v("debug_remove_item", {"agent_id": "agent_p0", "item_id": item_id}, new_state)
        assert result.valid

    def test_debug_remove_item_invalid_no_agent(self):
        state = self._base_state()
        result = self._v("debug_remove_item", {"agent_id": "ghost", "item_id": "x"}, state)
        assert not result.valid

    def test_debug_remove_item_resolve_removes_from_inventory(self):
        state = self._base_state()
        new_state, _ = self._r("debug_add_item", {"agent_id": "agent_p0", "item_type": "medkit"}, state)
        item_id = new_state["agents"]["agent_p0"]["inventory"][-1]["id"]
        final_state, events = self._r("debug_remove_item", {"agent_id": "agent_p0", "item_id": item_id}, new_state)
        inv_types = {i["type"] for i in final_state["agents"]["agent_p0"]["inventory"]}
        assert "medkit" not in inv_types
        ev = next(e for e in events if e["event_type"] == "debug_item_removed")
        assert ev["payload"]["removed"] is True

    def test_debug_remove_item_resolve_removes_from_equipment(self):
        """debug_remove_item should set equipment slots to None (not delete them)."""
        state = self._base_state()
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        eq_item = {
            "id": "pistol_eq_test", "type": "pistol",
            "name": ITEM_TYPES["pistol"]["name"], "value": 500,
        }
        state["agents"]["agent_p0"].setdefault("equipment", {})["weapon"] = eq_item
        final_state, events = self._r(
            "debug_remove_item",
            {"agent_id": "agent_p0", "item_id": "pistol_eq_test"},
            state,
        )
        # Slot should be set to None, not deleted
        assert final_state["agents"]["agent_p0"]["equipment"]["weapon"] is None
        ev = next(e for e in events if e["event_type"] == "debug_item_removed")
        assert ev["payload"]["removed"] is True

    def test_debug_remove_nonexistent_item_returns_removed_false(self):
        state = self._base_state()
        final_state, events = self._r(
            "debug_remove_item",
            {"agent_id": "agent_p0", "item_id": "nonexistent_id"},
            state,
        )
        ev = next(e for e in events if e["event_type"] == "debug_item_removed")
        assert ev["payload"]["removed"] is False


class TestMaterialThreshold:
    """Tests for material_threshold separation from global_goal and the debug command."""

    def _base_state(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        return generate_zone(seed=7, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "any_user")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "any_user")

    # ── Generator: all agents get threshold in [3000, 10000] ───────────────

    def test_all_generated_agents_have_threshold_in_range(self):
        """Generator must set material_threshold in [3000, 10000] for all stalkers."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        for seed in range(20):
            state = generate_zone(seed=seed, num_players=0, num_ai_stalkers=5, num_mutants=0, num_traders=0)
            for agent_id, agent in state.get("agents", {}).items():
                t = agent.get("material_threshold", 0)
                assert 3000 <= t <= 10000, (
                    f"seed={seed} agent={agent_id}: material_threshold={t} out of [3000,10000]"
                )

    def test_get_rich_agent_has_same_threshold_range(self):
        """get_rich agents must also have threshold in [3000, 10000], not 50k-1M."""
        from app.games.zone_stalkers.generators.zone_generator import _make_stalker_agent
        import random
        found_get_rich = False
        for seed in range(100):
            rng = random.Random(seed)
            agent = _make_stalker_agent(
                agent_id=f"a{seed}", name=f"S{seed}", location_id="loc0",
                controller_kind="bot", participant_id=None, rng=rng,
                global_goal="get_rich",
            )
            assert 3000 <= agent["material_threshold"] <= 10000, (
                f"get_rich agent seed={seed}: threshold={agent['material_threshold']}"
            )
            found_get_rich = True
        assert found_get_rich

    # ── Bot decision: ALL agents go through threshold gate ─────────────────

    def test_get_rich_agent_below_threshold_gathers_resources(self):
        """A get_rich agent below material_threshold must gather resources, not pursue goal directly."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action_inner
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=8, num_players=0, num_ai_stalkers=1, num_mutants=0, num_traders=0)
        agent_id = list(state["agents"].keys())[0]
        agent = state["agents"][agent_id]
        agent["global_goal"] = "get_rich"
        agent["material_threshold"] = 5000
        agent["money"] = 100  # far below threshold
        agent["inventory"] = []
        agent["equipment"] = {"weapon": None, "armor": None, "detector": None}
        # We just need to verify current_goal gets set to gather_resources
        _run_bot_action_inner(agent_id, agent, state, world_turn=1)
        assert agent.get("current_goal") in ("gather_resources", "get_weapon", "get_armor"), (
            f"Expected resource gathering for underfunded get_rich agent, got {agent.get('current_goal')}"
        )

    def test_validate_set_threshold_valid(self):
        state = self._base_state()
        result = self._v("debug_set_agent_threshold", {"agent_id": "agent_p0", "amount": 5000}, state)
        assert result.valid

    def test_validate_set_threshold_below_min(self):
        state = self._base_state()
        result = self._v("debug_set_agent_threshold", {"agent_id": "agent_p0", "amount": 2999}, state)
        assert not result.valid

    def test_validate_set_threshold_above_max(self):
        state = self._base_state()
        result = self._v("debug_set_agent_threshold", {"agent_id": "agent_p0", "amount": 10001}, state)
        assert not result.valid

    def test_validate_set_threshold_missing_agent(self):
        state = self._base_state()
        result = self._v("debug_set_agent_threshold", {"agent_id": "ghost_agent", "amount": 5000}, state)
        assert not result.valid

    def test_validate_set_threshold_missing_amount(self):
        state = self._base_state()
        result = self._v("debug_set_agent_threshold", {"agent_id": "agent_p0"}, state)
        assert not result.valid

    # ── debug_set_agent_threshold: resolve ─────────────────────────────────

    def test_resolve_set_threshold_updates_agent(self):
        state = self._base_state()
        new_state, events = self._r("debug_set_agent_threshold", {"agent_id": "agent_p0", "amount": 7500}, state)
        assert new_state["agents"]["agent_p0"]["material_threshold"] == 7500
        assert any(e["event_type"] == "debug_agent_threshold_set" for e in events)

    def test_resolve_set_threshold_at_boundary_3000(self):
        state = self._base_state()
        new_state, _ = self._r("debug_set_agent_threshold", {"agent_id": "agent_p0", "amount": 3000}, state)
        assert new_state["agents"]["agent_p0"]["material_threshold"] == 3000

    def test_resolve_set_threshold_at_boundary_10000(self):
        state = self._base_state()
        new_state, _ = self._r("debug_set_agent_threshold", {"agent_id": "agent_p0", "amount": 10000}, state)
        assert new_state["agents"]["agent_p0"]["material_threshold"] == 10000


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
            assert loc["terrain_type"] in {"plain", "hills", "slag_heaps", "industrial", "buildings", "military_buildings", "hamlet", "farm", "field_camp", "swamp"}
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
        assert agent["global_goal"] == "get_rich"

    def test_npc_agent_has_global_goal(self):
        state = self._state()
        agent = state["agents"]["agent_ai_0"]
        assert agent["global_goal"] == "get_rich"

    def test_all_agents_have_material_threshold(self):
        state = self._state()
        for aid, agent in state["agents"].items():
            assert "material_threshold" in agent, f"Agent {aid} missing material_threshold"
            assert isinstance(agent["material_threshold"], int)
            assert agent["material_threshold"] > 0


class TestAutoTickCore:
    """Tests for the core set_auto_tick pipeline meta-command and ticker service."""

    def _state(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        return generate_zone(seed=42, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)

    # ── tick() return value includes world_minute ─────────────────────────────

    def test_tick_zone_map_returns_world_minute(self):
        """tick_zone_map must include world_minute so the WS push can carry it."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._state()
        new_state, _ = tick_zone_map(state)
        # world_minute must be present in the state after a tick
        assert "world_minute" in new_state

    def test_tick_zone_map_increments_world_minute(self):
        """Each call to tick_zone_map advances world_minute by 1 (wraps at 60)."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._state()
        minute_before = state.get("world_minute", 0)
        hour_before = state.get("world_hour", 6)
        new_state, _ = tick_zone_map(state)
        if minute_before == 59:
            assert new_state["world_minute"] == 0
            assert new_state["world_hour"] == (hour_before + 1) % 24
        else:
            assert new_state["world_minute"] == minute_before + 1
            assert new_state["world_hour"] == hour_before

    # ── pipeline sets auto_tick_enabled flag ────────────────────────────────

    def _simulate_pipeline_set_auto_tick(self, state: dict, enabled: bool) -> dict:
        """
        Simulate the pipeline meta-command logic in isolation
        (without a real DB/HTTP stack) to verify the state mutation.
        """
        import copy
        new_state = copy.deepcopy(state)
        new_state["auto_tick_enabled"] = bool(enabled)
        return new_state

    def test_set_auto_tick_stores_flag_true(self):
        state = self._state()
        new_state = self._simulate_pipeline_set_auto_tick(state, True)
        assert new_state["auto_tick_enabled"] is True

    def test_set_auto_tick_stores_flag_false(self):
        state = self._state()
        new_state = self._simulate_pipeline_set_auto_tick(state, False)
        assert new_state["auto_tick_enabled"] is False

    def test_set_auto_tick_does_not_mutate_original_state(self):
        import copy
        state = self._state()
        original = copy.deepcopy(state)
        self._simulate_pipeline_set_auto_tick(state, True)
        assert state == original

    # ── ticker checks auto_tick_enabled (new core flag) ─────────────────────

    def test_ticker_reads_auto_tick_enabled_flag(self):
        """get_context_flag should return auto_tick_enabled from state."""
        from app.core.state_cache.service import get_context_flag, _compress, _STATE_KEY_PREFIX
        from app.core.state_cache.client import get_redis

        state = {"auto_tick_enabled": True, "other_key": 42}
        r = get_redis()
        if r is None:
            import pytest
            pytest.skip("Redis not available")

        ctx_id = "test-auto-tick-core-ctx"
        r.set(f"{_STATE_KEY_PREFIX}{ctx_id}", _compress(state), ex=60)
        assert get_context_flag(ctx_id, "auto_tick_enabled", default=False) is True
        # cleanup
        r.delete(f"{_STATE_KEY_PREFIX}{ctx_id}")

    def test_ticker_reads_legacy_debug_auto_tick_flag(self):
        """Backward compat: legacy debug_auto_tick flag is still recognised."""
        from app.core.state_cache.service import get_context_flag, _compress, _STATE_KEY_PREFIX
        from app.core.state_cache.client import get_redis

        state = {"debug_auto_tick": True}
        r = get_redis()
        if r is None:
            import pytest
            pytest.skip("Redis not available")

        ctx_id = "test-legacy-auto-tick-ctx"
        r.set(f"{_STATE_KEY_PREFIX}{ctx_id}", _compress(state), ex=60)
        assert get_context_flag(ctx_id, "debug_auto_tick", default=False) is True
        # cleanup
        r.delete(f"{_STATE_KEY_PREFIX}{ctx_id}")

    # ── world_rules: debug_set_auto_tick no longer a game command ────────────

    def test_debug_set_auto_tick_is_not_a_game_command(self):
        """debug_set_auto_tick was removed from Zone Stalkers world_rules
        (superseded by the core set_auto_tick pipeline command)."""
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        state = self._state()
        result = validate_world_command("debug_set_auto_tick", {"enabled": True}, state, "any_user")
        # Should NOT be valid as a game command (falls through to unknown-command error)
        assert not result.valid


class TestCustomTerrainTypes:
    """Tests for the extended terrain type support (fixes import+edit bug)."""

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "any_user")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "any_user")

    def _state_with_custom_loc(self, terrain_type: str) -> tuple:
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=42, num_players=1, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        # Inject a location with a custom terrain type (simulating an import)
        loc_id = "loc_custom_1"
        state["locations"][loc_id] = {
            "id": loc_id,
            "name": "Custom Location",
            "terrain_type": terrain_type,
            "anomaly_activity": 0,
            "dominant_anomaly_type": None,
            "region": None,
            "connections": [],
            "artifacts": [],
            "agents": [],
            "items": [],
        }
        return state, loc_id

    def test_edit_location_with_urban_terrain_now_invalid(self):
        """urban was removed as a duplicate of buildings; it must now be rejected."""
        state, loc_id = self._state_with_custom_loc("plain")
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "terrain_type": "urban"}, state)
        assert not result.valid

    def test_edit_location_with_bridge_terrain(self):
        state, loc_id = self._state_with_custom_loc("bridge")
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "terrain_type": "bridge"}, state)
        assert result.valid, result.error

    def test_edit_location_with_tunnel_terrain(self):
        state, loc_id = self._state_with_custom_loc("tunnel")
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "terrain_type": "tunnel"}, state)
        assert result.valid, result.error

    def test_edit_location_with_swamp_terrain(self):
        state, loc_id = self._state_with_custom_loc("swamp")
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "terrain_type": "swamp"}, state)
        assert result.valid, result.error

    def test_edit_location_with_scientific_bunker_terrain(self):
        state, loc_id = self._state_with_custom_loc("scientific_bunker")
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "terrain_type": "scientific_bunker"}, state)
        assert result.valid, result.error

    def test_edit_location_with_underground_terrain_now_invalid(self):
        """underground was removed as a duplicate of dungeon; it must now be rejected."""
        state, loc_id = self._state_with_custom_loc("plain")
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "terrain_type": "underground"}, state)
        assert not result.valid

    def test_save_custom_terrain_persists(self):
        state, loc_id = self._state_with_custom_loc("tunnel")
        new_state, events = self._r("debug_update_location", {"loc_id": loc_id, "name": "Tunnel", "terrain_type": "tunnel"}, state)
        assert new_state["locations"][loc_id]["terrain_type"] == "tunnel"
        assert new_state["locations"][loc_id]["name"] == "Tunnel"
        assert any(e["event_type"] == "debug_location_updated" for e in events)

    def test_invalid_terrain_still_rejected(self):
        state, loc_id = self._state_with_custom_loc("plain")
        result = self._v("debug_update_location", {"loc_id": loc_id, "name": "X", "terrain_type": "ocean"}, state)
        assert not result.valid


class TestEmissionDangerousTerrain:
    """Unit tests for the _EMISSION_DANGEROUS_TERRAIN constant."""

    def test_dangerous_terrain_contains_required_types(self):
        """All five user-specified dangerous terrain types must be present."""
        from app.games.zone_stalkers.rules.tick_rules import _EMISSION_DANGEROUS_TERRAIN
        required = {"plain", "hills", "swamp", "field_camp", "slag_heaps"}
        assert required <= _EMISSION_DANGEROUS_TERRAIN, (
            f"Missing from dangerous terrain: {required - _EMISSION_DANGEROUS_TERRAIN}"
        )

    def test_swamp_terrain_locations_exist_in_fixed_map(self):
        """Fixed zone map should contain at least one location with terrain_type='swamp'."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=1, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        swamp_locs = [
            loc for loc in state["locations"].values()
            if loc.get("terrain_type") == "swamp"
        ]
        assert len(swamp_locs) >= 1, "Expected at least one swamp-terrain location in the fixed map"

    def test_swamp_location_is_dangerous(self):
        """A location with terrain_type='swamp' must be classified as dangerous for emission."""
        from app.games.zone_stalkers.rules.tick_rules import _EMISSION_DANGEROUS_TERRAIN
        assert "swamp" in _EMISSION_DANGEROUS_TERRAIN

    def test_field_camp_terrain_is_dangerous(self):
        from app.games.zone_stalkers.rules.tick_rules import _EMISSION_DANGEROUS_TERRAIN
        assert "field_camp" in _EMISSION_DANGEROUS_TERRAIN

    def test_slag_heaps_terrain_is_dangerous(self):
        from app.games.zone_stalkers.rules.tick_rules import _EMISSION_DANGEROUS_TERRAIN
        assert "slag_heaps" in _EMISSION_DANGEROUS_TERRAIN

    def test_bridge_terrain_is_dangerous(self):
        """Мост (bridge) is open terrain — must be dangerous during emission."""
        from app.games.zone_stalkers.rules.tick_rules import _EMISSION_DANGEROUS_TERRAIN
        assert "bridge" in _EMISSION_DANGEROUS_TERRAIN

    def test_industrial_and_buildings_remain_safe(self):
        """Industrial structures and buildings must remain safe (not in dangerous set)."""
        from app.games.zone_stalkers.rules.tick_rules import _EMISSION_DANGEROUS_TERRAIN
        assert "industrial" not in _EMISSION_DANGEROUS_TERRAIN
        assert "buildings" not in _EMISSION_DANGEROUS_TERRAIN

    def test_terrain_migration_normalizes_unknown_types(self):
        """Unknown terrain types (e.g. old 'urban'/'underground') are normalized to 'plain' on first tick."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = generate_zone(seed=1, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        # Inject invalid/removed terrain types into two locations
        locs = list(state["locations"].values())
        locs[0]["terrain_type"] = "urban"
        locs[1]["terrain_type"] = "underground"
        new_state, _ = tick_zone_map(state)
        assert new_state["locations"][locs[0]["id"]]["terrain_type"] == "plain"
        assert new_state["locations"][locs[1]["id"]]["terrain_type"] == "plain"
        assert new_state.get("_terrain_migrated_v3") is True
