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


def test_zone_stalkers_second_action_rejected(test_client):
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
