"""Tests for tick rules, scheduled actions, and zone events."""
import pytest


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _make_world(seed=42, num_bots=0):
    from app.games.zone_stalkers.generators.zone_generator import generate_zone
    state = generate_zone(seed=seed, num_players=1, num_ai_stalkers=num_bots, num_mutants=0, num_traders=0)
    state["player_agents"]["player1"] = "agent_p0"
    state["agents"]["agent_p0"]["controller"]["participant_id"] = "player1"
    return state


def _loc_ids(state):
    return list(state["locations"].keys())


class TestGeneratorExtensions:
    """New fields added to the generator."""

    def test_agent_has_scheduled_action(self):
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        assert "scheduled_action" in agent
        assert agent["scheduled_action"] is None

    def test_agent_has_memory(self):
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        assert "memory" in agent
        assert isinstance(agent["memory"], list)

    def test_world_has_hour_and_day(self):
        state = _make_world()
        assert "world_hour" in state
        assert "world_day" in state
        assert state["world_hour"] == 6
        assert state["world_day"] == 1

    def test_world_has_active_events(self):
        state = _make_world()
        assert "active_events" in state
        assert isinstance(state["active_events"], list)


class TestWorldRulesNewCommands:
    """Validate and resolve new scheduled-action commands."""

    def _v(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        return validate_world_command(cmd, payload, state, "player1")

    def _r(self, cmd, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(cmd, payload, state, "player1")

    # travel ─────────────────────────────────────────────────────

    def test_travel_to_reachable_location_valid(self):
        state = _make_world()
        locs = _loc_ids(state)
        # pick a non-current location
        agent_loc = state["agents"]["agent_p0"]["location_id"]
        target = next(l for l in locs if l != agent_loc)
        assert self._v("travel", {"target_location_id": target}, state).valid

    def test_travel_to_same_location_invalid(self):
        state = _make_world()
        loc = state["agents"]["agent_p0"]["location_id"]
        result = self._v("travel", {"target_location_id": loc}, state)
        assert not result.valid
        assert "Already" in result.error

    def test_travel_to_nonexistent_invalid(self):
        state = _make_world()
        assert not self._v("travel", {"target_location_id": "no_such_loc"}, state).valid

    def test_travel_schedules_action(self):
        state = _make_world()
        agent_loc = state["agents"]["agent_p0"]["location_id"]
        target = next(l for l in _loc_ids(state) if l != agent_loc)
        new_state, events = self._r("travel", {"target_location_id": target}, state)
        agent = new_state["agents"]["agent_p0"]
        assert agent["scheduled_action"] is not None
        assert agent["scheduled_action"]["type"] == "travel"
        assert agent["scheduled_action"]["turns_remaining"] >= 1
        assert any(e["event_type"] == "travel_started" for e in events)

    def test_travel_blocked_while_action_in_progress(self):
        state = _make_world()
        agent_loc = state["agents"]["agent_p0"]["location_id"]
        target = next(l for l in _loc_ids(state) if l != agent_loc)
        new_state, _ = self._r("travel", {"target_location_id": target}, state)
        # Trying to travel again should fail
        result = self._v("travel", {"target_location_id": target}, new_state)
        assert not result.valid
        assert "in progress" in result.error

    # explore_location ───────────────────────────────────────────

    def test_explore_valid(self):
        state = _make_world()
        assert self._v("explore_location", {}, state).valid

    def test_explore_schedules_action(self):
        state = _make_world()
        new_state, events = self._r("explore_location", {}, state)
        agent = new_state["agents"]["agent_p0"]
        assert agent["scheduled_action"]["type"] == "explore_anomaly_location"
        assert agent["scheduled_action"]["turns_remaining"] == 1
        assert any(e["event_type"] == "exploration_started" for e in events)

    # sleep ──────────────────────────────────────────────────────

    def test_sleep_in_safe_hub_valid(self):
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        # Move agent to a low-anomaly location (anomaly_activity <= 3 = "safe")
        safe_locs = [lid for lid, l in state["locations"].items() if l.get("anomaly_activity", 0) <= 3]
        if safe_locs:
            agent["location_id"] = safe_locs[0]
            assert self._v("sleep", {"hours": 6}, state).valid

    def test_sleep_in_dangerous_area_invalid(self):
        # Sleep is now valid everywhere (restriction removed); this test verifies it stays valid
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        wild_locs = [lid for lid, l in state["locations"].items() if l.get("anomaly_activity", 0) > 3]
        if wild_locs:
            agent["location_id"] = wild_locs[0]
            result = self._v("sleep", {"hours": 6}, state)
            assert result.valid  # sleep is allowed anywhere now

    def test_sleep_schedules_action(self):
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        safe_locs = [lid for lid, l in state["locations"].items() if l.get("anomaly_activity", 0) <= 3]
        if not safe_locs:
            pytest.skip("No low-anomaly location in generated world")
        agent["location_id"] = safe_locs[0]
        new_state, events = self._r("sleep", {"hours": 6}, state)
        scheduled = new_state["agents"]["agent_p0"]["scheduled_action"]
        assert scheduled["type"] == "sleep"
        assert scheduled["turns_remaining"] == 6 * 60  # 1 turn = 1 minute
        assert any(e["event_type"] == "sleep_started" for e in events)

    # join_event ─────────────────────────────────────────────────

    def test_join_event_invalid_when_no_active_event(self):
        state = _make_world()
        result = self._v("join_event", {"event_context_id": "fake-id"}, state)
        assert not result.valid

    def test_join_event_valid_with_active_event(self):
        state = _make_world()
        state["active_events"].append("evt-001")
        result = self._v("join_event", {"event_context_id": "evt-001"}, state)
        assert result.valid

    def test_join_event_schedules_action(self):
        state = _make_world()
        state["active_events"].append("evt-001")
        new_state, events = self._r("join_event", {"event_context_id": "evt-001"}, state)
        scheduled = new_state["agents"]["agent_p0"]["scheduled_action"]
        assert scheduled["type"] == "event"
        assert any(e["event_type"] == "event_joined" for e in events)


class TestTickRules:
    """World-turn advancement and scheduled action resolution."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    # Basic tick ─────────────────────────────────────────────────

    def test_tick_advances_world_turn(self):
        state = _make_world()
        initial_turn = state["world_turn"]
        new_state, _ = self._tick(state)
        assert new_state["world_turn"] == initial_turn + 1

    def test_tick_advances_world_hour(self):
        state = _make_world()
        initial_hour = state.get("world_hour", 0)
        # 1 tick = 1 minute; need 60 ticks to advance 1 hour
        for _ in range(60):
            state, _ = self._tick(state)
        assert state["world_hour"] == initial_hour + 1

    def test_tick_day_rollover(self):
        state = _make_world()
        state["world_hour"] = 23
        state["world_minute"] = 59
        state["world_day"] = 1
        new_state, events = self._tick(state)
        assert new_state["world_hour"] == 0
        assert new_state["world_day"] == 2
        assert any(e["event_type"] == "day_changed" for e in events)

    def test_tick_emits_world_turn_advanced_event(self):
        state = _make_world()
        _, events = self._tick(state)
        assert any(e["event_type"] == "world_turn_advanced" for e in events)

    def test_tick_resets_action_used(self):
        state = _make_world()
        state["agents"]["agent_p0"]["action_used"] = True
        new_state, _ = self._tick(state)
        assert not new_state["agents"]["agent_p0"]["action_used"]

    # Travel completion ──────────────────────────────────────────

    def test_travel_completes_after_N_ticks(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        agent_loc = state["agents"]["agent_p0"]["location_id"]
        target = next(l for l in _loc_ids(state) if l != agent_loc)
        # Schedule travel
        state, _ = resolve_world_command("travel", {"target_location_id": target}, state, "player1")
        turns = state["agents"]["agent_p0"]["scheduled_action"]["turns_remaining"]
        # Tick until travel completes
        for _ in range(turns):
            state, events = self._tick(state)
        # After all ticks, agent should be at destination
        assert state["agents"]["agent_p0"]["location_id"] == target
        assert state["agents"]["agent_p0"]["scheduled_action"] is None

    def test_travel_completion_emits_event(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        agent_loc = state["agents"]["agent_p0"]["location_id"]
        target = next(l for l in _loc_ids(state) if l != agent_loc)
        state, _ = resolve_world_command("travel", {"target_location_id": target}, state, "player1")
        turns = state["agents"]["agent_p0"]["scheduled_action"]["turns_remaining"]
        all_events = []
        for _ in range(turns):
            state, events = self._tick(state)
            all_events.extend(events)
        assert any(e["event_type"] == "travel_completed" for e in all_events)

    def test_travel_adds_memory_entry(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        agent_loc = state["agents"]["agent_p0"]["location_id"]
        target = next(l for l in _loc_ids(state) if l != agent_loc)
        state, _ = resolve_world_command("travel", {"target_location_id": target}, state, "player1")
        turns = state["agents"]["agent_p0"]["scheduled_action"]["turns_remaining"]
        for _ in range(turns):
            state, _ = self._tick(state)
        memory = state["agents"]["agent_p0"]["memory"]
        assert len(memory) > 0
        assert any(m["type"] == "action" for m in memory)

    # Explore completion ─────────────────────────────────────────

    def test_explore_completes_after_one_tick(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        state, _ = resolve_world_command("explore_location", {}, state, "player1")
        assert state["agents"]["agent_p0"]["scheduled_action"]["turns_remaining"] == 1
        state, events = self._tick(state)
        assert state["agents"]["agent_p0"]["scheduled_action"] is None
        assert any(e["event_type"] == "exploration_completed" for e in events)

    def test_explore_adds_memory(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        state, _ = resolve_world_command("explore_location", {}, state, "player1")
        state, _ = self._tick(state)
        memory = state["agents"]["agent_p0"]["memory"]
        assert len(memory) > 0
        assert any(m["type"] == "action" for m in memory)

    # Sleep completion ───────────────────────────────────────────

    def test_sleep_heals_hp(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        agent["hp"] = 50
        safe_locs = [lid for lid, l in state["locations"].items() if l.get("anomaly_activity", 0) <= 3]
        if not safe_locs:
            pytest.skip("No low-anomaly location in generated world")
        agent["location_id"] = safe_locs[0]
        state, _ = resolve_world_command("sleep", {"hours": 4}, state, "player1")
        # 1 turn = 1 minute; sleep(4 hours) = 240 ticks
        for _ in range(4 * 60):
            state, _ = self._tick(state)
        assert state["agents"]["agent_p0"]["hp"] > 50

    def test_sleep_reduces_radiation(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        agent["radiation"] = 50
        safe_locs = [lid for lid, l in state["locations"].items() if l.get("anomaly_activity", 0) <= 3]
        if not safe_locs:
            pytest.skip("No low-anomaly location in generated world")
        agent["location_id"] = safe_locs[0]
        state, _ = resolve_world_command("sleep", {"hours": 4}, state, "player1")
        # 1 turn = 1 minute; sleep(4 hours) = 240 ticks
        for _ in range(4 * 60):
            state, _ = self._tick(state)
        assert state["agents"]["agent_p0"]["radiation"] < 50

    def test_sleep_adds_memory(self):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        safe_locs = [lid for lid, l in state["locations"].items() if l.get("anomaly_activity", 0) <= 3]
        if not safe_locs:
            pytest.skip("No low-anomaly location in generated world")
        agent["location_id"] = safe_locs[0]
        state, _ = resolve_world_command("sleep", {"hours": 2}, state, "player1")
        # 1 turn = 1 minute; sleep(2 hours) = 120 ticks
        for _ in range(2 * 60):
            state, _ = self._tick(state)
        assert any(m["type"] == "action" for m in state["agents"]["agent_p0"]["memory"])


class TestDebugSetTime:
    """debug_set_time must update world_turn so NPC memory timestamps are correct."""

    def _r(self, payload, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command("debug_set_time", payload, state, "player1")

    def test_world_turn_updated_on_set_time(self):
        """After debug_set_time the world_turn must equal (day-1)*1440 + hour*60 + minute."""
        state = _make_world()
        state, events = self._r({"day": 2, "hour": 3, "minute": 15}, state)

        expected_turn = (2 - 1) * 24 * 60 + 3 * 60 + 15  # 1440 + 180 + 15 = 1635
        assert state["world_turn"] == expected_turn, (
            f"Expected world_turn={expected_turn}, got {state['world_turn']}"
        )
        # Event payload must also carry the new world_turn
        time_event = next((e for e in events if e["event_type"] == "debug_time_set"), None)
        assert time_event is not None
        assert time_event["payload"]["world_turn"] == expected_turn

    def test_world_day_hour_minute_unchanged_when_omitted(self):
        """Fields not in payload must not change; world_turn must reflect the surviving values."""
        state = _make_world()
        state["world_day"] = 5
        state["world_hour"] = 10
        state["world_minute"] = 30
        # Only override the hour
        state, _ = self._r({"hour": 22}, state)

        assert state["world_day"] == 5
        assert state["world_hour"] == 22
        assert state["world_minute"] == 30
        expected_turn = (5 - 1) * 24 * 60 + 22 * 60 + 30  # 5760 + 1320 + 30 = 7110
        assert state["world_turn"] == expected_turn

    def test_world_turn_day1_midnight(self):
        """Day 1, hour 0, minute 0 → world_turn = 0."""
        state = _make_world()
        state, _ = self._r({"day": 1, "hour": 0, "minute": 0}, state)
        assert state["world_turn"] == 0


class TestDebugDeleteAllItems:
    """debug_delete_all_items must clear items from location grounds and agent inventories."""

    def _r(self, state):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command("debug_delete_all_items", {}, state, "player1")

    def test_clears_ground_items(self):
        state = _make_world()
        # Plant items on every location
        for loc in state["locations"].values():
            loc["items"] = [
                {"id": "item_001", "type": "medkit", "name": "Аптечка", "weight": 1, "value": 500},
                {"id": "item_002", "type": "bandage", "name": "Бинт", "weight": 0.2, "value": 100},
            ]
        state, events = self._r(state)
        for loc in state["locations"].values():
            assert loc.get("items", []) == [], "Ground items must be cleared"
        ev = next(e for e in events if e["event_type"] == "debug_items_deleted")
        assert ev["payload"]["ground_count"] > 0

    def test_clears_agent_inventories(self):
        state = _make_world()
        for agent in state["agents"].values():
            agent["inventory"] = [
                {"id": "inv_001", "type": "medkit", "name": "Аптечка", "weight": 1, "value": 500},
            ]
        state, events = self._r(state)
        for agent in state["agents"].values():
            assert agent.get("inventory", []) == [], "Agent inventory must be cleared"
        ev = next(e for e in events if e["event_type"] == "debug_items_deleted")
        assert ev["payload"]["inventory_count"] > 0

    def test_event_payload_counts(self):
        state = _make_world()
        # Count initial items to set a baseline, then add more
        initial_ground = sum(len(loc.get("items", [])) for loc in state["locations"].values())
        initial_inv = sum(len(a.get("inventory", [])) for a in state["agents"].values())
        locs = list(state["locations"].values())
        locs[0]["items"].append({"id": "g_extra", "type": "medkit", "name": "X", "weight": 1, "value": 1})
        agents = list(state["agents"].values())
        agents[0].setdefault("inventory", []).extend([
            {"id": "a1", "type": "medkit", "name": "X", "weight": 1, "value": 1},
            {"id": "a2", "type": "bandage", "name": "Y", "weight": 0.2, "value": 1},
        ])
        state, events = self._r(state)
        ev = next(e for e in events if e["event_type"] == "debug_items_deleted")
        assert ev["payload"]["ground_count"] == initial_ground + 1
        assert ev["payload"]["inventory_count"] == initial_inv + 2

    def test_empty_world_no_error(self):
        """Command must succeed; counts equal the actual number of items in the world."""
        state = _make_world()
        state, events = self._r(state)
        ev = next(e for e in events if e["event_type"] == "debug_items_deleted")
        # counts must be non-negative integers (generator may place items on ground)
        assert ev["payload"]["ground_count"] >= 0
        assert ev["payload"]["inventory_count"] >= 0
        # After the command all locations and inventories must be empty
        for loc in state["locations"].values():
            assert loc.get("items", []) == []
        for agent in state["agents"].values():
            assert agent.get("inventory", []) == []


class TestEventRules:
    """Zone event context rules."""

    def _make_event_state(self, participants=None):
        from app.games.zone_stalkers.rules.event_rules import create_zone_event_state
        pids = participants or ["player1", "player2"]
        return create_zone_event_state(
            event_id="evt-001",
            title="Ambush at the Crossroads",
            description="Armed raiders block the road.",
            location_id="loc_0",
            participant_ids=pids,
            max_turns=3,
        )

    def test_create_event_state_structure(self):
        state = self._make_event_state()
        assert state["context_type"] == "zone_event"
        assert state["phase"] == "waiting"
        assert "player1" in state["participants"]
        assert state["current_turn"] == 0
        assert state["max_turns"] == 3

    def test_start_event_transitions_to_active(self):
        from app.games.zone_stalkers.rules.event_rules import start_event
        state = self._make_event_state()
        new_state, events = start_event(state)
        assert new_state["phase"] == "active"
        assert new_state["current_turn"] == 1
        assert len(new_state["current_options"]) >= 2
        assert any(e["event_type"] == "event_started" for e in events)

    def test_validate_choose_option_requires_active_phase(self):
        from app.games.zone_stalkers.rules.event_rules import validate_event_command
        state = self._make_event_state()  # still in "waiting"
        result = validate_event_command("choose_option", {"option_index": 0}, state, "player1")
        assert not result.valid

    def test_validate_choose_option_valid_after_start(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, validate_event_command
        state = self._make_event_state()
        state, _ = start_event(state)
        result = validate_event_command("choose_option", {"option_index": 0}, state, "player1")
        assert result.valid

    def test_validate_choose_option_out_of_range_invalid(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, validate_event_command
        state = self._make_event_state()
        state, _ = start_event(state)
        result = validate_event_command("choose_option", {"option_index": 99}, state, "player1")
        assert not result.valid

    def test_validate_nonparticipant_invalid(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, validate_event_command
        state = self._make_event_state()
        state, _ = start_event(state)
        result = validate_event_command("choose_option", {"option_index": 0}, state, "stranger")
        assert not result.valid

    def test_choose_option_records_choice(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, resolve_event_command
        state = self._make_event_state(participants=["player1"])
        state, _ = start_event(state)
        new_state, events = resolve_event_command("choose_option", {"option_index": 0}, state, "player1")
        assert any(e["event_type"] == "option_chosen" for e in events)

    def test_all_choose_advances_turn(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, resolve_event_command
        state = self._make_event_state(participants=["player1", "player2"])
        state, _ = start_event(state)
        state, _ = resolve_event_command("choose_option", {"option_index": 0}, state, "player1")
        state, events = resolve_event_command("choose_option", {"option_index": 1}, state, "player2")
        # Should advance turn or end event
        turn_advanced = any(e["event_type"] == "event_turn_advanced" for e in events)
        event_ended = any(e["event_type"] == "event_ended" for e in events)
        assert turn_advanced or event_ended

    def test_double_choose_blocked(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, validate_event_command, resolve_event_command
        # Two participants so the turn doesn't auto-advance after one choice
        state = self._make_event_state(participants=["player1", "player2"])
        state, _ = start_event(state)
        # Player1 chooses
        state, _ = resolve_event_command("choose_option", {"option_index": 0}, state, "player1")
        # Player1 trying to choose again should fail (choice already set this round)
        result = validate_event_command("choose_option", {"option_index": 0}, state, "player1")
        assert not result.valid

    def test_event_ends_after_max_turns(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, resolve_event_command
        # single player, max_turns=2
        from app.games.zone_stalkers.rules.event_rules import create_zone_event_state
        state = create_zone_event_state(
            event_id="evt-x", title="Short Event", description="",
            location_id="loc_0", participant_ids=["player1"], max_turns=2,
        )
        state, _ = start_event(state)
        all_events = []
        for _ in range(5):  # more than max_turns
            if state["phase"] == "ended":
                break
            state, evs = resolve_event_command("choose_option", {"option_index": 0}, state, "player1")
            all_events.extend(evs)
        assert state["phase"] == "ended"
        assert any(e["event_type"] == "event_ended" for e in all_events)

    def test_event_ended_has_memory_template(self):
        from app.games.zone_stalkers.rules.event_rules import create_zone_event_state, start_event, resolve_event_command
        state = create_zone_event_state(
            event_id="evt-mem", title="Memory Test", description="",
            location_id="loc_0", participant_ids=["player1"], max_turns=1,
        )
        state, _ = start_event(state)
        state, _ = resolve_event_command("choose_option", {"option_index": 0}, state, "player1")
        assert state["phase"] == "ended"
        assert state["memory_template"] is not None
        assert state["memory_template"]["type"] == "event"

    def test_leave_event(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, validate_event_command, resolve_event_command
        state = self._make_event_state(participants=["player1"])
        state, _ = start_event(state)
        state, events = resolve_event_command("leave_event", {}, state, "player1")
        assert state["participants"]["player1"]["status"] == "left"
        assert any(e["event_type"] == "participant_left_event" for e in events)

    def test_leave_event_all_left_ends_event(self):
        from app.games.zone_stalkers.rules.event_rules import start_event, resolve_event_command
        state = self._make_event_state(participants=["player1"])
        state, _ = start_event(state)
        state, events = resolve_event_command("leave_event", {}, state, "player1")
        assert state["phase"] == "ended"
        assert state["outcome"] == "abandoned"


class TestTickerAPIEndpoint:
    """Test the manual tick HTTP endpoint."""

    def test_manual_tick_requires_auth(self, test_client):
        import uuid
        response = test_client.post(f"/api/matches/{uuid.uuid4()}/tick")
        assert response.status_code == 401

    def test_manual_tick_nonexistent_match(self, test_client, auth_headers):
        import uuid
        response = test_client.post(f"/api/matches/{uuid.uuid4()}/tick", headers=auth_headers)
        assert response.status_code == 404

    def test_manual_tick_full_cycle(self, test_client, auth_headers):
        """Create match → start → create zone_map → tick → verify turn advanced."""
        # Create match
        resp = test_client.post("/api/matches", json={"game_id": "zone_stalkers"}, headers=auth_headers)
        assert resp.status_code in (200, 201)
        match = resp.json()
        match_id = match["id"]

        # Start match
        resp = test_client.post(f"/api/matches/{match_id}/start", headers=auth_headers)
        assert resp.status_code in (200, 201)

        # Create zone_map context
        resp = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=auth_headers)
        assert resp.status_code in (200, 201)

        # Tick
        resp = test_client.post(f"/api/matches/{match_id}/tick", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "world_turn" in data
        assert data["world_turn"] == 2  # started at 1, advanced by tick

    def test_tick_zone_event_endpoint(self, test_client, auth_headers):
        """Create match → zone_map → create zone_event → tick → event started."""
        resp = test_client.post("/api/matches", json={"game_id": "zone_stalkers"}, headers=auth_headers)
        assert resp.status_code in (200, 201)
        match_id = resp.json()["id"]

        resp = test_client.post(f"/api/matches/{match_id}/start", headers=auth_headers)
        assert resp.status_code in (200, 201)

        resp = test_client.post("/api/contexts", json={"match_id": match_id, "context_type": "zone_map"}, headers=auth_headers)
        assert resp.status_code in (200, 201)
        zone_ctx_id = resp.json()["id"]

        # Create a zone_event
        resp = test_client.post("/api/contexts/zone-event", json={
            "match_id": match_id,
            "zone_map_context_id": zone_ctx_id,
            "title": "Test Event",
            "description": "A test event",
            "max_turns": 3,
            "participant_ids": [],
        }, headers=auth_headers)
        assert resp.status_code in (200, 201)
        event_ctx_id = resp.json()["id"]

        # Zone-map active_events should contain the event
        resp = test_client.get(f"/api/matches/{match_id}/contexts", headers=auth_headers)
        ctxs = resp.json()
        zone_ctx = next(c for c in ctxs if c["context_type"] == "zone_map")
        assert event_ctx_id in zone_ctx["state_blob"]["active_events"]


# ─────────────────────────────────────────────────────────────────
# Needs degradation tests
# ─────────────────────────────────────────────────────────────────

class TestNeedsDegradation:
    """Verify hunger/thirst/sleepiness increase each tick."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def test_hunger_increases_each_tick(self):
        # Survival needs degrade once per in-game hour (every 60 ticks)
        state = _make_world()
        state["world_minute"] = 59  # next tick crosses hour boundary
        agent = state["agents"]["agent_p0"]
        initial_hunger = agent.get("hunger", 0)
        new_state, _ = self._tick(state)
        assert new_state["agents"]["agent_p0"]["hunger"] > initial_hunger

    def test_thirst_increases_each_tick(self):
        state = _make_world()
        state["world_minute"] = 59
        initial_thirst = state["agents"]["agent_p0"].get("thirst", 0)
        new_state, _ = self._tick(state)
        assert new_state["agents"]["agent_p0"]["thirst"] > initial_thirst

    def test_sleepiness_increases_each_tick(self):
        state = _make_world()
        state["world_minute"] = 59
        initial_sleep = state["agents"]["agent_p0"].get("sleepiness", 0)
        new_state, _ = self._tick(state)
        assert new_state["agents"]["agent_p0"]["sleepiness"] > initial_sleep

    def test_hunger_capped_at_100(self):
        state = _make_world()
        state["agents"]["agent_p0"]["hunger"] = 99
        state["agents"]["agent_p0"]["hp"] = 50  # avoid death from thirst
        state["agents"]["agent_p0"]["thirst"] = 0
        new_state, _ = self._tick(state)
        assert new_state["agents"]["agent_p0"]["hunger"] <= 100

    def test_thirst_critical_damages_hp(self):
        state = _make_world()
        state["world_minute"] = 59  # next tick crosses hour boundary → degradation applied
        state["agents"]["agent_p0"]["thirst"] = 80
        state["agents"]["agent_p0"]["hunger"] = 0
        initial_hp = state["agents"]["agent_p0"]["hp"]
        new_state, _ = self._tick(state)
        assert new_state["agents"]["agent_p0"]["hp"] < initial_hp

    def test_hunger_critical_damages_hp(self):
        state = _make_world()
        state["world_minute"] = 59  # next tick crosses hour boundary → degradation applied
        state["agents"]["agent_p0"]["hunger"] = 80
        state["agents"]["agent_p0"]["thirst"] = 0
        initial_hp = state["agents"]["agent_p0"]["hp"]
        new_state, _ = self._tick(state)
        assert new_state["agents"]["agent_p0"]["hp"] < initial_hp

    def test_sleep_resets_sleepiness(self):
        from app.games.zone_stalkers.rules.tick_rules import _resolve_sleep
        agent = {"hp": 60, "max_hp": 100, "radiation": 10, "sleepiness": 90}
        sched = {"hours": 6}  # hours field used by _resolve_sleep
        _resolve_sleep(agent, sched, 5, {})
        assert agent["sleepiness"] == 0

    def test_sleep_resolve_hours_field(self):
        """_resolve_sleep heals correctly when scheduled with the 'hours' key."""
        from app.games.zone_stalkers.rules.tick_rules import _resolve_sleep
        agent = {"hp": 50, "max_hp": 100, "radiation": 30, "sleepiness": 80, "memory": []}
        _resolve_sleep(agent, {"hours": 4}, 1, {"world_day": 1, "world_hour": 6, "world_minute": 0})
        assert agent["hp"] == min(50 + 15 * 4, 100)  # 110 → capped at max_hp=100
        assert agent["radiation"] == 30 - 5 * 4     # 10
        assert agent["sleepiness"] == 0

    def test_sleep_resolve_turns_total_fallback(self):
        """_resolve_sleep falls back to turns_total when 'hours' key is absent (legacy saves)."""
        from app.games.zone_stalkers.rules.tick_rules import _resolve_sleep, _HOUR_IN_TURNS
        agent = {"hp": 70, "max_hp": 100, "radiation": 20, "sleepiness": 75, "memory": []}
        # Simulate legacy scheduled action that used turns_total=360 (= 6 h × 60 turns/h)
        sched = {"turns_total": 6 * _HOUR_IN_TURNS}
        _resolve_sleep(agent, sched, 1, {"world_day": 1, "world_hour": 0, "world_minute": 0})
        # 6 hours → same result as hours=6
        assert agent["hp"] == 100          # 70 + 6*15 = 160 → capped at 100
        assert agent["radiation"] == 0     # 20 - 6*5 = -10 → clamped to 0
        assert agent["sleepiness"] == 0


# ─────────────────────────────────────────────────────────────────
# Action queue tests
# ─────────────────────────────────────────────────────────────────

class TestActionQueue:
    """Verify action_queue pops next action when scheduled_action completes."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def test_queue_pops_after_sleep_completes(self):
        state = _make_world()
        locs = _loc_ids(state)
        agent = state["agents"]["agent_p0"]
        agent_loc = agent["location_id"]
        target = next(l for l in locs if l != agent_loc)

        # Schedule a 1-turn sleep that completes next tick
        agent["scheduled_action"] = {
            "type": "sleep",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": agent_loc,
            "started_turn": 1,
        }
        # Queue a travel action next
        agent["action_queue"] = [{
            "type": "travel",
            "turns_remaining": 2,
            "turns_total": 2,
            "target_id": target,
            "route": [target],
            "started_turn": 1,
        }]

        new_state, events = self._tick(state)
        new_agent = new_state["agents"]["agent_p0"]
        # Sleep should be done; travel should now be the scheduled action
        assert new_agent["scheduled_action"] is not None
        assert new_agent["scheduled_action"]["type"] == "travel"
        assert new_agent["action_queue"] == []
        assert any(e["event_type"] == "queue_action_started" for e in events)

    def test_empty_queue_leaves_scheduled_action_none(self):
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        agent_loc = agent["location_id"]
        # Single sleep that completes; empty queue
        agent["scheduled_action"] = {
            "type": "sleep",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": agent_loc,
            "started_turn": 1,
        }
        agent["action_queue"] = []

        new_state, _ = self._tick(state)
        assert new_state["agents"]["agent_p0"]["scheduled_action"] is None


# ─────────────────────────────────────────────────────────────────
# Artifact → Sell → Trader scenario (ALIVe behaviour)
# ─────────────────────────────────────────────────────────────────

def _make_trader_scenario():
    """
    Build a minimal world state for the trader scenario:

    - One NPC stalker (global_goal="get_rich", wealth < threshold so
      it will try to gather resources / sell artifacts)
    - One trader in a DIFFERENT location (so the stalker has to travel)
    - ONLY our test artifact placed at the stalker's starting location

    Location layout (two connected locations):
        stalker_loc ──► trader_loc
    """
    from app.games.zone_stalkers.generators.zone_generator import generate_zone
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES

    # Generate a world with no bots/traders (we'll add them manually)
    state = generate_zone(seed=99, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
    # Offset world_turn so that the first exploration attempt uses an RNG seed that
    # guarantees a successful artifact pickup.
    #
    # Exploration is scheduled with turns_remaining=EXPLORE_DURATION_TURNS (30).
    # It resolves when turns_remaining reaches 0, at which point the world_turn is
    #   resolution_wt = initial_wt + EXPLORE_DURATION_TURNS
    # The exploration RNG is seeded with agent_id + str(resolution_wt):
    #   random.Random("bot_stalker" + str(resolution_wt)).random()
    #
    # With initial_wt=7 → resolution_wt=37:
    #   random.Random("bot_stalker37").random() ≈ 0.2393 < 0.5  → HIT
    #
    # This offset is necessary because any failed search now writes
    # `explore_confirmed_empty`, blocking all retries until an emission cycle
    # clears the block.  To recalculate: iterate initial_wt until
    # random.Random("bot_stalker" + str(initial_wt + EXPLORE_DURATION_TURNS)).random() < 0.5.
    state["world_turn"] = 7

    # Pick two connected locations
    locs = list(state["locations"].keys())
    stalker_loc = locs[0]
    # Find a connected neighbor for the trader
    conns = state["locations"][stalker_loc].get("connections", [])
    trader_loc = conns[0]["to"] if conns else locs[1]

    # Spawn the stalker
    from app.games.zone_stalkers.generators.zone_generator import _make_stalker_agent
    import random
    rng = random.Random(1)
    stalker = _make_stalker_agent(
        agent_id="bot_stalker",
        name="Test Stalker",
        location_id=stalker_loc,
        controller_kind="bot",
        participant_id=None,
        rng=rng,
    )
    stalker["global_goal"] = "get_rich"
    stalker["material_threshold"] = 999999  # always in "gather" phase initially
    stalker["inventory"] = []  # clear inventory so wealth is just money
    stalker["money"] = 100
    # Ensure equipment slots are fully filled so equipment-maintenance layer
    # does not interfere with the artifact-exploration scenario under test.
    stalker["equipment"] = {
        "weapon": {"id": "wpn_test", "type": "ak74", "name": "АК-74", "weight": 3.5, "value": 1500},
        "armor": {"id": "arm_test", "type": "stalker_suit", "name": "Комбинезон сталкера", "weight": 5.0, "value": 1500},
        "detector": None,
    }
    # Give the stalker ammo so the ammo-check layer is also satisfied
    stalker["inventory"] = [
        {"id": "ammo_test", "type": "ammo_545", "name": "Патроны 5.45х39 (30 шт.)", "weight": 0.3, "value": 100},
        {"id": "heal_test", "type": "bandage", "name": "Бинт", "weight": 0.1, "value": 50},
    ]
    state["agents"]["bot_stalker"] = stalker
    state["locations"][stalker_loc]["agents"].append("bot_stalker")

    # Place ONLY our test artifact at the stalker's location (clear any generator artifacts)
    art_type = "soul"
    art_info = ARTIFACT_TYPES[art_type]
    artifact = {
        "id": "art_test_001",
        "type": art_type,
        "name": art_info["name"],
        "value": art_info["value"],
    }
    state["locations"][stalker_loc]["artifacts"] = [artifact]  # replace, not append
    # Artifacts only spawn in anomaly zones; ensure the location has activity so exploration triggers
    state["locations"][stalker_loc].setdefault("anomaly_activity", 5)

    # Spawn a trader at trader_loc
    trader = {
        "id": "trader_test",
        "archetype": "trader_npc",
        "name": "Sidorovich",
        "location_id": trader_loc,
        "inventory": [],
        "money": 10000,
        "memory": [],
    }
    state.setdefault("traders", {})["trader_test"] = trader
    state["locations"][trader_loc]["agents"].append("trader_test")

    return state, "bot_stalker", "trader_test", stalker_loc, trader_loc, artifact


class TestArtifactToTraderScenario:
    """
    Verify that a bot stalker with global_goal='get_rich':
      1. Starts exploration (artifacts must be found via explore, not picked up directly)
      2. After exploration completes, artifact may be found and is recorded in memory
      3. Decides to travel to the trader's location once artifact is in inventory
      4. After arriving, sells the artifact to the trader (both sides record it in memory)

    Note: the sell action fires in the SAME tick as travel completion (bot AI
    decision step runs in the same tick immediately after the scheduled action
    clears), so we use _run_until_sold() to advance through the whole cycle.
    """

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def _run_until_artifact_acquired(self, state, sid, artifact_id, max_ticks=300):
        """Tick until the stalker has the artifact in inventory or max_ticks reached."""
        for _ in range(max_ticks):
            agent = state["agents"][sid]
            if any(i["id"] == artifact_id for i in agent.get("inventory", [])):
                return state
            state, _ = self._tick(state)
        return state

    def _run_until_sold(self, state, sid, max_ticks=400):
        """Tick until the stalker has a trade_sell (action) entry in memory or max_ticks."""
        for _ in range(max_ticks):
            agent = state["agents"][sid]
            if any(m["type"] == "action" and m["effects"].get("action_kind") == "trade_sell"
                   for m in agent.get("memory", [])):
                return state
            state, _ = self._tick(state)
        return state

    # ── Phase 1: Exploration start ────────────────────────────────────────────

    def test_tick1_starts_exploration(self):
        """Artifacts must be found via explore — NPC starts exploration on tick 1."""
        state, sid, *_ = _make_trader_scenario()
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have a scheduled action"
        assert sched["type"] == "explore_anomaly_location", (
            f"Agent should start exploring anomaly location, not '{sched['type']}' "
            "(artifacts must be obtained through explore, not direct pickup)"
        )
        assert any(e["event_type"] == "exploration_started" for e in events)

    def test_tick1_explore_decision_recorded_in_memory(self):
        """Decision to explore should be recorded on tick 1."""
        state, sid, *_ = _make_trader_scenario()
        new_state, _ = self._tick(state)
        agent = new_state["agents"][sid]
        explore_mems = [m for m in agent["memory"]
                        if m["type"] == "decision"
                        and m["effects"].get("action_kind") == "explore_decision"]
        assert len(explore_mems) >= 1

    def test_artifact_found_via_explore(self):
        """After explore completes, artifact must appear in inventory (via explore, not direct pickup)."""
        state, sid, _, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        state = self._run_until_artifact_acquired(state, sid, artifact["id"])
        agent = state["agents"][sid]
        assert any(i["id"] == artifact["id"] for i in agent.get("inventory", [])), \
            "Artifact should eventually be found via exploration"
        # Verify it was found through explore action, not direct pickup
        pickup_mems = [m for m in agent["memory"]
                       if m["type"] == "action" and m["effects"].get("action_kind") == "pickup"
                       and m["effects"].get("artifact_type") == "soul"]
        assert len(pickup_mems) >= 1
        mem = pickup_mems[0]
        assert mem["effects"]["artifact_value"] > 0

    # ── Phase 2: Travel decision toward trader ────────────────────────────────

    def test_decides_to_travel_to_trader_after_explore(self):
        """After explore finds the artifact, agent should plan to travel to trader."""
        state, sid, _, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        # Run until artifact is acquired via explore
        state = self._run_until_artifact_acquired(state, sid, artifact["id"])
        # One more tick for the travel decision
        new_state, _ = self._tick(state)
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have a scheduled travel action after acquiring artifact"
        assert sched["type"] == "travel"
        assert sched.get("final_target_id") == trader_loc

    def test_travel_decision_recorded_in_memory(self):
        """Travel-to-trader decision must be recorded in memory after artifact is found."""
        state, sid, _, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        state = self._run_until_artifact_acquired(state, sid, artifact["id"])
        # Run one more tick for travel decision
        new_state, _ = self._tick(state)
        agent = new_state["agents"][sid]
        decision_mems = [m for m in agent["memory"] if m["type"] == "decision"]
        assert len(decision_mems) >= 1
        # The artifacts_count memory is written in the trading opportunity check
        trader_decision_mems = [m for m in decision_mems
                                 if m["effects"].get("artifacts_count", 0) >= 1]
        assert len(trader_decision_mems) >= 1

    # ── Phase 3: Sell to trader ───────────────────────────────────────────────

    def test_sell_completes_full_cycle(self):
        """Full scenario: pickup → travel → sell."""
        state, sid, tid, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        initial_stalker_money = state["agents"][sid]["money"]
        initial_trader_money = state["traders"][tid]["money"]

        state = self._run_until_sold(state, sid)

        agent = state["agents"][sid]
        # Stalker reached trader location
        assert agent["location_id"] == trader_loc
        # Artifact gone from inventory
        assert not any(i["id"] == artifact["id"] for i in agent["inventory"])
        # Stalker earned money
        assert agent["money"] > initial_stalker_money
        # bot_sold_artifact event was emitted somewhere in the run
        # (checked via memory instead since events are per-tick)
        sell_mems = [m for m in agent["memory"]
                     if m["type"] == "action" and m["effects"].get("action_kind") == "trade_sell"]
        assert len(sell_mems) >= 1

    def test_sell_recorded_in_stalker_memory(self):
        state, sid, tid, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        state = self._run_until_sold(state, sid)

        agent = state["agents"][sid]
        sell_mems = [m for m in agent["memory"]
                     if m["type"] == "action" and m["effects"].get("action_kind") == "trade_sell"]
        assert len(sell_mems) >= 1
        mem = sell_mems[0]
        assert "soul" in mem["effects"]["items_sold"]
        assert mem["effects"]["money_gained"] > 0
        assert mem["effects"]["trader_id"] == tid

    def test_sell_recorded_in_trader_memory(self):
        state, sid, tid, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        state = self._run_until_sold(state, sid)

        trader = state["traders"][tid]
        buy_mems = [m for m in trader.get("memory", []) if m["type"] == "trade_buy"]
        assert len(buy_mems) >= 1
        mem = buy_mems[0]
        assert "soul" in mem["effects"]["items_bought"]
        assert mem["effects"]["money_spent"] > 0
        assert mem["effects"]["stalker_id"] == sid

    def test_trader_receives_artifact(self):
        state, sid, tid, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        state = self._run_until_sold(state, sid)

        trader = state["traders"][tid]
        assert any(i.get("type") == "soul" for i in trader["inventory"]), \
            "Trader should have the soul artifact after purchase"

    def test_trader_money_decreased(self):
        state, sid, tid, stalker_loc, trader_loc, artifact = _make_trader_scenario()
        initial_trader_money = state["traders"][tid]["money"]
        state = self._run_until_sold(state, sid)

        trader = state["traders"][tid]
        assert trader["money"] < initial_trader_money

    def test_stalker_memory_has_full_chain(self):
        """Stalker memory must contain pickup → decision → travel → trade_sell."""
        state, sid, *_ = _make_trader_scenario()
        state = self._run_until_sold(state, sid)

        agent = state["agents"][sid]
        mem_types = [m["type"] for m in agent["memory"]]
        action_kinds = [m["effects"].get("action_kind") for m in agent["memory"]
                        if m["type"] == "action"]
        assert "action" in mem_types  # at least one action entry (pickup, travel, or sell)
        assert "decision" in mem_types
        assert "pickup" in action_kinds
        assert "trade_sell" in action_kinds
        # Check ordering: pickup before travel_arrived before trade_sell
        def first_idx(kind):
            for i, m in enumerate(agent["memory"]):
                if m["type"] == "action" and m["effects"].get("action_kind") == kind:
                    return i
            return 999
        p = first_idx("pickup")
        t = first_idx("travel_arrived")
        s = first_idx("trade_sell")
        assert p < t < s, f"Expected pickup({p}) < travel_arrived({t}) < trade_sell({s})"

    # ── Debug spawn trader command ───────────────────────────────────────────

    def test_debug_spawn_trader_valid(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        state = generate_zone(seed=42, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        loc_id = next(iter(state["locations"]))
        result = validate_world_command("debug_spawn_trader", {"loc_id": loc_id}, state, "any")
        assert result.valid

    def test_debug_spawn_trader_invalid_no_loc(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.rules.world_rules import validate_world_command
        state = generate_zone(seed=42, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        result = validate_world_command("debug_spawn_trader", {}, state, "any")
        assert not result.valid

    def test_debug_spawn_trader_creates_trader_with_memory(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.rules.world_rules import validate_world_command, resolve_world_command
        state = generate_zone(seed=42, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        loc_id = next(iter(state["locations"]))
        new_state, events = resolve_world_command(
            "debug_spawn_trader", {"loc_id": loc_id, "name": "TestTrader"}, state, "any"
        )
        new_tid = (set(new_state["traders"]) - set(state["traders"])).pop()
        trader = new_state["traders"][new_tid]
        assert trader["name"] == "TestTrader"
        assert trader["location_id"] == loc_id
        assert "memory" in trader
        assert isinstance(trader["memory"], list)
        assert any(e["event_type"] == "debug_trader_spawned" for e in events)

    def test_trader_has_memory_field_from_generator(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=42, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=2)
        for tid, trader in state["traders"].items():
            assert "memory" in trader, f"Trader {tid} missing memory field"
            assert isinstance(trader["memory"], list)


# ─────────────────────────────────────────────────────────────────
# Per-turn location observations
# ─────────────────────────────────────────────────────────────────

class TestPerTurnObservations:
    """_write_location_observations is called every tick and uses deduplication."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def test_observation_written_when_other_agent_present(self):
        """An agent ticking in the same location as another agent writes a stalkers observation."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=42, num_players=2, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        # Force both agents to the same location
        agents = list(state["agents"].keys())
        a0, a1 = agents[0], agents[1]
        shared_loc = state["agents"][a0]["location_id"]
        state["agents"][a1]["location_id"] = shared_loc
        new_state, _ = self._tick(state)
        obs = [m for m in new_state["agents"][a0]["memory"]
               if m["type"] == "observation" and m["effects"].get("observed") == "stalkers"]
        assert len(obs) >= 1
        assert state["agents"][a1]["name"] in obs[0]["effects"]["names"]

    def test_no_artifact_observation_on_tick_arrival(self):
        """Artifacts at a location do NOT generate an 'artifacts' observation on arrival/tick.
        Artifact observations are only written when an artifact is found via explore."""
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        state["locations"][loc_id].setdefault("artifacts", []).append(
            {"id": "art_obs_001", "type": "fire", "value": 100}
        )
        new_state, _ = self._tick(state)
        obs = [m for m in new_state["agents"]["agent_p0"]["memory"]
               if m["type"] == "observation" and m["effects"].get("observed") == "artifacts"]
        assert len(obs) == 0, "Artifacts should NOT be observed passively; only via explore."

    def test_observation_deduplicated_on_second_tick(self):
        """Identical observations are NOT re-written on the next tick — deduplication works.
        Test uses loose items since artifacts are no longer passively observed on tick."""
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        state["locations"][loc_id].setdefault("items", []).append(
            {"id": "item_dedup_001", "type": "medkit", "value": 50}
        )
        state1, _ = self._tick(state)
        count_after_tick1 = sum(
            1 for m in state1["agents"]["agent_p0"]["memory"]
            if m["type"] == "observation" and m["effects"].get("observed") == "items"
        )
        state2, _ = self._tick(state1)
        count_after_tick2 = sum(
            1 for m in state2["agents"]["agent_p0"]["memory"]
            if m["type"] == "observation" and m["effects"].get("observed") == "items"
        )
        # Should NOT have added a duplicate entry on the second tick
        assert count_after_tick2 == count_after_tick1

    def test_no_artifact_observation_on_content_change(self):
        """Adding more artifacts to a location still does NOT generate artifact observations
        on tick. Only explore can produce artifact observations."""
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        state["locations"][loc_id].setdefault("artifacts", []).append(
            {"id": "art_change_001", "type": "fire", "value": 100}
        )
        state1, _ = self._tick(state)
        # Add a second artifact before the next tick
        state1["locations"][loc_id]["artifacts"].append(
            {"id": "art_change_002", "type": "soul", "value": 200}
        )
        state2, _ = self._tick(state1)
        obs = [m for m in state2["agents"]["agent_p0"]["memory"]
               if m["type"] == "observation" and m["effects"].get("observed") == "artifacts"]
        # No artifact observations should exist — not written on tick
        assert len(obs) == 0

    def test_trader_visible_in_stalker_observations(self):
        """Traders at the same location appear in the 'stalkers' observation entry."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=42, num_players=1, num_ai_stalkers=0,
                              num_mutants=0, num_traders=1)
        state["player_agents"]["player1"] = list(state["agents"].keys())[0]
        agent_id = state["player_agents"]["player1"]
        agent = state["agents"][agent_id]
        loc_id = agent["location_id"]
        # Move the trader to the agent's location
        trader_id = list(state["traders"].keys())[0]
        state["traders"][trader_id]["location_id"] = loc_id
        new_state, _ = self._tick(state)
        obs = [m for m in new_state["agents"][agent_id]["memory"]
               if m["type"] == "observation" and m["effects"].get("observed") == "stalkers"]
        trader_name = state["traders"][trader_id]["name"]
        assert any(trader_name in o["effects"]["names"] for o in obs)

    def test_no_observation_when_location_empty(self):
        """No observation entries are written when the location is completely empty."""
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        # Ensure location is clean
        state["locations"][loc_id]["artifacts"] = []
        state["locations"][loc_id]["items"] = []
        state["locations"][loc_id].pop("agents", None)
        new_state, _ = self._tick(state)
        obs = [m for m in new_state["agents"]["agent_p0"]["memory"]
               if m["type"] == "observation"]
        assert len(obs) == 0


# ─────────────────────────────────────────────────────────────────
# Travel hop action memory
# ─────────────────────────────────────────────────────────────────

class TestTravelHopActionMemory:
    """Each intermediate travel hop must be recorded as an 'action' memory entry."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def _setup_two_hop_travel(self):
        """Create a 3-location chain (A→B→C) and start agent travelling A→B→C."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        state = generate_zone(seed=42, num_players=1, num_ai_stalkers=0,
                              num_mutants=0, num_traders=0)
        locs = list(state["locations"].keys())
        # Use first three locations; wire A→B→C connections
        a, b, c = locs[0], locs[1], locs[2]
        for lid in (a, b, c):
            state["locations"][lid]["connections"] = []
        state["locations"][a]["connections"] = [{"to": b, "travel_time": 1}]
        state["locations"][b]["connections"] = [{"to": c, "travel_time": 1}]

        agent_id = list(state["agents"].keys())[0]
        agent = state["agents"][agent_id]
        agent["location_id"] = a
        agent["memory"] = []
        # Schedule a 2-hop journey: immediate target = B, final = C, remaining = [C]
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": b,
            "final_target_id": c,
            "remaining_route": [c],
            "started_turn": state.get("world_turn", 1),
        }
        return state, agent_id, a, b, c

    def test_hop_recorded_as_action(self):
        """Completing an intermediate hop writes an action with action_kind='travel_hop'."""
        state, agent_id, a, b, c = self._setup_two_hop_travel()
        new_state, _ = self._tick(state)
        agent = new_state["agents"][agent_id]
        hop_mems = [m for m in agent["memory"]
                    if m["type"] == "action"
                    and m["effects"].get("action_kind") == "travel_hop"]
        assert len(hop_mems) >= 1
        assert hop_mems[0]["effects"]["to_loc"] == b
        assert hop_mems[0]["effects"]["final_target"] == c

    def test_hop_title_contains_location_name(self):
        """The hop action title contains the name of the intermediate location."""
        state, agent_id, a, b, c = self._setup_two_hop_travel()
        b_name = state["locations"][b].get("name", b)
        new_state, _ = self._tick(state)
        agent = new_state["agents"][agent_id]
        hop_mems = [m for m in agent["memory"]
                    if m["type"] == "action"
                    and m["effects"].get("action_kind") == "travel_hop"]
        assert any(b_name in m["title"] for m in hop_mems)

    def test_final_arrival_still_recorded(self):
        """After all hops, the final arrival is still recorded as travel_arrived."""
        state, agent_id, a, b, c = self._setup_two_hop_travel()
        # Tick once to complete A→B hop (schedules B→C), then tick again to complete B→C
        state, _ = self._tick(state)
        new_state, _ = self._tick(state)
        agent = new_state["agents"][agent_id]
        arrived = [m for m in agent["memory"]
                   if m["type"] == "action"
                   and m["effects"].get("action_kind") == "travel_arrived"]
        assert len(arrived) >= 1
        assert arrived[-1]["effects"]["to_loc"] == c


# ─────────────────────────────────────────────────────────────────
# Unreachable-target handling
# ─────────────────────────────────────────────────────────────────

def _make_minimal_state(locations_cfg, agent_loc_id="A"):
    """Build a minimal game state with only the provided locations and one stalker agent.

    *locations_cfg* is a dict:
        { loc_id: {"connections": [...], "artifacts": [...], ...}, ... }
    All other state fields are set to safe defaults.
    """
    locations = {}
    for lid, cfg in locations_cfg.items():
        locations[lid] = {
            "name": f"Loc {lid}",
            "agents": [],
            "mutants": [],
            "artifacts": cfg.get("artifacts", []),
            "anomaly_activity": 0,
            "connections": cfg.get("connections", []),
        }
    agent_id = "test_agent"
    agent = {
        "id": agent_id,
        "name": "Test Stalker",
        "location_id": agent_loc_id,
        "hp": 100,
        "hunger": 0,
        "thirst": 0,
        "sleepiness": 0,
        "is_alive": True,
        "inventory": [],
        "money": 0,
        "memory": [],
        "scheduled_action": None,
        "action_used": False,
        "controller": {"kind": "bot", "participant_id": None},
        "global_goal": "get_rich",
        "current_goal": None,
        "material_threshold": 1000,
        "risk_tolerance": 0.5,
        "faction": "loner",
    }
    locations[agent_loc_id].setdefault("agents", []).append(agent_id)
    return {
        "locations": locations,
        "agents": {agent_id: agent},
        "traders": {},
        "mutants": {},
        "world_turn": 1,
        "world_day": 1,
        "world_hour": 6,
        "world_minute": 0,
        "max_turns": 0,
        "active_events": [],
        "player_agents": {},
    }


class TestUnreachableTargetHandling:
    """Tests for closed-connection / unreachable-target scenarios."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    # ── Part 1: _bfs_route respects closed connections ─────────────────────────

    def test_bfs_route_skips_closed_connection(self):
        """_bfs_route returns [] when the only path uses a closed connection."""
        from app.games.zone_stalkers.rules.world_rules import _bfs_route
        state = _make_minimal_state({
            "A": {"connections": [{"to": "B", "travel_time": 1, "closed": True}]},
            "B": {"connections": [{"to": "C", "travel_time": 1}]},
            "C": {},
        }, agent_loc_id="A")
        route = _bfs_route(state["locations"], "A", "C")
        assert route == [], f"Expected no route, got {route}"

    def test_bfs_route_uses_open_connection(self):
        """_bfs_route succeeds when connections are open."""
        from app.games.zone_stalkers.rules.world_rules import _bfs_route
        state = _make_minimal_state({
            "A": {"connections": [{"to": "B", "travel_time": 1}]},
            "B": {"connections": [{"to": "C", "travel_time": 1}]},
            "C": {},
        }, agent_loc_id="A")
        route = _bfs_route(state["locations"], "A", "C")
        assert route == ["B", "C"]

    def test_bfs_route_finds_alternative_around_closed(self):
        """_bfs_route finds an alternate path when one connection is closed."""
        from app.games.zone_stalkers.rules.world_rules import _bfs_route
        state = _make_minimal_state({
            "A": {"connections": [
                {"to": "B", "travel_time": 1, "closed": True},
                {"to": "C", "travel_time": 2},
            ]},
            "B": {"connections": [{"to": "C", "travel_time": 1}]},
            "C": {},
        }, agent_loc_id="A")
        route = _bfs_route(state["locations"], "A", "C")
        assert route == ["C"], "Should reach C via direct connection, skipping closed B path"

    # ── Part 2: _find_richest_artifact_location respects reachability ──────────

    def test_find_richest_skips_unreachable_location(self):
        """When a rich artifact location is cut off by a closed connection, it is ignored."""
        from app.games.zone_stalkers.rules.tick_rules import _find_richest_artifact_location
        state = _make_minimal_state({
            "A": {"connections": [{"to": "B", "travel_time": 1, "closed": True}]},
            "B": {"connections": [{"to": "C", "travel_time": 1}],
                  "artifacts": [{"id": "art1", "type": "fireball", "value": 9999}]},
            "C": {},
        }, agent_loc_id="A")
        best_id, best_val = _find_richest_artifact_location(
            state, exclude_loc_id="A", from_loc_id="A"
        )
        assert best_id is None, f"Expected None, got {best_id}"

    def test_find_richest_returns_reachable_location(self):
        """When a rich artifact location is reachable, it is returned."""
        from app.games.zone_stalkers.rules.tick_rules import _find_richest_artifact_location
        state = _make_minimal_state({
            "A": {"connections": [{"to": "B", "travel_time": 1}]},
            "B": {"connections": [{"to": "C", "travel_time": 1}]},
            "C": {"artifacts": [{"id": "art2", "type": "fireball", "value": 500}]},
        }, agent_loc_id="A")
        best_id, best_val = _find_richest_artifact_location(
            state, exclude_loc_id="A", from_loc_id="A"
        )
        assert best_id == "C"
        assert best_val == 500

    def test_find_richest_prefers_reachable_over_richer_unreachable(self):
        """A reachable location with lower value beats an unreachable richer one."""
        from app.games.zone_stalkers.rules.tick_rules import _find_richest_artifact_location
        state = _make_minimal_state({
            "A": {"connections": [{"to": "B", "travel_time": 1}]},
            # B is reachable but low value
            "B": {"artifacts": [{"id": "art1", "type": "sparks", "value": 100}]},
            # C is unreachable (no connection from A or B to C)
            "C": {"artifacts": [{"id": "art2", "type": "fireball", "value": 9999}]},
        }, agent_loc_id="A")
        best_id, best_val = _find_richest_artifact_location(
            state, exclude_loc_id="A", from_loc_id="A"
        )
        assert best_id == "B"
        assert best_val == 100

    # ── Part 3: Mid-travel blockage detection ──────────────────────────────────

    def _state_agent_at_a_travelling_to_c_via_b(self, close_bc=False):
        """Agent at A, scheduled: target_id=B, final_target_id=C, remaining=[C]."""
        state = _make_minimal_state({
            "A": {"connections": [{"to": "B", "travel_time": 1}]},
            "B": {"connections": [{"to": "C", "travel_time": 1, "closed": close_bc}]},
            "C": {},
        }, agent_loc_id="A")
        agent = state["agents"]["test_agent"]
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": "B",
            "final_target_id": "C",
            "remaining_route": ["C"],
            "started_turn": 1,
        }
        return state

    def test_mid_travel_abort_writes_decision_memory(self):
        """When next hop is blocked and target is unreachable, a decision memory is written."""
        state = self._state_agent_at_a_travelling_to_c_via_b(close_bc=True)
        new_state, _ = self._tick(state)
        agent = new_state["agents"]["test_agent"]
        decision_mems = [
            m for m in agent["memory"]
            if m["type"] == "decision"
            and m["effects"].get("action_kind") == "goal_cancelled"
        ]
        assert len(decision_mems) >= 1, "Expected a goal_cancelled decision memory"
        assert decision_mems[0]["effects"]["cancelled_target"] == "C"

    def test_mid_travel_abort_emits_travel_aborted_event(self):
        """When route is blocked, a travel_aborted event is emitted."""
        state = self._state_agent_at_a_travelling_to_c_via_b(close_bc=True)
        new_state, events = self._tick(state)
        aborted = [e for e in events if e["event_type"] == "travel_aborted"]
        assert len(aborted) >= 1
        assert aborted[0]["payload"]["final_target"] == "C"

    def test_mid_travel_abort_clears_scheduled_action(self):
        """After aborting, the agent is no longer travelling toward C."""
        state = self._state_agent_at_a_travelling_to_c_via_b(close_bc=True)
        new_state, _ = self._tick(state)
        agent = new_state["agents"]["test_agent"]
        sa = agent.get("scheduled_action")
        # The travel-toward-C must be gone; if a new action was scheduled by the
        # bot AI it must NOT be a travel with final_target_id == "C".
        if sa is not None and sa.get("type") == "travel":
            assert sa.get("final_target_id") != "C", \
                "Agent should not be travelling toward the unreachable target anymore"

    def test_mid_travel_normal_continues(self):
        """When B→C is open, travel continues normally."""
        state = self._state_agent_at_a_travelling_to_c_via_b(close_bc=False)
        new_state, events = self._tick(state)
        aborted = [e for e in events if e["event_type"] == "travel_aborted"]
        assert len(aborted) == 0, "Should not abort when route is clear"
        agent = new_state["agents"]["test_agent"]
        sa = agent.get("scheduled_action")
        assert sa is not None, "Agent should still be travelling"
        assert sa["target_id"] == "C"

    def test_mid_travel_reroutes_when_alternative_exists(self):
        """When B→C is blocked but B→D→C is open, agent re-routes and writes a decision memory."""
        state = _make_minimal_state({
            "A": {"connections": [{"to": "B", "travel_time": 1}]},
            "B": {"connections": [
                {"to": "C", "travel_time": 1, "closed": True},
                {"to": "D", "travel_time": 1},
            ]},
            "C": {},
            "D": {"connections": [{"to": "C", "travel_time": 1}]},
        }, agent_loc_id="A")
        agent = state["agents"]["test_agent"]
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": "B",
            "final_target_id": "C",
            "remaining_route": ["C"],
            "started_turn": 1,
        }
        new_state, events = self._tick(state)
        aborted = [e for e in events if e["event_type"] == "travel_aborted"]
        assert len(aborted) == 0, "Should not abort when an alternative route exists"
        agent_after = new_state["agents"]["test_agent"]
        sa = agent_after.get("scheduled_action")
        assert sa is not None, "Agent should still be travelling"
        assert sa["final_target_id"] == "C"
        # Re-route should write a decision memory entry
        reroute_mems = [
            m for m in agent_after["memory"]
            if m["type"] == "decision"
            and m["effects"].get("action_kind") == "route_changed"
        ]
        assert len(reroute_mems) >= 1, "Expected a route_changed decision memory"
        assert reroute_mems[0]["effects"]["final_target"] == "C"
        assert reroute_mems[0]["effects"]["rerouted_at"] == "B"



# ─────────────────────────────────────────────────────────────────
# Equipment maintenance mechanic tests
# ─────────────────────────────────────────────────────────────────

def _make_bare_stalker_state(with_trader: bool = False, weapon: str | None = None, armor: str | None = None):
    """Build a minimal world with one bot stalker that has no weapon/armor."""
    from app.games.zone_stalkers.generators.zone_generator import generate_zone, _make_stalker_agent
    from app.games.zone_stalkers.balance.items import ITEM_TYPES
    import random

    state = generate_zone(seed=7, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
    locs = list(state["locations"].keys())
    stalker_loc = locs[0]

    rng = random.Random(2)
    stalker = _make_stalker_agent(
        agent_id="bot_bare",
        name="Bare Stalker",
        location_id=stalker_loc,
        controller_kind="bot",
        participant_id=None,
        rng=rng,
    )

    def _item(item_type: str, item_id: str) -> dict:
        info = ITEM_TYPES[item_type]
        return {"id": item_id, "type": item_type, "name": info["name"],
                "weight": info.get("weight", 0), "value": info.get("value", 0)}

    # Strip equipment and inventory for deterministic tests
    stalker["inventory"] = [
        _item("ammo_545", "ammo_t"),
        _item("bandage", "heal_t"),
        _item("bread", "food_t"),
        _item("water", "water_t"),
    ]
    stalker["equipment"] = {"weapon": None, "armor": None, "detector": None}
    stalker["money"] = 2000
    stalker["hp"] = 100
    stalker["hunger"] = 20
    stalker["thirst"] = 20
    stalker["sleepiness"] = 10
    stalker["global_goal"] = "get_rich"
    stalker["material_threshold"] = 999999

    if weapon:
        info = ITEM_TYPES[weapon]
        stalker["equipment"]["weapon"] = {
            "id": "wpn_preset", "type": weapon, "name": info["name"],
            "weight": info.get("weight", 1.0), "value": info.get("value", 500),
        }
    if armor:
        info = ITEM_TYPES[armor]
        stalker["equipment"]["armor"] = {
            "id": "arm_preset", "type": armor, "name": info["name"],
            "weight": info.get("weight", 2.0), "value": info.get("value", 300),
        }

    state["agents"]["bot_bare"] = stalker
    state["locations"][stalker_loc]["agents"].append("bot_bare")

    if with_trader:
        conns = state["locations"][stalker_loc].get("connections", [])
        trader = {
            "id": "trader_bare",
            "archetype": "trader_npc",
            "name": "Trader",
            "location_id": stalker_loc,  # trader at same location
            "inventory": [],
            "money": 10000,
            "memory": [],
            "is_alive": True,
        }
        state.setdefault("traders", {})["trader_bare"] = trader
        state["locations"][stalker_loc]["agents"].append("trader_bare")

    return state, "bot_bare", stalker_loc


class TestEquipmentMaintenance:
    """Verify that bots properly maintain their equipment."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def test_equip_weapon_from_inventory(self):
        """Bot should equip a weapon from inventory into the weapon slot."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state()
        # Put a weapon in inventory using ITEM_TYPES as source of truth
        info = ITEM_TYPES["pistol"]
        state["agents"][sid]["inventory"].append(
            {"id": "wpn_inv", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        )
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        assert agent["equipment"].get("weapon") is not None, "Weapon should be equipped from inventory"
        assert agent["equipment"]["weapon"]["type"] == "pistol"
        assert not any(i["id"] == "wpn_inv" for i in agent["inventory"]), "Weapon removed from inventory"
        assert any(e["event_type"] == "item_equipped" for e in events)

    def test_equip_armor_from_inventory(self):
        """Bot should equip armor from inventory when no armor is equipped."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state(weapon="pistol")
        info = ITEM_TYPES["leather_jacket"]
        state["agents"][sid]["inventory"].append(
            {"id": "arm_inv", "type": "leather_jacket", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        )
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        assert agent["equipment"].get("armor") is not None, "Armor should be equipped from inventory"
        assert agent["equipment"]["armor"]["type"] == "leather_jacket"
        assert any(e["event_type"] == "item_equipped" for e in events)

    def test_pickup_weapon_from_ground(self):
        """Bot should pick up a weapon from the ground when not equipped."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state()
        # Place weapon on ground using ITEM_TYPES
        info = ITEM_TYPES["pistol"]
        state["locations"][loc_id]["items"] = [
            {"id": "wpn_ground", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        # Weapon should be in inventory now (equip from inventory happens on NEXT tick)
        loc_items = new_state["locations"][loc_id]["items"]
        assert not any(i["id"] == "wpn_ground" for i in loc_items), "Weapon removed from ground"
        assert any(e["event_type"] == "item_picked_up" for e in events)
        # weapon is in inventory (equip happens next tick)
        assert any(i["id"] == "wpn_ground" for i in agent["inventory"])

    def test_pickup_armor_from_ground(self):
        """Bot should pick up armor from the ground when not equipped (weapon already set)."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state(weapon="pistol")
        info = ITEM_TYPES["leather_jacket"]
        state["locations"][loc_id]["items"] = [
            {"id": "arm_ground", "type": "leather_jacket", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        new_state, events = self._tick(state)
        loc_items = new_state["locations"][loc_id]["items"]
        assert not any(i["id"] == "arm_ground" for i in loc_items), "Armor removed from ground"
        assert any(e["event_type"] == "item_picked_up" for e in events)

    def test_travel_to_trader_for_weapon_when_no_ground_items(self):
        """Bot should travel to a trader if no weapon in inventory or on ground,
        but ONLY when wealth >= material_threshold (Phase 2 / buy is unlocked)."""
        state, sid, loc_id = _make_bare_stalker_state(with_trader=False)
        # Put agent into Phase 2 so the equipment-buy step is allowed
        state["agents"][sid]["material_threshold"] = 100  # wealth (2000) >> threshold (100)
        conns = state["locations"][loc_id].get("connections", [])
        if conns:
            trader_loc = conns[0]["to"]
            trader = {
                "id": "tr_distant",
                "archetype": "trader_npc",
                "name": "Far Trader",
                "location_id": trader_loc,
                "inventory": [],
                "money": 10000,
                "memory": [],
                "is_alive": True,
            }
            state.setdefault("traders", {})["tr_distant"] = trader
            state["locations"][trader_loc]["agents"].append("tr_distant")

            new_state, events = self._tick(state)
            agent = new_state["agents"][sid]
            sched = agent.get("scheduled_action")
            # Should be traveling toward the trader
            assert sched is not None
            assert sched["type"] == "travel"
            goal = agent.get("current_goal")
            assert goal in ("get_weapon", "get_armor"), f"Expected get_weapon or get_armor goal, got {goal}"

    def test_equipment_buy_skipped_in_phase1(self):
        """Bot should NOT travel to a trader for equipment when wealth < threshold (Phase 1)."""
        state, sid, loc_id = _make_bare_stalker_state(with_trader=False)
        # material_threshold=999999 → agent is firmly in Phase 1 (wealth < threshold)
        # (this is the default in _make_bare_stalker_state)
        conns = state["locations"][loc_id].get("connections", [])
        if not conns:
            return
        trader_loc = conns[0]["to"]
        state.setdefault("traders", {})["tr_phase1"] = {
            "id": "tr_phase1", "archetype": "trader_npc", "name": "Phase1 Trader",
            "location_id": trader_loc, "inventory": [], "money": 10000,
            "memory": [], "is_alive": True,
        }
        state["locations"][trader_loc]["agents"].append("tr_phase1")
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        # Bot should NOT be traveling toward trader to buy equipment in Phase 1
        if sched and sched["type"] == "travel":
            final_target = sched.get("final_target_id", sched.get("target_id"))
            assert final_target != trader_loc, (
                "Phase-1 bot should not travel to a trader to buy equipment; "
                f"it should gather resources instead. Target: {final_target!r}"
            )

    def test_buy_weapon_from_local_trader(self):
        """Bot should buy a weapon from a local trader when wealth >= threshold."""
        state, sid, loc_id = _make_bare_stalker_state(with_trader=True)
        # Set threshold below wealth so the equipment-buy step fires
        state["agents"][sid]["money"] = 3000
        state["agents"][sid]["material_threshold"] = 100  # wealth (3000) >> threshold (100)
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        # Should have bought a weapon
        assert any(e["event_type"] == "bot_bought_item" for e in events), \
            "Bot should have bought an item from trader when wealth >= threshold"

    def test_seek_item_from_memory(self):
        """Bot should travel to a location remembered as having a weapon."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state()
        conns = state["locations"][loc_id].get("connections", [])
        if not conns:
            return  # Skip if no connections
        mem_loc = conns[0]["to"]
        # Place weapon at mem_loc using ITEM_TYPES for consistency
        info = ITEM_TYPES["pistol"]
        state["locations"][mem_loc]["items"] = [
            {"id": "wpn_mem", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        # Give agent a memory of seeing that item at mem_loc
        state["agents"][sid]["memory"] = [{
            "world_turn": 1,
            "type": "observation",
            "title": "Вижу предметы",
            "summary": "На земле: pistol",
            "effects": {"observed": "items", "location_id": mem_loc, "item_types": ["pistol"]},
        }]
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have a scheduled action"
        assert sched["type"] == "travel", "Agent should travel toward remembered item location"
        assert agent.get("current_goal") == "get_weapon"

    def test_no_equipment_maintenance_when_fully_equipped(self):
        """Bot with full equipment should skip the equipment maintenance layer."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state(weapon="pistol", armor="leather_jacket")
        # Ensure ammo is present (pistol uses 9x18 ammo)
        info_ammo = ITEM_TYPES["ammo_9mm"]
        info_heal = ITEM_TYPES["bandage"]
        state["agents"][sid]["inventory"] = [
            {"id": "ammo_9mm_t", "type": "ammo_9mm", "name": info_ammo["name"],
             "weight": info_ammo["weight"], "value": info_ammo["value"]},
            {"id": "heal_t2", "type": "bandage", "name": info_heal["name"],
             "weight": info_heal["weight"], "value": info_heal["value"]},
        ]
        new_state, events = self._tick(state)
        # No item_equipped or item_picked_up events should fire
        assert not any(e["event_type"] == "item_equipped" for e in events), \
            "Should not equip when already equipped"
        assert not any(e["event_type"] == "item_picked_up" for e in events), \
            "Should not pick up when already equipped"

    def test_decision_tree_shows_equipment_layer(self):
        """_describe_bot_decision_tree should include the equipment maintenance layer."""
        from app.games.zone_stalkers.rules.tick_rules import _describe_bot_decision_tree
        state, sid, loc_id = _make_bare_stalker_state()
        agent = state["agents"][sid]
        tree = _describe_bot_decision_tree(agent, [], state)
        layer_names = [l["name"] for l in tree["layers"]]
        assert any("СНАРЯЖЕНИЕ" in n for n in layer_names), \
            f"Equipment layer missing from decision tree. Layers: {layer_names}"
        equip_layer = next(l for l in tree["layers"] if "СНАРЯЖЕНИЕ" in l["name"])
        assert equip_layer["skipped"] is False, "Equipment layer should NOT be skipped when agent has no weapon"

    def test_items_constants_are_correct(self):
        """Verify derived item-type constants are consistent with ITEM_TYPES."""
        from app.games.zone_stalkers.balance.items import (
            ITEM_TYPES, WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES,
            AMMO_ITEM_TYPES, AMMO_FOR_WEAPON, HEAL_ITEM_TYPES,
            FOOD_ITEM_TYPES, DRINK_ITEM_TYPES,
        )
        assert "ak74" in WEAPON_ITEM_TYPES
        assert "pistol" in WEAPON_ITEM_TYPES
        assert "shotgun" in WEAPON_ITEM_TYPES
        assert "stalker_suit" in ARMOR_ITEM_TYPES
        assert "leather_jacket" in ARMOR_ITEM_TYPES
        assert "ammo_545" in AMMO_ITEM_TYPES
        assert AMMO_FOR_WEAPON["ak74"] == "ammo_545"
        assert AMMO_FOR_WEAPON["pistol"] == "ammo_9mm"
        assert AMMO_FOR_WEAPON["shotgun"] == "ammo_12gauge"
        assert "medkit" in HEAL_ITEM_TYPES
        assert "canned_food" in FOOD_ITEM_TYPES
        assert "water" in DRINK_ITEM_TYPES
        # All weapon types have an ammo mapping
        for w in WEAPON_ITEM_TYPES:
            assert w in AMMO_FOR_WEAPON, f"Weapon {w} missing from AMMO_FOR_WEAPON"

    def test_new_item_canned_food_in_item_types(self):
        """canned_food should be in ITEM_TYPES and affect hunger."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, FOOD_ITEM_TYPES
        assert "canned_food" in ITEM_TYPES
        assert ITEM_TYPES["canned_food"]["type"] == "consumable"
        assert ITEM_TYPES["canned_food"]["effects"]["hunger"] < 0
        assert "canned_food" in FOOD_ITEM_TYPES

    def test_generator_spawns_water_and_canned_food(self):
        """Generated stalkers should sometimes spawn with water and canned_food."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        import random
        # Run many seeds to find at least one with canned_food and one with water
        found_water = False
        found_canned = False
        for seed in range(100):
            state = generate_zone(seed=seed, num_players=0, num_ai_stalkers=5, num_mutants=0, num_traders=0)
            for agent in state["agents"].values():
                inv_types = {i["type"] for i in agent.get("inventory", [])}
                if "water" in inv_types:
                    found_water = True
                if "canned_food" in inv_types:
                    found_canned = True
            if found_water and found_canned:
                break
        assert found_water, "No stalker spawned with water across 100 seeds"
        assert found_canned, "No stalker spawned with canned_food across 100 seeds"

    # ── Ammo pickup (ground and observation memory) ───────────────────────────

    def test_pickup_ammo_from_ground(self):
        """Bot with a weapon but no ammo should pick up matching ammo from the ground."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state(weapon="pistol")
        # Strip inventory of any ammo
        state["agents"][sid]["inventory"] = []
        info = ITEM_TYPES["ammo_9mm"]
        state["locations"][loc_id]["items"] = [
            {"id": "ammo_ground", "type": "ammo_9mm", "name": info["name"],
             "weight": info.get("weight", 0.01), "value": info.get("value", 10)}
        ]
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        loc_items = new_state["locations"][loc_id]["items"]
        assert not any(i["id"] == "ammo_ground" for i in loc_items), "Ammo should be removed from ground"
        assert any(e["event_type"] == "item_picked_up" for e in events), "item_picked_up event expected"
        assert any(i["id"] == "ammo_ground" for i in agent["inventory"]), "Ammo should be in inventory"

    def test_seek_ammo_from_observation_memory(self):
        """Bot with a weapon but no ammo should travel to a location where ammo was observed."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state(weapon="pistol")
        state["agents"][sid]["inventory"] = []
        conns = state["locations"][loc_id].get("connections", [])
        if not conns:
            return  # skip if no neighbours
        mem_loc = conns[0]["to"]
        info = ITEM_TYPES["ammo_9mm"]
        # Place the ammo at mem_loc (so _find_item_memory_location verifies it's still there)
        state["locations"][mem_loc]["items"] = [
            {"id": "ammo_mem", "type": "ammo_9mm", "name": info["name"],
             "weight": info.get("weight", 0.01), "value": info.get("value", 10)}
        ]
        # Give agent an observation memory of the ammo at mem_loc
        state["agents"][sid]["memory"] = [{
            "world_turn": 1,
            "type": "observation",
            "title": "Вижу предметы",
            "summary": "На земле: ammo_9mm",
            "effects": {"observed": "items", "location_id": mem_loc, "item_types": ["ammo_9mm"]},
        }]
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        assert sched is not None, "Bot should have a scheduled travel action"
        assert sched["type"] == "travel", "Bot should be traveling toward observed ammo location"
        assert agent.get("current_goal") == "get_ammo"

    # ── Medicine/food/drink observation-memory pickup ─────────────────────────

    def test_seek_medicine_from_observation_memory(self):
        """Bot with no heal items should travel to a location where medicine was observed."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state, sid, loc_id = _make_bare_stalker_state(weapon="pistol", armor="leather_jacket")
        # Fully equipped; strip heal items from inventory so Need 4 fires
        state["agents"][sid]["inventory"] = [
            {"id": "ammo_t", "type": "ammo_9mm",
             "name": ITEM_TYPES["ammo_9mm"]["name"], "value": ITEM_TYPES["ammo_9mm"]["value"]},
        ]
        conns = state["locations"][loc_id].get("connections", [])
        if not conns:
            return
        mem_loc = conns[0]["to"]
        info = ITEM_TYPES["bandage"]
        state["locations"][mem_loc]["items"] = [
            {"id": "heal_mem", "type": "bandage", "name": info["name"],
             "weight": info.get("weight", 0.1), "value": info.get("value", 50)}
        ]
        state["agents"][sid]["memory"] = [{
            "world_turn": 1,
            "type": "observation",
            "title": "Вижу предметы",
            "summary": "На земле: bandage",
            "effects": {"observed": "items", "location_id": mem_loc, "item_types": ["bandage"]},
        }]
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        assert sched is not None, "Bot should have a scheduled travel action"
        assert sched["type"] == "travel", "Bot should be traveling toward observed medicine"
        # Verify a 'seek_item' decision memory was written for medical category
        decision_mems = [
            m for m in agent.get("memory", [])
            if m.get("type") == "decision"
            and m.get("effects", {}).get("item_category") == "medical"
        ]
        assert decision_mems, "Bot should write a 'seek_item' decision memory for medicine"

    # ── Affordability guard (trader-loop bug) ─────────────────────────────────

    def test_broke_bot_does_not_travel_to_trader(self):
        """A bot with no money should NOT travel to a trader to buy equipment."""
        state, sid, loc_id = _make_bare_stalker_state(with_trader=False)
        # Create a reachable trader location
        conns = state["locations"][loc_id].get("connections", [])
        if not conns:
            return
        trader_loc = conns[0]["to"]
        state.setdefault("traders", {})["tr_far"] = {
            "id": "tr_far",
            "archetype": "trader_npc",
            "name": "Far Trader",
            "location_id": trader_loc,
            "inventory": [],
            "money": 10000,
            "memory": [],
            "is_alive": True,
        }
        state["locations"][trader_loc].setdefault("agents", []).append("tr_far")
        # Bot has zero money — cannot afford anything
        state["agents"][sid]["money"] = 0
        state["agents"][sid]["inventory"] = []
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        # Bot must NOT start traveling to the trader when it can't afford anything
        if sched and sched["type"] == "travel":
            # If it is traveling, it must NOT be heading toward the trader for equipment
            goal = agent.get("current_goal", "")
            assert goal not in ("get_weapon", "get_armor", "get_ammo"), (
                f"Broke bot should not seek equipment at trader; current_goal={goal!r}, "
                f"traveling to {sched.get('final_target_id', '?')!r}"
            )


class TestConfirmedEmptyBlocking:
    """Verify that explore_confirmed_empty memory blocks re-exploration in all code paths."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def _make_state_with_confirmed_empty(
        self, global_goal: str = "get_rich", material_threshold: int = 999999
    ):
        """Return (state, sid, loc_id) where the bot is at an anomaly location it
        previously confirmed as empty (has explore_confirmed_empty memory)."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone, _make_stalker_agent
        import random

        state = generate_zone(seed=7, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        locs = list(state["locations"].keys())
        stalker_loc = locs[0]

        # Ensure the location has anomaly_activity so exploration would normally fire
        state["locations"][stalker_loc]["anomaly_activity"] = 5
        # Empty it of artifacts
        state["locations"][stalker_loc]["artifacts"] = []

        rng = random.Random(42)
        stalker = _make_stalker_agent(
            agent_id="bot_ce",
            name="Confirmed Empty Bot",
            location_id=stalker_loc,
            controller_kind="bot",
            participant_id=None,
            rng=rng,
        )
        stalker["global_goal"] = global_goal
        stalker["material_threshold"] = material_threshold
        stalker["money"] = 5000
        stalker["hp"] = 100
        stalker["hunger"] = 10
        stalker["thirst"] = 10
        stalker["sleepiness"] = 10
        # Fully equipped to skip equipment-maintenance layer
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        stalker["equipment"] = {
            "weapon": {"id": "w1", "type": "pistol", "name": ITEM_TYPES["pistol"]["name"],
                       "weight": ITEM_TYPES["pistol"]["weight"], "value": ITEM_TYPES["pistol"]["value"]},
            "armor": {"id": "a1", "type": "leather_jacket", "name": ITEM_TYPES["leather_jacket"]["name"],
                      "weight": ITEM_TYPES["leather_jacket"]["weight"], "value": ITEM_TYPES["leather_jacket"]["value"]},
            "detector": None,
        }
        stalker["inventory"] = [
            {"id": "ammo_t", "type": "ammo_9mm", "name": ITEM_TYPES["ammo_9mm"]["name"],
             "weight": ITEM_TYPES["ammo_9mm"].get("weight", 0.01), "value": ITEM_TYPES["ammo_9mm"]["value"]},
            {"id": "heal_t", "type": "bandage", "name": ITEM_TYPES["bandage"]["name"],
             "weight": ITEM_TYPES["bandage"].get("weight", 0.1), "value": ITEM_TYPES["bandage"]["value"]},
            {"id": "food_t", "type": "bread", "name": ITEM_TYPES["bread"]["name"],
             "weight": ITEM_TYPES["bread"].get("weight", 0.3), "value": ITEM_TYPES["bread"]["value"]},
            {"id": "water_t", "type": "water", "name": ITEM_TYPES["water"]["name"],
             "weight": ITEM_TYPES["water"].get("weight", 0.5), "value": ITEM_TYPES["water"]["value"]},
        ]
        # Plant the confirmed_empty memory entry for the current location.
        # Note: written as "observation" (not "decision") — see tick_rules.py.
        stalker["memory"] = [{
            "world_turn": 1,
            "type": "observation",
            "title": "Аномалия пустая",
            "summary": "Тщательно обыскал — артефактов нет.",
            "effects": {"action_kind": "explore_confirmed_empty", "location_id": stalker_loc},
        }]

        state["agents"]["bot_ce"] = stalker
        state["locations"][stalker_loc]["agents"].append("bot_ce")
        return state, "bot_ce", stalker_loc

    def test_get_rich_does_not_reexplore_confirmed_empty(self):
        """get_rich bot at a confirmed-empty anomaly location should NOT schedule exploration."""
        state, sid, loc_id = self._make_state_with_confirmed_empty(global_goal="get_rich")
        new_state, events = self._tick(state)
        assert not any(e["event_type"] == "exploration_started" for e in events), (
            "get_rich bot should not re-explore a confirmed-empty anomaly location"
        )
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        if sched:
            assert sched["type"] != "explore_anomaly_location", (
                f"get_rich bot should not schedule explore, got {sched['type']!r}"
            )

    def test_gather_resources_does_not_reexplore_confirmed_empty(self):
        """Phase-1 (gather resources) bot should NOT re-explore confirmed-empty locations."""
        # material_threshold=0 so agent is NOT in phase 1 (wealth >= 0) → stays in phase 2
        # To force phase-1, make threshold very high
        state, sid, loc_id = self._make_state_with_confirmed_empty(
            global_goal="get_rich", material_threshold=999999
        )
        # Drain money to force phase-1 (wealth < threshold)
        state["agents"][sid]["money"] = 10
        state["agents"][sid]["equipment"] = {"weapon": None, "armor": None, "detector": None}
        state["agents"][sid]["inventory"] = []
        new_state, events = self._tick(state)
        assert not any(e["event_type"] == "exploration_started" for e in events), (
            "Phase-1 (gather_resources) bot should not re-explore confirmed-empty location"
        )

    def test_explore_zone_does_not_reexplore_confirmed_empty(self):
        """get_rich bot (formerly explore_zone) should NOT re-explore a confirmed-empty location."""
        state, sid, loc_id = self._make_state_with_confirmed_empty(global_goal="get_rich")
        # Remove connections so bot can't travel, forcing it to consider local explore
        state["locations"][loc_id]["connections"] = []
        new_state, events = self._tick(state)
        assert not any(e["event_type"] == "exploration_started" for e in events), (
            "explore_zone bot should not re-explore confirmed-empty location"
        )

    def test_generator_always_uses_get_rich(self):
        """zone_generator should generate valid global goals for all agents."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.rules.world_rules import _VALID_GLOBAL_GOALS
        goals_found = set()
        for seed in range(50):
            state = generate_zone(seed=seed, num_players=0, num_ai_stalkers=5, num_mutants=0, num_traders=0)
            for ag in state["agents"].values():
                goals_found.add(ag.get("global_goal"))
        assert goals_found.issubset(_VALID_GLOBAL_GOALS), (
            f"Generator created agents with invalid global goals: {goals_found - _VALID_GLOBAL_GOALS}"
        )
        assert "get_rich" in goals_found, "Generator should still produce some 'get_rich' agents"

    def test_confirmed_empty_cleared_by_emission_memory(self):
        """An explore_confirmed_empty entry older than the agent's emission_ended memory
        should NOT block re-exploration — the stalker knows the zone may have been refilled."""
        state, sid, loc_id = self._make_state_with_confirmed_empty(global_goal="get_rich")
        agent = state["agents"][sid]
        # The confirmed_empty entry was written at world_turn=1.
        # Now add a newer emission_ended memory (turn 5) → the confirmed_empty is stale.
        agent["memory"].append({
            "world_turn": 5,
            "type": "observation",
            "title": "✅ Выброс закончился",
            "summary": "Выброс закончился. Аномальные зоны могут снова содержать артефакты.",
            "effects": {"action_kind": "emission_ended"},
        })
        # Put an artifact at the location so exploration can succeed
        state["locations"][loc_id]["artifacts"] = [
            {"id": "art_emission_test", "type": "crystal", "name": "Кристалл", "value": 500}
        ]
        new_state, events = self._tick(state)
        # The agent's emission knowledge should unlock re-exploration
        assert any(e["event_type"] == "exploration_started" for e in events), (
            "Bot should re-explore after emission invalidates the confirmed_empty entry"
        )

    def test_confirmed_empty_still_blocks_without_emission(self):
        """An explore_confirmed_empty entry blocks re-exploration when no emission has occurred
        after it — purely memory-based, no peeking at real map data."""
        state, sid, loc_id = self._make_state_with_confirmed_empty(global_goal="get_rich")
        # Put an artifact at the location to confirm we're NOT using ground-truth
        state["locations"][loc_id]["artifacts"] = [
            {"id": "art_secret", "type": "crystal", "name": "Кристалл", "value": 500}
        ]
        # No emission_ended in memory → confirmed_empty should still block
        new_state, events = self._tick(state)
        assert not any(e["event_type"] == "exploration_started" for e in events), (
            "Bot should NOT re-explore even when artifacts are present if no emission was observed "
            "(stalkers rely on memory, not omniscient map knowledge)"
        )


# ─────────────────────────────────────────────────────────────────
# Anomaly zone selection respects risk_tolerance
# ─────────────────────────────────────────────────────────────────

class TestAnomalyRiskTolerance:
    """Verify that anomaly-zone scoring penalises mismatches between a location's
    anomaly_activity/10.0 and the agent's risk_tolerance, so that:
      - low-risk agents gravitate toward low-activity zones, and
      - high-risk agents gravitate toward high-activity zones.
    """

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def _two_zone_state(self, risk_tolerance: float, wealth_phase: str = "gather"):
        """Return a state with the agent at A (no anomaly), B (high anomaly=8),
        C (low anomaly=2) both at distance 1.

        *wealth_phase* controls which Phase the bot enters:
        - "gather"  → Phase 1 (_bot_gather_resources), material_threshold=999999
        - "goal"    → Phase 2b (_bot_pursue_goal), material_threshold=0
        """
        state = _make_minimal_state(
            {
                "A": {"connections": [
                    {"to": "B", "travel_time": 1},
                    {"to": "C", "travel_time": 1},
                ]},
                "B": {},
                "C": {},
            },
            agent_loc_id="A",
        )
        state["locations"]["B"]["anomaly_activity"] = 8
        state["locations"]["C"]["anomaly_activity"] = 2

        agent = next(iter(state["agents"].values()))
        agent["risk_tolerance"] = risk_tolerance
        if wealth_phase == "gather":
            agent["material_threshold"] = 999999  # wealth (0) < threshold → Phase 1
        else:
            agent["material_threshold"] = 0  # wealth (0) >= threshold → Phase 2b
        return state

    # ── Phase 1 (_bot_gather_resources) ─────────────────────────────────────

    def test_phase1_low_risk_prefers_low_anomaly_zone(self):
        """Phase-1 (gather) low-risk agent (0.1) should prefer zone C (activity=2) over B (activity=8)."""
        state = self._two_zone_state(risk_tolerance=0.1, wealth_phase="gather")
        new_state, _events = self._tick(state)
        agent = next(iter(new_state["agents"].values()))
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have scheduled a travel action"
        assert sched["type"] == "travel"
        assert sched["final_target_id"] == "C", (
            f"Low-risk agent should head to C (low anomaly), got {sched.get('final_target_id')}"
        )

    def test_phase1_high_risk_prefers_high_anomaly_zone(self):
        """Phase-1 (gather) high-risk agent (0.9) should prefer zone B (activity=8) over C (activity=2)."""
        state = self._two_zone_state(risk_tolerance=0.9, wealth_phase="gather")
        new_state, _events = self._tick(state)
        agent = next(iter(new_state["agents"].values()))
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have scheduled a travel action"
        assert sched["type"] == "travel"
        assert sched["final_target_id"] == "B", (
            f"High-risk agent should head to B (high anomaly), got {sched.get('final_target_id')}"
        )

    # ── Phase 2b (_bot_pursue_goal) ──────────────────────────────────────────

    def test_phase2_low_risk_prefers_low_anomaly_zone(self):
        """Phase-2b (goal) low-risk agent (0.1) should prefer zone C (activity=2) over B (activity=8)."""
        state = self._two_zone_state(risk_tolerance=0.1, wealth_phase="goal")
        new_state, _events = self._tick(state)
        agent = next(iter(new_state["agents"].values()))
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have scheduled a travel action"
        assert sched["type"] == "travel"
        assert sched["final_target_id"] == "C", (
            f"Low-risk agent should head to C (low anomaly), got {sched.get('final_target_id')}"
        )

    def test_phase2_high_risk_prefers_high_anomaly_zone(self):
        """Phase-2b (goal) high-risk agent (0.9) should prefer zone B (activity=8) over C (activity=2)."""
        state = self._two_zone_state(risk_tolerance=0.9, wealth_phase="goal")
        new_state, _events = self._tick(state)
        agent = next(iter(new_state["agents"].values()))
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have scheduled a travel action"
        assert sched["type"] == "travel"
        assert sched["final_target_id"] == "B", (
            f"High-risk agent should head to B (high anomaly), got {sched.get('final_target_id')}"
        )


# ─────────────────────────────────────────────────────────────────
# Item-not-found loop prevention
# ─────────────────────────────────────────────────────────────────

class TestItemNotFoundLoop:
    """Verify that when a stalker travels to a memorised item location but finds nothing,
    an observation is written that blocks repeated trips to the same location."""

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def _agent_with_seek_weapon_memory(self, loc_id_target: str):
        """Return a minimal state (agent at A, target loc_id_target) where
        the agent has both an item-observation and a seek_item decision memory
        pointing to loc_id_target for "weapon"."""
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": loc_id_target, "travel_time": 1}]
                      if loc_id_target != "A" else []},
                **({loc_id_target: {}} if loc_id_target != "A" else {}),
            },
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        # Memory: agent observed a weapon at the target
        agent["memory"].append({
            "world_turn": 0,
            "type": "observation",
            "title": "Вижу предметы",
            "summary": "pistol на земле",
            "effects": {"observed": "items", "location_id": loc_id_target,
                        "item_types": sorted(WEAPON_ITEM_TYPES)},
        })
        # Memory: agent decided to travel there to get a weapon
        agent["memory"].append({
            "world_turn": 0,
            "type": "decision",
            "title": "Ищу оружие по памяти",
            "summary": "Иду за оружием",
            "effects": {"action_kind": "seek_item", "item_category": "weapon",
                        "destination": loc_id_target},
        })
        return state

    # ── Unit tests for helper functions ─────────────────────────────────────

    def test_item_not_found_locations_returns_blocked_loc(self):
        """_item_not_found_locations returns the location that has an item_not_found_here entry."""
        from app.games.zone_stalkers.rules.tick_rules import _item_not_found_locations
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        agent = {"memory": [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"world_turn": 1, "type": "observation",
             "effects": {"action_kind": "item_not_found_here", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]}
        result = _item_not_found_locations(agent, WEAPON_ITEM_TYPES)
        assert "B" in result, f"B should be blocked; got {result}"

    def test_newer_item_obs_lifts_block(self):
        """A newer items-observed entry for the same location supersedes the not_found block."""
        from app.games.zone_stalkers.rules.tick_rules import _item_not_found_locations
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        agent = {"memory": [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"world_turn": 1, "type": "observation",
             "effects": {"action_kind": "item_not_found_here", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            # Newer observation — item spawned again
            {"world_turn": 2, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]}
        result = _item_not_found_locations(agent, WEAPON_ITEM_TYPES)
        assert "B" not in result, f"B should not be blocked after newer item obs; got {result}"

    def test_find_item_memory_location_excludes_not_found(self):
        """_find_item_memory_location returns None when the only remembered location has a
        newer item_not_found_here entry."""
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state(
            {"A": {"connections": [{"to": "B", "travel_time": 1}]}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"world_turn": 1, "type": "observation",
             "effects": {"action_kind": "item_not_found_here", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result is None, f"Should return None when B is blocked; got {result}"

    def test_find_item_memory_location_not_blocked_without_not_found(self):
        """_find_item_memory_location returns the location normally when no not_found entry exists."""
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state(
            {"A": {"connections": [{"to": "B", "travel_time": 1}]}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result == "B", f"Should return B normally; got {result}"

    # ── Integration tests via tick ───────────────────────────────────────────

    def test_observation_written_when_sought_item_gone(self):
        """When the agent arrives at a memorised weapon location and finds nothing,
        an item_not_found_here observation is written into agent memory."""
        # Agent is already at the target location (simulates post-travel arrival)
        state = self._agent_with_seek_weapon_memory("A")
        # No weapon on the ground at A
        state["locations"]["A"]["items"] = []
        new_state, _events = self._tick(state)
        agent = next(iter(new_state["agents"].values()))
        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
            and m.get("effects", {}).get("location_id") == "A"
        ]
        assert len(not_found) == 1, (
            f"Expected 1 item_not_found_here observation for A, got {len(not_found)}"
        )

    def test_no_observation_when_item_found(self):
        """When the weapon IS on the ground and is picked up via a seek_item arrival,
        only item_picked_up_here is written (not item_not_found_here).  The suppressed
        'pickup' note was misleading: the item WAS found, not absent."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        state = self._agent_with_seek_weapon_memory("A")
        info = ITEM_TYPES["pistol"]
        state["locations"]["A"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        new_state, _events = self._tick(state)
        agent = next(iter(new_state["agents"].values()))
        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
        ]
        picked_up = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_picked_up_here"
        ]
        # With suppress_not_found=True in _bot_pickup_on_arrival, no "📭 Предметы закончились"
        assert len(not_found) == 0, (
            f"Expected 0 item_not_found_here on success; got {len(not_found)}: {not_found}"
        )
        # Instead, item_picked_up_here should be written exactly once
        assert len(picked_up) == 1, (
            f"Expected 1 item_picked_up_here; got {len(picked_up)}: {picked_up}"
        )
        assert picked_up[0]["effects"].get("source") == "seek_item_arrival"

    def test_no_observation_without_seek_intent(self):
        """Without a prior seek_item decision for this location, arriving at an empty location
        does NOT write a not_found observation (incidental visit)."""
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
        state = _make_minimal_state({"A": {}}, agent_loc_id="A")
        agent = next(iter(state["agents"].values()))
        # Only an item-observation memory — no seek_item decision
        agent["memory"].append({
            "world_turn": 0,
            "type": "observation",
            "effects": {"observed": "items", "location_id": "A",
                        "item_types": sorted(WEAPON_ITEM_TYPES)},
        })
        state["locations"]["A"]["items"] = []
        new_state, _events = self._tick(state)
        agent = next(iter(new_state["agents"].values()))
        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
        ]
        assert len(not_found) == 0, (
            f"Should not write not_found without a seek_item decision; got {not_found}"
        )

    def test_item_picked_up_here_blocks_location(self):
        """_item_not_found_locations returns a blocked location when it has an
        item_picked_up_here entry."""
        from app.games.zone_stalkers.rules.tick_rules import _item_not_found_locations
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        agent = {"memory": [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"world_turn": 1, "type": "observation",
             "effects": {"action_kind": "item_picked_up_here", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]}
        result = _item_not_found_locations(agent, WEAPON_ITEM_TYPES)
        assert "B" in result, f"B should be blocked by item_picked_up_here; got {result}"

    def test_newer_item_obs_lifts_picked_up_block(self):
        """A newer observed:items entry supersedes an item_picked_up_here block."""
        from app.games.zone_stalkers.rules.tick_rules import _item_not_found_locations
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        agent = {"memory": [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"world_turn": 1, "type": "observation",
             "effects": {"action_kind": "item_picked_up_here", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            # Fresh spawn observed on turn 2
            {"world_turn": 2, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]}
        result = _item_not_found_locations(agent, WEAPON_ITEM_TYPES)
        assert "B" not in result, f"B should be unblocked after newer obs; got {result}"

    def test_find_item_memory_excludes_picked_up_location(self):
        """_find_item_memory_location excludes a location that has item_picked_up_here."""
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state(
            {"A": {"connections": [{"to": "B", "travel_time": 1}]}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"world_turn": 1, "type": "observation",
             "effects": {"action_kind": "item_picked_up_here", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result is None, f"B should be excluded after item_picked_up_here; got {result}"


# ─────────────────────────────────────────────────────────────────
# _find_item_memory_location skips unreachable locations
# ─────────────────────────────────────────────────────────────────

class TestItemMemoryUnreachable:
    """Verify that _find_item_memory_location ignores locations that are cut off
    by closed connections, so the bot does not try to travel to an unreachable spot."""

    def test_unreachable_location_skipped(self):
        """_find_item_memory_location returns None when the only remembered location
        is cut off by a closed connection."""
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 1, "closed": True}]},
                "B": {},
            },
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result is None, f"Should return None for unreachable B; got {result}"

    def test_reachable_location_returned(self):
        """_find_item_memory_location returns the location when the path is open."""
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 1}]},
                "B": {},
            },
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result == "B", f"Should return B; got {result}"

    def test_alternative_reachable_location_preferred(self):
        """When one path is closed but another route exists to a different location,
        the reachable location is returned."""
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state(
            {
                "A": {"connections": [
                    {"to": "B", "travel_time": 1, "closed": True},
                    {"to": "C", "travel_time": 1},
                ]},
                "B": {},
                "C": {},
            },
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        # B was observed more recently but is unreachable; C is older but reachable
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "C",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"world_turn": 1, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result == "C", f"Should return reachable C, not unreachable B; got {result}"


# ─────────────────────────────────────────────────────────────────
# Successful pickup blocks repeated item search at same location
# ─────────────────────────────────────────────────────────────────

class TestPickupBlocksResearch:
    """Verify that after picking up the last item of a needed type from a location,
    an item_not_found_here observation is written so that _find_item_memory_location
    won't send the agent back to the same spot."""

    def _call_pickup(self, agent, item_types, state, world_turn=1):
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_item_from_ground
        return _bot_pickup_item_from_ground("agent1", agent, item_types, state, world_turn)

    def _make_agent_at(self, loc_id: str, state: dict) -> dict:
        agent = next(iter(state["agents"].values()))
        agent["location_id"] = loc_id
        return agent

    def test_pickup_last_item_writes_not_found_observation(self):
        """After picking up the last weapon on the ground, item_not_found_here is written."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, WEAPON_ITEM_TYPES
        state = _make_minimal_state({"A": {}}, agent_loc_id="A")
        agent = self._make_agent_at("A", state)
        info = ITEM_TYPES["pistol"]
        state["locations"]["A"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        evs = self._call_pickup(agent, WEAPON_ITEM_TYPES, state)
        assert evs, "Should have returned pickup event"
        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
            and m.get("effects", {}).get("location_id") == "A"
        ]
        assert len(not_found) == 1, (
            f"Expected 1 item_not_found_here after last-item pickup, got {len(not_found)}"
        )
        assert not_found[0]["effects"].get("source") == "pickup", (
            f"Expected source='pickup'; got {not_found[0]['effects'].get('source')!r}"
        )

    def test_pickup_with_remaining_items_no_observation(self):
        """When another weapon remains after pickup, no item_not_found_here is written."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, WEAPON_ITEM_TYPES
        state = _make_minimal_state({"A": {}}, agent_loc_id="A")
        agent = self._make_agent_at("A", state)
        info = ITEM_TYPES["pistol"]
        state["locations"]["A"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]},
            {"id": "wpn2", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]},
        ]
        evs = self._call_pickup(agent, WEAPON_ITEM_TYPES, state)
        assert evs, "Should have returned pickup event"
        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
        ]
        assert len(not_found) == 0, (
            f"Should NOT write not_found when another weapon remains; got {not_found}"
        )

    def test_not_found_observation_blocks_find_item_memory_location(self):
        """After picking up the last weapon, _find_item_memory_location no longer
        returns that location."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, WEAPON_ITEM_TYPES
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location

        state = _make_minimal_state(
            {"A": {"connections": [{"to": "B", "travel_time": 1}]}, "B": {}},
            agent_loc_id="A",
        )
        agent = self._make_agent_at("A", state)
        # Agent remembers having seen a weapon at B
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        # Simulate: agent travelled to B, picked up last weapon there
        agent["location_id"] = "B"
        info = ITEM_TYPES["pistol"]
        state["locations"]["B"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        self._call_pickup(agent, WEAPON_ITEM_TYPES, state)  # picks up + writes not_found for B

        # Back at A: now search again
        agent["location_id"] = "A"
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result is None, (
            f"_find_item_memory_location should return None after pickup-block on B; got {result}"
        )

    def test_newer_item_obs_lifts_pickup_block(self):
        """A newer observed:items entry for the same location lifts the pickup-induced block."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, WEAPON_ITEM_TYPES
        from app.games.zone_stalkers.rules.tick_rules import _find_item_memory_location

        state = _make_minimal_state(
            {"A": {"connections": [{"to": "B", "travel_time": 1}]}, "B": {}},
            agent_loc_id="A",
        )
        agent = self._make_agent_at("A", state)
        # Simulate: old observation + pickup (writes not_found) + newer observation
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            # the block written by pickup on turn 1
            {"world_turn": 1, "type": "observation",
             "effects": {"action_kind": "item_not_found_here", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            # a new item spawned and agent observed it on turn 2
            {"world_turn": 2, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
        ]
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result == "B", (
            f"Newer observed:items should lift the pickup block on B; got {result}"
        )

    def test_seek_item_arrival_pickup_writes_item_picked_up_here(self):
        """When the agent arrives at a seek_item destination and picks up the item,
        item_picked_up_here is written so the location is blocked for re-search."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, WEAPON_ITEM_TYPES
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival

        state = _make_minimal_state({"A": {}, "B": {}}, agent_loc_id="A")
        agent = self._make_agent_at("A", state)
        info = ITEM_TYPES["pistol"]
        state["locations"]["A"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        # Simulate NPC having decided to travel to A for a weapon
        agent["memory"] = [{
            "type": "decision", "world_turn": 0,
            "label": "Иду за оружием", "summary": "...",
            "effects": {"action_kind": "seek_item", "item_category": "weapon",
                        "destination": "A"},
            "reason": "test",
        }]
        agent_id = next(iter(state["agents"]))
        result = _bot_pickup_on_arrival(agent_id, agent, state, world_turn=1)
        assert result, "Should have returned pickup events"
        picked_up = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_picked_up_here"
            and m.get("effects", {}).get("location_id") == "A"
        ]
        assert len(picked_up) == 1, (
            f"Expected 1 item_picked_up_here, got {len(picked_up)}"
        )
        assert picked_up[0]["effects"].get("source") == "seek_item_arrival"

    def test_seek_item_arrival_pickup_blocks_find_item_memory_location(self):
        """After a seek_item arrival pickup, _find_item_memory_location excludes that location."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, WEAPON_ITEM_TYPES
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival, _find_item_memory_location

        state = _make_minimal_state(
            {"A": {"connections": [{"to": "B", "travel_time": 1}]}, "B": {}},
            agent_loc_id="B",
        )
        agent = self._make_agent_at("B", state)
        info = ITEM_TYPES["pistol"]
        state["locations"]["B"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]},
            # A second weapon remains so _bot_pickup_item_from_ground does NOT write item_not_found_here
            {"id": "wpn2", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]},
        ]
        agent["memory"] = [
            {"world_turn": 0, "type": "observation",
             "effects": {"observed": "items", "location_id": "B",
                         "item_types": sorted(WEAPON_ITEM_TYPES)}},
            {"type": "decision", "world_turn": 0,
             "label": "Иду за оружием", "summary": "...",
             "effects": {"action_kind": "seek_item", "item_category": "weapon",
                         "destination": "B"},
             "reason": "test"},
        ]
        agent_id = next(iter(state["agents"]))
        _bot_pickup_on_arrival(agent_id, agent, state, world_turn=1)
        # Even though one weapon remains at B, item_picked_up_here should block B
        agent["location_id"] = "A"
        result = _find_item_memory_location(agent, WEAPON_ITEM_TYPES, state)
        assert result is None, (
            f"B should be blocked by item_picked_up_here even when items remain; got {result}"
        )

    def test_seek_item_arrival_no_picked_up_here_without_seek_decision(self):
        """If the most recent decision is NOT seek_item, no item_picked_up_here is written."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, WEAPON_ITEM_TYPES
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival

        state = _make_minimal_state({"A": {}}, agent_loc_id="A")
        agent = self._make_agent_at("A", state)
        info = ITEM_TYPES["pistol"]
        state["locations"]["A"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": info["name"],
             "weight": info["weight"], "value": info["value"]}
        ]
        # Most recent decision is a wander — not a seek_item
        agent["memory"] = [{
            "type": "decision", "world_turn": 0,
            "label": "Иду куда-нибудь", "summary": "...",
            "effects": {"action_kind": "wander", "destination": "A"},
            "reason": "test",
        }]
        agent_id = next(iter(state["agents"]))
        result = _bot_pickup_on_arrival(agent_id, agent, state, world_turn=1)
        assert result == [], "_bot_pickup_on_arrival returns [] when latest decision is not seek_item"
        picked_up = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_picked_up_here"
        ]
        assert len(picked_up) == 0, "Should not write item_picked_up_here without seek_item decision"


# ─────────────────────────────────────────────────────────────────
# Emergency travel-to-trader decisions write a memory entry
# ─────────────────────────────────────────────────────────────────

class TestEmergencyTravelMemory:
    """Verify that all three emergency travel-to-trader branches (low HP, hunger,
    thirst) write a 'decision' memory entry before scheduling travel."""

    def _make_state_with_remote_trader(self, agent_hp=100, hunger=0, thirst=0, money=5000):
        """Agent at A, trader at B (requires travel). Agent has no consumables."""
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 10}]},
                "B": {},
            },
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        agent["hp"] = agent_hp
        agent["hunger"] = hunger
        agent["thirst"] = thirst
        agent["money"] = money
        agent["inventory"] = []
        # Place a trader at B
        trader_id = "t1"
        state["traders"][trader_id] = {
            "id": trader_id,
            "name": "Сидорович",
            "location_id": "B",
            "is_alive": True,
        }
        state["locations"]["B"]["agents"] = [trader_id]
        return state

    def _run_tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        agent_id = next(iter(state["agents"]))
        agent = state["agents"][agent_id]
        _run_bot_action(agent_id, agent, state, world_turn=1)
        return agent

    def test_low_hp_travel_writes_decision_memory(self):
        """When HP is critical and trader requires travel, a decision memory is written."""
        state = self._make_state_with_remote_trader(agent_hp=25, money=5000)
        agent = self._run_tick(state)
        decisions = [
            m for m in agent["memory"]
            if m.get("type") == "decision"
            and m.get("effects", {}).get("action_kind") == "seek_item"
            and m.get("effects", {}).get("item_category") == "medical"
        ]
        assert len(decisions) == 1, (
            f"Expected 1 seek_item/medical decision for emergency HP; got {decisions}"
        )
        assert decisions[0]["effects"].get("emergency") is True
        assert decisions[0]["effects"].get("destination") == "B"

    def test_high_hunger_travel_writes_decision_memory(self):
        """When hunger is critical and trader requires travel, a decision memory is written."""
        state = self._make_state_with_remote_trader(hunger=75, money=5000)
        agent = self._run_tick(state)
        decisions = [
            m for m in agent["memory"]
            if m.get("type") == "decision"
            and m.get("effects", {}).get("action_kind") == "seek_item"
            and m.get("effects", {}).get("item_category") == "food"
        ]
        assert len(decisions) == 1, (
            f"Expected 1 seek_item/food decision for emergency hunger; got {decisions}"
        )
        assert decisions[0]["effects"].get("emergency") is True
        assert decisions[0]["effects"].get("destination") == "B"

    def test_high_thirst_travel_writes_decision_memory(self):
        """When thirst is critical and trader requires travel, a decision memory is written."""
        state = self._make_state_with_remote_trader(thirst=75, money=5000)
        agent = self._run_tick(state)
        decisions = [
            m for m in agent["memory"]
            if m.get("type") == "decision"
            and m.get("effects", {}).get("action_kind") == "seek_item"
            and m.get("effects", {}).get("item_category") == "drink"
        ]
        assert len(decisions) == 1, (
            f"Expected 1 seek_item/drink decision for emergency thirst; got {decisions}"
        )
        assert decisions[0]["effects"].get("emergency") is True
        assert decisions[0]["effects"].get("destination") == "B"


# ─────────────────────────────────────────────────────────────────
# _bot_pickup_on_arrival: commit to picking up on the arrival tick
# ─────────────────────────────────────────────────────────────────

class TestPickupOnArrival:
    """Verify that when an agent arrives at a seek_item destination it picks up
    the sought item before re-evaluating priorities, even if a higher-priority
    Need would otherwise redirect it elsewhere."""

    def _make_state(self, agent_extra=None):
        """Agent at A. A has a weapon on the ground.
        A memory seek_item(weapon, destination=A) is pre-set so the agent looks
        as if it just arrived from travelling."""
        state = _make_minimal_state(
            {"A": {}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        # Weapon on the ground at A
        state["locations"]["A"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": "Пистолет", "value": 500,
             "category": "weapon", "risk_tolerance": 0.5},
        ]
        # Inject a seek_item decision pointing at current location
        agent["memory"] = [
            {
                "type": "decision",
                "world_turn": 0,
                "label": "Ищу оружие по памяти",
                "summary": "...",
                "effects": {
                    "action_kind": "seek_item",
                    "item_category": "weapon",
                    "destination": "A",
                },
                "reason": "test",
            }
        ]
        if agent_extra:
            agent.update(agent_extra)
        return state

    def _run_tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        agent_id = next(iter(state["agents"]))
        agent = state["agents"][agent_id]
        _run_bot_action(agent_id, agent, state, world_turn=2)
        return agent

    def test_picks_up_weapon_on_arrival(self):
        """Agent with seek_item(weapon, A) at location A immediately picks up
        the weapon before any other priority fires."""
        state = self._make_state()
        agent = self._run_tick(state)
        pickups = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "pickup_ground"
        ]
        assert pickups, "Expected a pickup_ground action memory on arrival"
        assert pickups[0]["effects"]["item_type"] == "pistol"
        # Item is now in inventory
        assert any(i["type"] == "pistol" for i in agent.get("inventory", []))

    def test_pickup_happens_before_priority_switch(self):
        """Even when the agent also lacks armor (Need 2), the arrival commitment
        for a weapon (higher Need) is honoured first."""
        state = self._make_state({"equipment": {}})  # no weapon AND no armor
        agent = self._run_tick(state)
        # Should have picked up weapon, not scheduled travel to armor
        pickups = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "pickup_ground"
        ]
        assert pickups, "pickup_ground should fire on arrival, not a redirect"
        seek_armor = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "seek_item"
            and m.get("effects", {}).get("item_category") == "armor"
        ]
        assert not seek_armor, "Should not have sought armor before picking up weapon"

    def test_no_pickup_when_destination_differs(self):
        """If the seek_item destination is B but agent is at A, _bot_pickup_on_arrival
        returns [] — the arrival shortcut does not fire."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival
        state = _make_minimal_state(
            {"A": {"connections": [{"to": "B", "travel_time": 10}]}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        state["locations"]["A"]["items"] = [
            {"id": "wpn1", "type": "pistol", "name": "Пистолет", "value": 500,
             "category": "weapon", "risk_tolerance": 0.5},
        ]
        agent["memory"] = [
            {
                "type": "decision",
                "world_turn": 0,
                "label": "Ищу оружие по памяти",
                "summary": "...",
                "effects": {
                    "action_kind": "seek_item",
                    "item_category": "weapon",
                    "destination": "B",   # different location!
                },
                "reason": "test",
            }
        ]
        result = _bot_pickup_on_arrival(
            next(iter(state["agents"])), agent, state, world_turn=2
        )
        assert result == [], (
            "Expected [] when seek_item destination is B but agent is at A"
        )

    def test_no_pickup_when_no_seek_item_decision(self):
        """If the latest decision is not seek_item, no arrival pickup fires."""
        state = self._make_state()
        agent = next(iter(state["agents"].values()))
        # Replace memory with a non-seek_item decision
        agent["memory"] = [
            {
                "type": "decision",
                "world_turn": 0,
                "label": "Иду исследовать",
                "summary": "...",
                "effects": {"action_kind": "explore", "destination": "A"},
                "reason": "test",
            }
        ]
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=2)
        # The on-arrival pickup should NOT fire; pickup might still happen via
        # normal Need-1 ground-pickup — that's fine, just verify arrival logic
        # didn't produce a spurious pickup based on a non-seek decision.
        # We verify indirectly: arrival check returns [] for non-seek decision.
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival
        # Reset state for isolated test
        state2 = self._make_state()
        agent2 = next(iter(state2["agents"].values()))
        agent2["memory"] = [
            {
                "type": "decision",
                "world_turn": 0,
                "label": "...",
                "summary": "...",
                "effects": {"action_kind": "explore"},
                "reason": "test",
            }
        ]
        result = _bot_pickup_on_arrival(
            next(iter(state2["agents"])), agent2, state2, world_turn=2
        )
        assert result == [], f"Expected [] for non-seek_item decision, got {result}"

    def test_item_not_found_recorded_at_non_trader_on_arrival(self):
        """When arriving at a non-trader seek_item destination and the item is not
        on the ground, item_not_found_here is recorded immediately so that any
        emergency that fires next (before the normal priority tree runs) cannot
        cause the agent to loop back to the same empty location."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival

        state = _make_minimal_state(
            {"A": {}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        # No items on the ground at A (the food was taken)
        state["locations"]["A"]["items"] = []
        agent["memory"] = [
            {
                "type": "decision",
                "world_turn": 0,
                "label": "Иду за едой",
                "summary": "...",
                "effects": {
                    "action_kind": "seek_item",
                    "item_category": "food",
                    "destination": "A",
                },
                "reason": "test",
            }
        ]
        agent_id = next(iter(state["agents"]))
        result = _bot_pickup_on_arrival(agent_id, agent, state, world_turn=1)
        assert result == [], "No items on ground — should return []"
        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
            and m.get("effects", {}).get("location_id") == "A"
        ]
        assert len(not_found) == 1, (
            f"Expected item_not_found_here to be recorded at A on arrival; got {len(not_found)}"
        )

    def test_item_not_found_NOT_recorded_at_trader_on_arrival(self):
        """When arriving at a trader location with a seek_item decision, the absence
        of ground items must NOT record item_not_found_here — traders sell items,
        so 'nothing on the floor' is expected and must not blacklist the trader."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival

        state = _make_minimal_state(
            {"A": {}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        state["locations"]["A"]["items"] = []
        # Place a trader at location A — must appear in both traders dict AND
        # loc["agents"] because _find_trader_at_location checks loc["agents"].
        state.setdefault("traders", {})["t1"] = {
            "id": "t1",
            "name": "Торговец",
            "location_id": "A",
            "is_alive": True,
            "inventory": [],
        }
        state["locations"]["A"]["agents"] = ["t1"]
        agent["memory"] = [
            {
                "type": "decision",
                "world_turn": 0,
                "label": "Иду за водой (экстренно)",
                "summary": "...",
                "effects": {
                    "action_kind": "seek_item",
                    "item_category": "drink",
                    "destination": "A",
                    "emergency": True,
                },
                "reason": "test",
            }
        ]
        agent_id = next(iter(state["agents"]))
        result = _bot_pickup_on_arrival(agent_id, agent, state, world_turn=1)
        assert result == [], "No ground items — should return []"
        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
        ]
        assert len(not_found) == 0, (
            "item_not_found_here must NOT be written at a trader location"
        )

    def test_no_retrigger_after_successful_pickup(self):
        """On the tick AFTER a successful seek_item arrival pickup, _bot_pickup_on_arrival
        must return [] immediately (location already blocked by item_picked_up_here).
        This prevents a spurious 'item_not_found_here' source=arrival on tick T+1."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state({"A": {}}, agent_loc_id="A")
        agent = next(iter(state["agents"].values()))
        # No items left on the ground (pickup happened on a prior tick)
        state["locations"]["A"]["items"] = []
        # Simulate: agent had a seek_item decision + picked up the item (wrote item_picked_up_here)
        agent["memory"] = [
            {
                "type": "decision", "world_turn": 0,
                "label": "Иду за оружием", "summary": "...",
                "effects": {"action_kind": "seek_item", "item_category": "weapon",
                            "destination": "A"},
                "reason": "test",
            },
            {
                "type": "observation", "world_turn": 1,
                "label": "✅ Нашёл weapon в «A»", "summary": "...",
                "effects": {
                    "action_kind": "item_picked_up_here",
                    "source": "seek_item_arrival",
                    "location_id": "A",
                    "item_types": sorted(WEAPON_ITEM_TYPES),
                },
            },
        ]
        agent_id = next(iter(state["agents"]))
        # Simulate tick T+1: no items on ground, but seek_item is still the latest decision
        result = _bot_pickup_on_arrival(agent_id, agent, state, world_turn=2)
        assert result == [], "Should return [] when seek is already resolved"
        # No extra observations should be written
        new_obs = [
            m for m in agent["memory"]
            if m.get("type") == "observation" and m.get("world_turn") == 2
        ]
        assert len(new_obs) == 0, (
            f"Should not write any new observations on retrigger tick; got {new_obs}"
        )

    def test_no_retrigger_after_not_found(self):
        """On tick T+1 after a seek_item that found nothing, _bot_pickup_on_arrival
        should also return [] immediately (already blocked by item_not_found_here)."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_on_arrival
        from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES

        state = _make_minimal_state({"A": {}}, agent_loc_id="A")
        agent = next(iter(state["agents"].values()))
        state["locations"]["A"]["items"] = []
        agent["memory"] = [
            {
                "type": "decision", "world_turn": 0,
                "label": "Иду за оружием", "summary": "...",
                "effects": {"action_kind": "seek_item", "item_category": "weapon",
                            "destination": "A"},
                "reason": "test",
            },
            {
                "type": "observation", "world_turn": 1,
                "label": "⚠️ Предмет исчез", "summary": "...",
                "effects": {
                    "action_kind": "item_not_found_here",
                    "source": "arrival",
                    "location_id": "A",
                    "item_types": sorted(WEAPON_ITEM_TYPES),
                },
            },
        ]
        agent_id = next(iter(state["agents"]))
        result = _bot_pickup_on_arrival(agent_id, agent, state, world_turn=2)
        assert result == [], "Should return [] when already recorded as not_found"
        new_obs = [
            m for m in agent["memory"]
            if m.get("type") == "observation" and m.get("world_turn") == 2
        ]
        assert len(new_obs) == 0, (
            f"Should not write again after not_found was already recorded; got {new_obs}"
        )

    def test_no_spurious_item_not_found_after_emergency_buy_at_trader(self):
        """Regression: after an emergency seek_item (food/drink/medical) to a trader,
        subsequent calls to _maybe_record_item_not_found must NOT write
        item_not_found_here — the old seek_item had emergency=True, meaning the
        agent travelled there to *buy*, not to pick up from the ground.

        Scenario that was broken:
          Turn T   → agent writes seek_item(food, destination=A, emergency=True)
          Turn T+1 → arrives, emergency path buys food (writes trade_decision)
          Turn T+2 → consumes food, hunger back > 30 but < 70; Need-5 food path
                     calls _maybe_record_item_not_found; old seek_item found in
                     memory → spurious item_not_found_here written.
        """
        from app.games.zone_stalkers.rules.tick_rules import _maybe_record_item_not_found
        from app.games.zone_stalkers.balance.items import FOOD_ITEM_TYPES

        state = _make_minimal_state({"A": {}}, agent_loc_id="A")
        agent = next(iter(state["agents"].values()))
        state["locations"]["A"]["items"] = []

        # Old emergency seek_item in memory (agent travelled to this trader earlier)
        agent["memory"] = [
            {
                "type": "decision", "world_turn": 1,
                "label": "Иду к торговцу за едой (экстренно)",
                "summary": "...",
                "effects": {
                    "action_kind": "seek_item",
                    "item_category": "food",
                    "destination": "A",
                    "emergency": True,
                },
            },
            # Simulated trade_decision from the buy on arrival (no destination/item_category)
            {
                "type": "decision", "world_turn": 5,
                "label": "Решил купить «Буханка хлеба»",
                "summary": "...",
                "effects": {"action_kind": "trade_decision", "item_type": "bread"},
            },
        ]
        loc = state["locations"]["A"]

        _maybe_record_item_not_found(
            agent, 6, state, "A", loc, FOOD_ITEM_TYPES, "food"
        )

        not_found = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "item_not_found_here"
        ]
        assert len(not_found) == 0, (
            "item_not_found_here must NOT be written after an emergency seek_item "
            f"(trader visit for buying); got: {not_found}"
        )


# ─────────────────────────────────────────────────────────────────
# _bot_sell_on_arrival: commit to selling artifacts at the trader
# ─────────────────────────────────────────────────────────────────

class TestSellOnArrival:
    """Verify that when an agent arrives at the trader it specifically travelled to
    in order to sell artifacts, the sale is executed before any other need fires."""

    def _make_state(self, agent_extra=None):
        """Agent at A (trader location). The agent holds an artifact and has a
        ``sell_at_trader`` decision in memory pointing at A."""
        state = _make_minimal_state(
            {"A": {}, "B": {}},
            agent_loc_id="A",
        )
        agent = next(iter(state["agents"].values()))
        # Place a trader at location A
        state.setdefault("traders", {})["t1"] = {
            "id": "t1",
            "name": "Торговец",
            "location_id": "A",
            "archetype": "trader_npc",
            "inventory": [],
            "money": 10000,
            "memory": [],
            "is_alive": True,
        }
        state["locations"]["A"]["agents"] = ["t1"]
        # Give the agent an artifact to sell
        artifact = {"id": "art1", "type": "soul", "name": "Душа", "value": 800,
                    "category": "artifact", "risk_tolerance": 0.5}
        agent.setdefault("inventory", []).append(artifact)
        # Inject a sell_at_trader decision pointing at current location
        agent["memory"] = [
            {
                "type": "decision",
                "world_turn": 0,
                "label": "Иду к торговцу",
                "summary": "...",
                "effects": {
                    "action_kind": "sell_at_trader",
                    "destination": "A",
                },
                "reason": "test",
            }
        ]
        if agent_extra:
            agent.update(agent_extra)
        return state

    def test_sells_artifact_on_arrival(self):
        """When the latest decision is sell_at_trader pointing at the current
        location and a trader is present, the artifact is sold immediately."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_sell_on_arrival
        state = self._make_state()
        agent = next(iter(state["agents"].values()))
        agent_id = next(iter(state["agents"]))
        result = _bot_sell_on_arrival(agent_id, agent, state, world_turn=1)
        assert result, "_bot_sell_on_arrival should return events when artifact sold"
        sell_mem = [
            m for m in agent["memory"]
            if m.get("type") == "action"
            and m.get("effects", {}).get("action_kind") == "trade_sell"
        ]
        assert sell_mem, "trade_sell action memory should be written after sale"

    def test_no_sell_when_destination_differs(self):
        """If the sell_at_trader destination is B but agent is at A, no sale fires."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_sell_on_arrival
        state = self._make_state()
        agent = next(iter(state["agents"].values()))
        # Override destination to point at a different location
        agent["memory"][0]["effects"]["destination"] = "B"
        agent_id = next(iter(state["agents"]))
        result = _bot_sell_on_arrival(agent_id, agent, state, world_turn=1)
        assert result == [], "Should not sell when destination differs from current location"

    def test_sell_happens_before_equipment_maintenance(self):
        """sell_at_trader arrival commitment fires before equipment-maintenance
        so the agent completes its trading trip before buying new gear."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        state = self._make_state()
        # Make the agent wealthy enough to trigger equipment buy
        agent = next(iter(state["agents"].values()))
        agent["money"] = 10000
        agent["material_threshold"] = 100  # well above threshold
        # No weapon equipped — Need 1 would normally try to buy one
        agent.setdefault("equipment", {})["weapon"] = None
        # Equip the trader with a weapon for sale
        state["traders"]["t1"]["inventory"] = [
            {"id": "wpn1", "type": "pistol", "name": "Пистолет",
             "value": 500, "category": "weapon", "risk_tolerance": 0.5}
        ]
        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=1)
        sell_mem = [
            m for m in agent["memory"]
            if m.get("type") == "action"
            and m.get("effects", {}).get("action_kind") == "trade_sell"
        ]
        assert sell_mem, (
            "Artifact sale should fire on arrival even when equipment needs are present"
        )


# ─────────────────────────────────────────────────────────────────
# Emission warning observation system
# ─────────────────────────────────────────────────────────────────

class TestEmissionWarning:
    """Verify that a 'скоро выброс' observation is written to all alive agents
    exactly once, at a random 10–15 turn window before emission, and that
    the bot uses this observation (not raw distance arithmetic) to decide to flee."""

    def _make_state_with_emission(self, scheduled_turn: int, current_turn: int = 1,
                                  warning_written=None, warning_offset=None):
        """Minimal state: two locations A (plain) and B (building). Agent at A."""
        from app.games.zone_stalkers.rules.tick_rules import _EMISSION_WARNING_MIN_TURNS
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 1}]},
                "B": {},
            },
            agent_loc_id="A",
        )
        state["locations"]["A"]["terrain_type"] = "plain"
        state["locations"]["B"]["terrain_type"] = "buildings"
        state["emission_scheduled_turn"] = scheduled_turn
        state["emission_active"] = False
        state["emission_warning_written_turn"] = warning_written
        state["emission_warning_offset"] = warning_offset
        state["world_turn"] = current_turn
        state["seed"] = 0
        return state

    def test_warning_written_at_correct_turn(self):
        """emission_imminent observation is written when world_turn == scheduled - offset."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        # Use offset=12: scheduled=100, warning fires at turn 88
        scheduled = 100
        state = self._make_state_with_emission(scheduled, current_turn=88,
                                               warning_offset=12)
        state["world_turn"] = 88; state, events = tick_zone_map(state)
        agent = next(iter(state["agents"].values()))
        warnings = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "emission_imminent"
        ]
        assert len(warnings) == 1, f"Expected 1 emission_imminent memory, got {warnings}"
        assert state["emission_warning_written_turn"] == 88

    def test_warning_not_written_twice(self):
        """Once the warning is written, re-running subsequent ticks does NOT add a duplicate."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        scheduled = 100
        state = self._make_state_with_emission(scheduled, current_turn=88,
                                               warning_offset=12)
        state["world_turn"] = 88
        state, _ = tick_zone_map(state)  # first tick — writes warning
        state["world_turn"] = 89
        state, _ = tick_zone_map(state)  # second tick — must NOT add another
        agent = next(iter(state["agents"].values()))
        warnings = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "emission_imminent"
        ]
        assert len(warnings) == 1, "Duplicate emission_imminent observations written"

    def test_warning_event_emitted(self):
        """emission_warning world event is in the returned events list."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        scheduled = 100
        state = self._make_state_with_emission(scheduled, current_turn=88,
                                               warning_offset=12)
        state["world_turn"] = 88; state, events = tick_zone_map(state)
        warn_events = [e for e in events if e.get("event_type") == "emission_warning"]
        assert len(warn_events) == 1

    def test_bot_flees_on_emission_imminent_memory(self):
        """A bot on dangerous terrain with emission_imminent memory flees to shelter."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        state = self._make_state_with_emission(scheduled_turn=200, current_turn=1)
        agent = next(iter(state["agents"].values()))
        # Manually inject the emission_imminent observation (as if it was written earlier)
        agent["memory"] = [
            {
                "type": "observation",
                "world_turn": 1,
                "title": "⚠️ Скоро выброс!",
                "summary": "...",
                "effects": {
                    "action_kind": "emission_imminent",
                    "turns_until": 12,
                    "emission_scheduled_turn": 200,
                },
            }
        ]
        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=1)
        flee_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "flee_emission"
        ]
        assert flee_decisions, (
            "Bot on dangerous terrain with emission_imminent memory should flee"
        )

    def test_bot_does_not_flee_after_emission_ended(self):
        """After emission_ended supersedes emission_imminent, bot no longer flees."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        state = self._make_state_with_emission(scheduled_turn=200, current_turn=50)
        agent = next(iter(state["agents"].values()))
        # emission_imminent at turn 1, then emission_ended at turn 15
        agent["memory"] = [
            {
                "type": "observation",
                "world_turn": 1,
                "title": "⚠️ Скоро выброс!",
                "summary": "...",
                "effects": {"action_kind": "emission_imminent", "turns_until": 12,
                             "emission_scheduled_turn": 15},
            },
            {
                "type": "observation",
                "world_turn": 15,
                "title": "✅ Выброс закончился",
                "summary": "...",
                "effects": {"action_kind": "emission_ended"},
            },
        ]
        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=50)
        flee_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "flee_emission"
        ]
        assert not flee_decisions, (
            "Bot should NOT flee when emission_ended supersedes emission_imminent"
        )

    def test_warning_state_reset_after_emission_ends(self):
        """emission_warning_written_turn and offset are cleared when emission ends."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_state_with_emission(scheduled_turn=5, current_turn=5,
                                               warning_written=3, warning_offset=2)
        # Tick at emission start — starts emission
        state["world_turn"] = 5
        state, _ = tick_zone_map(state)
        assert state.get("emission_active") is True
        # Tick until emission ends
        ends_turn = state.get("emission_ends_turn", 10)
        state["world_turn"] = ends_turn
        state, _ = tick_zone_map(state)
        assert state.get("emission_warning_written_turn") is None
        assert state.get("emission_warning_offset") is None


# ─────────────────────────────────────────────────────────────────
# Emission shelter priority (wait_in_shelter)
# ─────────────────────────────────────────────────────────────────

class TestEmissionShelterBehavior:
    """Verify that an agent with emission_imminent in memory stays put when
    already on safe terrain instead of starting new work."""

    def _make_safe_state(self):
        """Two locations: S (building=safe) and D (plain=dangerous). Agent at S."""
        state = _make_minimal_state(
            {
                "S": {"connections": [{"to": "D", "travel_time": 1}]},
                "D": {"connections": [{"to": "S", "travel_time": 1}]},
            },
            agent_loc_id="S",
        )
        state["locations"]["S"]["terrain_type"] = "buildings"
        state["locations"]["D"]["terrain_type"] = "plain"
        state["emission_active"] = False
        state["emission_scheduled_turn"] = 200
        return state

    def _inject_emission_imminent(self, agent, world_turn=1, scheduled_turn=200):
        agent["memory"].append({
            "type": "observation",
            "world_turn": world_turn,
            "title": "⚠️ Скоро выброс!",
            "summary": "...",
            "effects": {
                "action_kind": "emission_imminent",
                "turns_until": 12,
                "emission_scheduled_turn": scheduled_turn,
            },
        })

    def test_bot_waits_in_shelter_when_emission_imminent(self):
        """Agent on safe terrain with emission_imminent memory should return []
        (no new scheduled action) and write a wait_in_shelter decision."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        state = self._make_safe_state()
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        agent_id = next(iter(state["agents"]))
        events = _run_bot_action(agent_id, agent, state, world_turn=2)

        assert events == [], "Bot should return no events while sheltering"
        assert agent.get("scheduled_action") is None, "No travel should be scheduled"
        shelter_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "wait_in_shelter"
        ]
        assert shelter_decisions, "Bot should write wait_in_shelter decision memory"

    def test_shelter_decision_written_only_once(self):
        """Calling _run_bot_action repeatedly while emission_imminent should not
        spam duplicate wait_in_shelter memories."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        state = self._make_safe_state()
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=2)
        _run_bot_action(agent_id, agent, state, world_turn=3)
        _run_bot_action(agent_id, agent, state, world_turn=4)

        shelter_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "wait_in_shelter"
        ]
        assert len(shelter_decisions) == 1, (
            f"Expected exactly 1 wait_in_shelter memory, got {len(shelter_decisions)}"
        )

    def test_shelter_superseded_by_emission_ended(self):
        """After emission_ended, the agent should resume normal decisions (not shelter)."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        state = self._make_safe_state()
        # Add some anomaly so the bot has something to do
        state["locations"]["S"]["anomaly_activity"] = 5
        state["emission_scheduled_turn"] = 5000  # far in the future
        agent = next(iter(state["agents"].values()))
        # Emission_imminent at turn 1, then emission_ended at turn 50
        self._inject_emission_imminent(agent, world_turn=1)
        agent["memory"].append({
            "type": "observation",
            "world_turn": 50,
            "title": "✅ Выброс закончился",
            "summary": "...",
            "effects": {"action_kind": "emission_ended"},
        })

        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=51)

        # Should NOT have added another wait_in_shelter after the emission ended
        shelter_after_ended = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "wait_in_shelter"
            and m.get("world_turn", 0) > 50
        ]
        assert not shelter_after_ended, (
            "Bot should not shelter after emission_ended supersedes emission_imminent"
        )

    def test_bot_still_flees_dangerous_terrain_when_emission_imminent(self):
        """Agent on DANGEROUS terrain with emission_imminent should flee (not just wait)."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        state = self._make_safe_state()
        agent = next(iter(state["agents"].values()))
        # Move agent to the dangerous location
        agent["location_id"] = "D"
        self._inject_emission_imminent(agent, world_turn=1)

        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=2)

        flee_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "flee_emission"
        ]
        assert flee_decisions, "Bot on dangerous terrain should flee when emission_imminent"
        # scheduled_action should be a travel action
        assert agent.get("scheduled_action") is not None, (
            "Bot should have scheduled a travel action to flee"
        )

    def test_bot_trapped_on_dangerous_terrain_no_safe_neighbour(self):
        """Agent on dangerous terrain with no safe neighbour should write
        trapped_on_dangerous_terrain decision and NOT write wait_in_shelter."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        # Both D1 (hills) and D2 (plain) are in _EMISSION_DANGEROUS_TERRAIN,
        # so there is no safe escape route from D1.
        state = _make_minimal_state(
            {
                "D1": {"connections": [{"to": "D2", "travel_time": 1}]},
                "D2": {"connections": [{"to": "D1", "travel_time": 1}]},
            },
            agent_loc_id="D1",
        )
        state["locations"]["D1"]["terrain_type"] = "hills"
        state["locations"]["D2"]["terrain_type"] = "plain"  # also dangerous
        state["emission_active"] = False
        state["emission_scheduled_turn"] = 200

        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        agent_id = next(iter(state["agents"]))
        events = _run_bot_action(agent_id, agent, state, world_turn=2)

        # Should return empty (agent stays put, nothing to do)
        assert events == [], "Trapped agent should return no events"
        # Must NOT claim it is in a safe shelter
        shelter_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "wait_in_shelter"
        ]
        assert not shelter_decisions, (
            "Trapped agent on dangerous terrain must NOT write wait_in_shelter"
        )
        # Must log the trapped situation
        trapped_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "trapped_on_dangerous_terrain"
        ]
        assert trapped_decisions, (
            "Trapped agent should write trapped_on_dangerous_terrain decision"
        )

    def test_bot_trapped_decision_written_only_once(self):
        """Repeated calls while trapped should not spam trapped_on_dangerous_terrain memories."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action
        # Both locations are in _EMISSION_DANGEROUS_TERRAIN → agent is trapped
        state = _make_minimal_state(
            {
                "D1": {"connections": [{"to": "D2", "travel_time": 1}]},
                "D2": {"connections": [{"to": "D1", "travel_time": 1}]},
            },
            agent_loc_id="D1",
        )
        state["locations"]["D1"]["terrain_type"] = "hills"
        state["locations"]["D2"]["terrain_type"] = "plain"  # also dangerous
        state["emission_active"] = False
        state["emission_scheduled_turn"] = 200

        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        agent_id = next(iter(state["agents"]))
        _run_bot_action(agent_id, agent, state, world_turn=2)
        _run_bot_action(agent_id, agent, state, world_turn=3)
        _run_bot_action(agent_id, agent, state, world_turn=4)

        trapped_decisions = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "trapped_on_dangerous_terrain"
        ]
        assert len(trapped_decisions) == 1, (
            f"Expected exactly 1 trapped_on_dangerous_terrain memory, got {len(trapped_decisions)}"
        )


# ─────────────────────────────────────────────────────────────────
# debug_trigger_emission via world_rules
# ─────────────────────────────────────────────────────────────────

class TestDebugTriggerEmission:
    """Verify that debug_trigger_emission schedules the emission 10–15 turns
    ahead, broadcasts emission_imminent, and does NOT start the emission
    immediately."""

    def _make_world_state(self):
        state = _make_minimal_state(
            {"A": {"connections": []}},
            agent_loc_id="A",
        )
        state["locations"]["A"]["terrain_type"] = "buildings"
        state["emission_active"] = False
        state["emission_scheduled_turn"] = None
        state["world_turn"] = 100
        state["seed"] = 42
        return state

    def _run_command(self, state, command_type, payload=None):
        from app.games.zone_stalkers.rules.world_rules import resolve_world_command
        return resolve_world_command(command_type, payload or {}, state, "player1")

    def test_emission_is_not_active_immediately(self):
        """debug_trigger_emission should NOT immediately start the emission."""
        state = self._make_world_state()
        new_state, events = self._run_command(state, "debug_trigger_emission")
        assert not new_state.get("emission_active"), (
            "emission_active must be False right after debug_trigger_emission"
        )

    def test_emission_scheduled_10_to_15_turns_ahead(self):
        """The scheduled turn should be world_turn + [10..15]."""
        from app.games.zone_stalkers.rules.tick_rules import (
            _EMISSION_WARNING_MIN_TURNS, _EMISSION_WARNING_MAX_TURNS
        )
        state = self._make_world_state()
        world_turn = state["world_turn"]
        new_state, _ = self._run_command(state, "debug_trigger_emission")
        scheduled = new_state.get("emission_scheduled_turn")
        assert scheduled is not None
        offset = scheduled - world_turn
        assert _EMISSION_WARNING_MIN_TURNS <= offset <= _EMISSION_WARNING_MAX_TURNS, (
            f"Expected offset {_EMISSION_WARNING_MIN_TURNS}–{_EMISSION_WARNING_MAX_TURNS}, got {offset}"
        )

    def test_emission_imminent_written_to_alive_agents(self):
        """All alive agents should receive an emission_imminent observation."""
        state = self._make_world_state()
        new_state, _ = self._run_command(state, "debug_trigger_emission")
        agent = next(iter(new_state["agents"].values()))
        warnings = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "emission_imminent"
        ]
        assert len(warnings) == 1, (
            f"Expected 1 emission_imminent memory, got {len(warnings)}"
        )

    def test_event_emitted_with_correct_payload(self):
        """debug_emission_triggered event should carry scheduled_turn and turns_until."""
        state = self._make_world_state()
        _, events = self._run_command(state, "debug_trigger_emission")
        trig = [e for e in events if e.get("event_type") == "debug_emission_triggered"]
        assert len(trig) == 1
        payload = trig[0]["payload"]
        assert "emission_scheduled_turn" in payload
        assert "turns_until" in payload
        assert payload.get("emission_active") is False

    def test_warning_not_duplicated_by_subsequent_ticks(self):
        """After debug_trigger_emission, normal ticks should NOT write a second
        emission_imminent observation (warning_written_turn prevents it)."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_world_state()
        new_state, _ = self._run_command(state, "debug_trigger_emission")
        # Tick several times; agent should still have exactly 1 emission_imminent
        for _ in range(5):
            new_state["world_turn"] += 1
            new_state, _ = tick_zone_map(new_state)
        agent = next(iter(new_state["agents"].values()))
        warnings = [
            m for m in agent["memory"]
            if m.get("effects", {}).get("action_kind") == "emission_imminent"
        ]
        assert len(warnings) == 1, (
            f"Expected 1 emission_imminent memory after ticks, got {len(warnings)}"
        )


# ─────────────────────────────────────────────────────────────────
# Emission interrupt during anomaly exploration
# ─────────────────────────────────────────────────────────────────

class TestExplorationEmissionInterrupt:
    """Verify that a bot mid-exploration cancels the exploration and flees/shelters
    when it has an active emission warning in memory."""

    def _make_explore_state(self, terrain="plain"):
        """Two locations: A (anomaly, configured terrain) and B (building=safe).
        Agent at A with an in-progress explore_anomaly_location scheduled action."""
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 1}]},
                "B": {"connections": [{"to": "A", "travel_time": 1}]},
            },
            agent_loc_id="A",
        )
        state["locations"]["A"]["terrain_type"] = terrain
        state["locations"]["A"]["anomaly_activity"] = 5
        state["locations"]["B"]["terrain_type"] = "buildings"
        state["emission_active"] = False
        state["emission_scheduled_turn"] = 200
        state["seed"] = 0
        # Put agent mid-exploration (5 turns remaining out of 30)
        agent = next(iter(state["agents"].values()))
        agent["scheduled_action"] = {
            "type": "explore_anomaly_location",
            "turns_remaining": 5,
            "turns_total": 30,
            "target_id": "A",
            "started_turn": 1,
        }
        return state

    def _inject_emission_imminent(self, agent, world_turn=1, scheduled_turn=200):
        agent["memory"].append({
            "type": "observation",
            "world_turn": world_turn,
            "title": "⚠️ Скоро выброс!",
            "summary": "...",
            "effects": {
                "action_kind": "emission_imminent",
                "turns_until": 12,
                "emission_scheduled_turn": scheduled_turn,
            },
        })

    def test_exploration_interrupted_by_emission_warning(self):
        """Mid-exploration with emission_imminent memory: exploration action cancelled.
        On dangerous terrain the bot immediately schedules a flee travel, so we
        only verify that the original explore_anomaly_location is no longer active."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_explore_state(terrain="plain")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, events = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        # The exploration action must no longer be the scheduled action
        sched = new_agent.get("scheduled_action")
        assert sched is None or sched.get("type") != "explore_anomaly_location", (
            "Exploration action should have been cancelled when emission_imminent is in memory"
        )

    def test_interruption_memory_written(self):
        """After interrupting exploration, an exploration_interrupted memory entry is written."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_explore_state(terrain="plain")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        interrupted = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "exploration_interrupted"
        ]
        assert interrupted, "exploration_interrupted memory entry should be written"

    def test_bot_flees_after_interrupt_on_dangerous_terrain(self):
        """After interrupting exploration on dangerous terrain, bot schedules flee travel."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_explore_state(terrain="plain")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        # Bot should have scheduled a flee travel to safe terrain
        sched = new_agent.get("scheduled_action")
        assert sched is not None, "Bot should schedule travel to flee after interruption"
        assert sched["type"] == "travel", f"Expected travel action, got {sched.get('type')}"
        # Target must be safe terrain (B = building)
        assert sched.get("final_target_id") == "B" or sched.get("target_id") == "B", (
            "Bot should flee toward safe terrain (building B)"
        )

    def test_bot_shelters_after_interrupt_on_safe_terrain(self):
        """After interrupting exploration on safe terrain, bot writes wait_in_shelter."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_explore_state(terrain="buildings")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        shelter = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "wait_in_shelter"
        ]
        assert shelter, "Bot should write wait_in_shelter after interruption on safe terrain"
        # No travel should be scheduled (already safe)
        sched = new_agent.get("scheduled_action")
        assert sched is None, "Bot should NOT schedule travel when already on safe terrain"

    def test_exploration_not_interrupted_without_warning(self):
        """Without emission warning, exploration continues normally."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_explore_state(terrain="plain")
        # No emission_imminent in memory

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        sched = new_agent.get("scheduled_action")
        assert sched is not None, "Exploration should continue without emission warning"
        assert sched["type"] == "explore_anomaly_location"
        assert sched["turns_remaining"] == 4, "turns_remaining should have decremented"

    def test_exploration_interrupted_by_active_emission(self):
        """Mid-exploration with emission_active=True: scheduled_action cleared."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_explore_state(terrain="buildings")
        state["emission_active"] = True
        state["emission_ends_turn"] = 300

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        assert new_agent.get("scheduled_action") is None, (
            "Exploration should be interrupted when emission_active=True"
        )


# ─────────────────────────────────────────────────────────────────
# Emission interrupt during travel
# ─────────────────────────────────────────────────────────────────

class TestTravelEmissionInterrupt:
    """Verify that a bot mid-travel cancels the trip and flees/shelters when
    an emission warning is active in memory.  Two cases are covered:

    1. Mid-hop (turns_remaining > 0): hop has not yet completed.
    2. Post-hop (turns_remaining == 0, remaining_route non-empty): bot arrives
       at an intermediate hop and would normally schedule the next hop but must
       not do so when emission is imminent.
    """

    def _make_travel_state(self, terrain_at_current="plain", hop_turns=5):
        """Three locations:
          A (origin, configured terrain) -- hop_turns --> B (intermediate, hills)
          B (intermediate) -- 1 --> C (destination, building)
        Agent is mid-trip A→B→C, currently at A with travel scheduled to B.
        """
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": hop_turns}]},
                "B": {"connections": [{"to": "A", "travel_time": hop_turns},
                                       {"to": "C", "travel_time": 1}]},
                "C": {"connections": [{"to": "B", "travel_time": 1}]},
            },
            agent_loc_id="A",
        )
        state["locations"]["A"]["terrain_type"] = terrain_at_current
        state["locations"]["B"]["terrain_type"] = "hills"
        state["locations"]["C"]["terrain_type"] = "buildings"
        state["emission_active"] = False
        state["emission_scheduled_turn"] = 200
        state["seed"] = 0
        agent = next(iter(state["agents"].values()))
        # Agent is mid-hop toward B (hop_turns - 1 turns remaining)
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": hop_turns - 1,  # > 0 → mid-hop case
            "turns_total": hop_turns,
            "target_id": "B",
            "final_target_id": "C",
            "remaining_route": ["C"],
            "started_turn": 1,
        }
        return state

    def _make_post_hop_state(self, terrain_at_B="hills"):
        """Agent has just arrived at B (turns_remaining will hit 0) with one
        more hop to C remaining.  B is on the given terrain."""
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 1}]},
                "B": {"connections": [{"to": "A", "travel_time": 1},
                                       {"to": "C", "travel_time": 1}]},
                "C": {"connections": [{"to": "B", "travel_time": 1}]},
            },
            agent_loc_id="A",
        )
        state["locations"]["A"]["terrain_type"] = "plain"
        state["locations"]["B"]["terrain_type"] = terrain_at_B
        state["locations"]["C"]["terrain_type"] = "buildings"
        state["emission_active"] = False
        state["emission_scheduled_turn"] = 200
        state["seed"] = 0
        agent = next(iter(state["agents"].values()))
        # turns_remaining=1 → will become 0 on next tick → hop completes, moves to B
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": 1,
            "turns_total": 1,
            "target_id": "B",
            "final_target_id": "C",
            "remaining_route": ["C"],
            "started_turn": 1,
        }
        return state

    def _inject_emission_imminent(self, agent, world_turn=1, scheduled_turn=200):
        agent["memory"].append({
            "type": "observation",
            "world_turn": world_turn,
            "title": "⚠️ Скоро выброс!",
            "summary": "...",
            "effects": {
                "action_kind": "emission_imminent",
                "turns_until": 12,
                "emission_scheduled_turn": scheduled_turn,
            },
        })

    # ── Mid-hop cases ────────────────────────────────────────────────────────

    def test_mid_hop_travel_interrupted_by_emission_warning(self):
        """Mid-hop travel is cancelled and replaced by flee_emission when emission_imminent is in memory.

        The original (non-emergency) travel is interrupted; the bot immediately
        schedules a new emergency flee travel (emergency_flee=True) on the same tick
        because the agent is on dangerous terrain.
        """
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_travel_state(hop_turns=5)
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        # The bot must have written a travel_interrupted entry (original travel cancelled)
        interrupted = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "travel_interrupted"
        ]
        assert interrupted, "travel_interrupted memory should be written when emission fires"

        # The bot should immediately reschedule a flee_emission travel on the same tick
        sched = new_agent.get("scheduled_action")
        assert sched is not None and sched.get("emergency_flee") is True, (
            "After interrupt on dangerous terrain, bot should schedule an emergency flee travel"
        )

    def test_mid_hop_travel_interrupted_memory_written(self):
        """travel_interrupted memory entry is written on mid-hop cancellation."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_travel_state(hop_turns=5)
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        interrupted = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "travel_interrupted"
        ]
        assert interrupted, "travel_interrupted memory entry should be written"
        assert interrupted[0]["effects"].get("reason") == "emission_warning"

    def test_mid_hop_travel_not_interrupted_without_warning(self):
        """Without emission warning, mid-hop travel continues normally."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_travel_state(hop_turns=5)
        # No emission_imminent in memory

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        sched = new_agent.get("scheduled_action")
        assert sched is not None, "Travel should continue without emission warning"
        assert sched["type"] == "travel"
        assert sched["turns_remaining"] == 3, "turns_remaining should decrement"

    def test_mid_hop_travel_interrupted_by_active_emission(self):
        """Mid-hop travel is cancelled and replaced by flee_emission when emission_active=True.

        Same as the emission_warning case: after the interrupt the bot immediately
        schedules an emergency flee travel because the agent is on dangerous terrain.
        """
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_travel_state(hop_turns=5)
        state["emission_active"] = True
        state["emission_ends_turn"] = 300

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        sched = new_agent.get("scheduled_action")
        assert sched is not None and sched.get("emergency_flee") is True, (
            "After interrupt on dangerous terrain during active emission, "
            "bot should schedule an emergency flee travel"
        )

    # ── Post-hop cases (hop completes, remaining_route non-empty) ────────────

    def test_post_hop_next_hop_not_scheduled_when_emission_warned(self):
        """After completing a hop with more hops remaining, the original multi-hop
        route is interrupted and no continuation is scheduled when on safe terrain.
        (On dangerous terrain the bot would instead schedule a flee action — see
        test_post_hop_bot_flees_dangerous_terrain_after_interrupt.)"""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        # Use safe terrain at B so the bot waits instead of scheduling a flee travel
        state = self._make_post_hop_state(terrain_at_B="buildings")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        # travel_interrupted must be in memory (route was cancelled)
        interrupted = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "travel_interrupted"
        ]
        assert interrupted, "travel_interrupted memory should confirm route was cancelled"
        # On safe terrain the bot waits in shelter — no new travel should be scheduled
        sched = new_agent.get("scheduled_action")
        assert sched is None, (
            "Bot on safe terrain should NOT schedule travel after post-hop interrupt"
        )

    def test_post_hop_travel_interrupted_memory_written(self):
        """travel_interrupted memory entry written when post-hop next hop is suppressed."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_post_hop_state(terrain_at_B="hills")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        interrupted = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "travel_interrupted"
        ]
        assert interrupted, "travel_interrupted memory should be written after post-hop interrupt"

    def test_post_hop_agent_moved_before_interrupt(self):
        """Even when emission interrupts the next hop, the agent still moved to
        the intermediate location (the completed hop is not reversed)."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_post_hop_state(terrain_at_B="hills")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        assert new_agent["location_id"] == "B", (
            "Agent should have moved to intermediate hop location B"
        )

    def test_post_hop_next_hop_scheduled_without_emission(self):
        """Without emission warning, the next hop IS scheduled after completing a hop."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_post_hop_state(terrain_at_B="hills")
        # No emission_imminent in memory

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        sched = new_agent.get("scheduled_action")
        assert sched is not None, "Next hop should be scheduled when no emission"
        assert sched["type"] == "travel"
        assert sched.get("target_id") == "C", "Next hop target should be C"

    def test_post_hop_bot_flees_dangerous_terrain_after_interrupt(self):
        """After post-hop interrupt on dangerous terrain, bot schedules flee travel."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_post_hop_state(terrain_at_B="hills")
        agent = next(iter(state["agents"].values()))
        self._inject_emission_imminent(agent, world_turn=1)

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        # After interrupt bot runs its decision logic and should flee from hills
        sched = new_agent.get("scheduled_action")
        flee_mems = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "flee_emission"
        ]
        assert flee_mems or (sched is not None and sched.get("type") == "travel"), (
            "Bot should flee from dangerous terrain after travel interrupt"
        )


# ─────────────────────────────────────────────────────────────────
# Emission flee-self-interrupt regression
# ─────────────────────────────────────────────────────────────────

class TestFleeEmissionSelfInterrupt:
    """Regression test: a bot fleeing from an emission must NOT have its own
    flee travel interrupted by the emission interrupt on the next tick.

    The bug (before the fix) was:
      tick N:   travel interrupted → flee travel scheduled (type="travel")
      tick N+1: flee travel also interrupted because _is_emission_threat is True
      tick N+2: repeated until the emission fires and the agent dies.
    """

    def _make_flee_state(self):
        """Two locations: A (plain, dangerous) → B (building, safe).
        Agent is at A with an in-progress flee travel to B."""
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 5}]},
                "B": {"connections": [{"to": "A", "travel_time": 5}]},
            },
            agent_loc_id="A",
        )
        state["locations"]["A"]["terrain_type"] = "plain"
        state["locations"]["B"]["terrain_type"] = "buildings"
        state["emission_active"] = False
        state["emission_scheduled_turn"] = 200
        state["seed"] = 0
        agent = next(iter(state["agents"].values()))
        # Simulate the state after the flee decision was made:
        # - Agent has an emission_imminent in memory
        # - scheduled_action is a flee travel (emergency_flee=True) to B
        agent["memory"].append({
            "type": "observation",
            "world_turn": 1,
            "title": "⚠️ Скоро выброс!",
            "summary": "...",
            "effects": {
                "action_kind": "emission_imminent",
                "turns_until": 10,
                "emission_scheduled_turn": 200,
            },
        })
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": 4,  # still 4 ticks away
            "turns_total": 5,
            "target_id": "B",
            "final_target_id": "B",
            "remaining_route": [],
            "started_turn": 1,
            "emergency_flee": True,
        }
        return state

    def test_flee_travel_not_interrupted(self):
        """Flee travel (emergency_flee=True) is NOT cancelled on the next tick
        even though emission_imminent is still in memory."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_flee_state()

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        sched = new_agent.get("scheduled_action")
        assert sched is not None, "Flee travel must not be cancelled by emission interrupt"
        assert sched["type"] == "travel", "Flee travel must remain active"
        assert sched.get("emergency_flee") is True, "emergency_flee flag must be preserved"

    def test_flee_travel_continues_across_multiple_ticks(self):
        """Flee travel persists for several ticks despite ongoing emission threat."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_flee_state()

        for tick in range(3):
            state, _ = tick_zone_map(state)
            agent = next(iter(state["agents"].values()))
            sched = agent.get("scheduled_action")
            # Agent may have completed travel (sched=None) but must NOT have
            # been interrupted (which would cause travel_interrupted in memory).
            interrupted = [
                m for m in agent["memory"]
                if m.get("effects", {}).get("action_kind") == "travel_interrupted"
                and m.get("world_turn", 0) > 1  # only entries written by these ticks
            ]
            assert not interrupted, (
                f"Flee travel was interrupted at tick {tick + 2} — "
                "emergency_flee flag must suppress the interrupt"
            )

    def test_normal_travel_still_interrupted(self):
        """Regular (non-flee) travel is still interrupted when emission is warned."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = _make_minimal_state(
            {
                "A": {"connections": [{"to": "B", "travel_time": 5}]},
                "B": {"connections": [{"to": "A", "travel_time": 5}]},
            },
            agent_loc_id="A",
        )
        state["locations"]["A"]["terrain_type"] = "plain"
        state["locations"]["B"]["terrain_type"] = "buildings"
        state["emission_active"] = False
        state["seed"] = 0
        agent = next(iter(state["agents"].values()))
        # Normal travel (no emergency_flee flag) while emission is warned
        agent["memory"].append({
            "type": "observation",
            "world_turn": 1,
            "title": "⚠️ Скоро выброс!",
            "summary": "...",
            "effects": {
                "action_kind": "emission_imminent",
                "turns_until": 10,
                "emission_scheduled_turn": 200,
            },
        })
        agent["scheduled_action"] = {
            "type": "travel",
            "turns_remaining": 4,
            "turns_total": 5,
            "target_id": "B",
            "final_target_id": "B",
            "remaining_route": [],
            "started_turn": 1,
            # No emergency_flee flag → should be interrupted
        }

        new_state, _ = tick_zone_map(state)
        new_agent = next(iter(new_state["agents"].values()))

        sched = new_agent.get("scheduled_action")
        # After interrupt, bot may reschedule a flee OR stay sheltered (B is safe anyway)
        interrupted = [
            m for m in new_agent["memory"]
            if m.get("effects", {}).get("action_kind") == "travel_interrupted"
        ]
        # The travel should have been interrupted (unless it completed, which it can't at 4 ticks)
        assert interrupted or (sched is None), (
            "Normal travel without emergency_flee should be interrupted by emission warning"
        )


# ─────────────────────────────────────────────────────────────────────────────
# New mechanic: secret documents + unravel_zone_mystery goal
# ─────────────────────────────────────────────────────────────────────────────

class TestSecretDocumentItems:
    """Verify new secret_document item category is correctly defined."""

    def test_secret_document_types_in_item_catalogue(self):
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, SECRET_DOCUMENT_ITEM_TYPES
        assert len(SECRET_DOCUMENT_ITEM_TYPES) >= 3, "At least 3 secret document types expected"
        for key in SECRET_DOCUMENT_ITEM_TYPES:
            assert key in ITEM_TYPES
            assert ITEM_TYPES[key]["type"] == "secret_document"

    def test_secret_document_fields(self):
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, SECRET_DOCUMENT_ITEM_TYPES
        for key in SECRET_DOCUMENT_ITEM_TYPES:
            info = ITEM_TYPES[key]
            assert "name" in info
            assert "weight" in info
            assert "value" in info
            assert info["value"] > 0

    def test_generator_places_secret_documents(self):
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = generate_zone(seed=1, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        found = [
            item
            for loc in state["locations"].values()
            for item in loc.get("items", [])
            if item.get("type") in SECRET_DOCUMENT_ITEM_TYPES
        ]
        assert len(found) >= 1, "Generator should place at least one secret document in the zone"


class TestUnravelZoneMysteryGoal:
    """Tests for the unravel_zone_mystery global goal bot behaviour."""

    def _make_mystery_agent(self, loc_id: str, state: dict, rng_seed: int = 0) -> dict:
        """Return a bot agent that is ready to pursue unravel_zone_mystery."""
        import random
        rng = random.Random(rng_seed)
        return {
            "id": "agent_ai_m",
            "archetype": "stalker_agent",
            "name": "Агент-Исследователь",
            "location_id": loc_id,
            "hp": 100, "max_hp": 100, "radiation": 0,
            "hunger": 10, "thirst": 10, "sleepiness": 10,
            "money": 5000,
            "inventory": [],
            "equipment": {"weapon": None, "armor": None, "detector": None},
            "faction": "loner",
            "controller": {"kind": "bot", "participant_id": None},
            "is_alive": True,
            "action_used": False,
            "reputation": 0,
            "experience": 0,
            "skill_combat": 1, "skill_stalker": 1, "skill_trade": 1,
            "skill_medicine": 1, "skill_social": 1, "skill_survival": 1,
            "skill_survival_xp": 0.0,
            "global_goal": "unravel_zone_mystery",
            "current_goal": None,
            "risk_tolerance": 0.5,
            "material_threshold": 1,  # already over threshold → goal pursuit phase
            "scheduled_action": None,
            "action_queue": [],
            "memory": [],
        }

    def _make_mystery_agent_equipped(self, loc_id: str, state: dict) -> dict:
        """Return an agent with weapon, armor, and ammo so equipment-purchase logic is skipped."""
        from app.games.zone_stalkers.balance.items import ITEM_TYPES
        agent = self._make_mystery_agent(loc_id, state)
        agent["equipment"] = {
            "weapon": {"id": "w1", "type": "pistol",
                       **{k: ITEM_TYPES["pistol"][k] for k in ("name", "weight", "value")}},
            "armor":  {"id": "a1", "type": "leather_jacket",
                       **{k: ITEM_TYPES["leather_jacket"][k] for k in ("name", "weight", "value")}},
            "detector": None,
        }
        ammo_info = ITEM_TYPES["ammo_9mm"]
        agent["inventory"] = [
            {"id": "ammo1", "type": "ammo_9mm",
             **{k: ammo_info[k] for k in ("name", "weight", "value")}},
        ]
        return agent

    def _minimal_state(self, loc_id: str = "A") -> dict:
        """Minimal two-location state for testing."""
        return {
            "locations": {
                "A": {
                    "name": "Лаборатория X-18",
                    "terrain_type": "x_lab",
                    "anomaly_activity": 0,
                    "items": [],
                    "artifacts": [],
                    "agents": ["agent_ai_m"],
                    "connections": [{"to": "B", "type": "normal"}],
                },
                "B": {
                    "name": "Равнина",
                    "terrain_type": "plain",
                    "anomaly_activity": 0,
                    "items": [],
                    "artifacts": [],
                    "agents": [],
                    "connections": [{"to": "A", "type": "normal"}],
                },
            },
            "agents": {},
            "traders": {},
            "mutants": {},
            "world_turn": 1,
            "emission_active": False,
        }

    def test_unravel_zone_mystery_is_valid_global_goal(self):
        from app.games.zone_stalkers.rules.world_rules import _VALID_GLOBAL_GOALS
        assert "unravel_zone_mystery" in _VALID_GLOBAL_GOALS

    def test_bot_with_no_memory_wanders(self):
        """Bot with unravel_zone_mystery and no doc intel should wander."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action_inner
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent
        _run_bot_action_inner("agent_ai_m", agent, state, 1)
        decisions = [
            m for m in agent["memory"]
            if m.get("type") == "decision"
        ]
        assert decisions, "Bot should record a decision"
        # Should wander or seek item
        action_kinds = {d["effects"].get("action_kind") for d in decisions}
        assert action_kinds.intersection(
            {"wander", "seek_item"}
        ), f"Expected wander or seek_item, got: {action_kinds}"

    def test_bot_goes_to_known_doc_location(self):
        """Bot should travel to a remembered secret document location."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action_inner, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent
        # Give agent a memory of seeing a secret document in location B
        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        _add_memory(
            agent, 0, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        _run_bot_action_inner("agent_ai_m", agent, state, 1)
        # Agent should decide to seek_item for destination B
        seek_decisions = [
            m for m in agent["memory"]
            if m.get("type") == "decision"
            and m["effects"].get("action_kind") == "seek_item"
            and m["effects"].get("destination") == "B"
            and m["effects"].get("item_category") == "secret_document"
        ]
        assert seek_decisions, "Bot with doc memory should seek_item to known location"

    def test_bot_picks_up_doc_from_ground(self):
        """Bot at a location with a secret document on the ground should pick it up."""
        from app.games.zone_stalkers.rules.tick_rules import _run_bot_action_inner
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, SECRET_DOCUMENT_ITEM_TYPES
        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        doc_info = ITEM_TYPES[doc_type]
        state = self._minimal_state("A")
        state["locations"]["A"]["items"].append({
            "id": "doc_test_001",
            "type": doc_type,
            "name": doc_info["name"],
            "weight": doc_info["weight"],
            "value": doc_info["value"],
        })
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent
        _run_bot_action_inner("agent_ai_m", agent, state, 1)
        inv_types = {i["type"] for i in agent.get("inventory", [])}
        assert doc_type in inv_types, "Bot should pick up secret document from ground"

    def test_bot_gets_intel_from_colocated_stalker(self):
        """Bot should receive intel from a co-located stalker who saw secret documents."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        # Create a co-located stalker who has memory of a secret document in B
        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        _add_memory(
            informant, 0, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant

        result_loc = _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES,
            "секретные документы", state, 1
        )
        assert result_loc == "B", "Should receive intel about location B from co-located stalker"
        # Asking agent should have an intel_from_stalker observation in memory
        intel_mems = [
            m for m in agent["memory"]
            if m["effects"].get("action_kind") == "intel_from_stalker"
        ]
        assert intel_mems, "Agent should have intel_from_stalker memory entry"
        assert intel_mems[0]["effects"]["location_id"] == "B"
        assert intel_mems[0]["effects"]["source_agent_name"] == "Информатор"

    def test_intel_deduplication_same_turn(self):
        """Asking twice in the same turn from the same stalker should not write duplicate entries."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        _add_memory(
            informant, 0, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant

        _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES, "секретные документы", state, 1
        )
        _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES, "секретные документы", state, 1
        )
        intel_mems = [
            m for m in agent["memory"]
            if m["effects"].get("action_kind") == "intel_from_stalker"
        ]
        assert len(intel_mems) == 1, "Should not write duplicate intel from same stalker in same turn"

    def test_ask_stalker_returns_all_intel(self):
        """When a stalker has observations about multiple locations, all should be written."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        # Add a third location C
        state["locations"]["C"] = {
            "name": "Лаборатория X-18 Сектор 2",
            "terrain_type": "x_lab",
            "anomaly_activity": 0,
            "items": [], "artifacts": [],
            "agents": [],
            "connections": [{"to": "A", "type": "normal"}],
        }
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        # Informant has seen docs in BOTH B and C
        _add_memory(
            informant, 1, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        _add_memory(
            informant, 2, state, "observation",
            "Вижу предметы в «Лаборатория»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "C", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant

        result_loc = _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES,
            "секретные документы", state, 3
        )
        # Should receive intel from BOTH locations
        intel_mems = [
            m for m in agent["memory"]
            if m["effects"].get("action_kind") == "intel_from_stalker"
        ]
        intel_locs = {m["effects"]["location_id"] for m in intel_mems}
        assert intel_locs == {"B", "C"}, (
            f"Should receive intel about both B and C, got: {intel_locs}"
        )
        # Return value should be the first loc found
        assert result_loc in {"B", "C"}, f"Return value should be B or C, got: {result_loc}"

    def test_ask_stalker_all_locations_from_multiple_stalkers(self):
        """Intel from multiple co-located stalkers should all be collected."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        state["locations"]["C"] = {
            "name": "Бункер",
            "terrain_type": "scientific_bunker",
            "anomaly_activity": 0,
            "items": [], "artifacts": [],
            "agents": [],
            "connections": [{"to": "A", "type": "normal"}],
        }
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))

        # Stalker 1 knows about location B
        stalker1 = {
            "id": "stalker_1", "archetype": "stalker_agent", "name": "Сталкер Один",
            "location_id": "A", "is_alive": True, "memory": [],
        }
        _add_memory(stalker1, 1, state, "observation", "Вижу предметы",
                    f"На земле: {doc_type}.",
                    {"observed": "items", "location_id": "B", "item_types": [doc_type]})
        state["agents"]["stalker_1"] = stalker1

        # Stalker 2 knows about location C
        stalker2 = {
            "id": "stalker_2", "archetype": "stalker_agent", "name": "Сталкер Два",
            "location_id": "A", "is_alive": True, "memory": [],
        }
        _add_memory(stalker2, 1, state, "observation", "Вижу предметы",
                    f"На земле: {doc_type}.",
                    {"observed": "items", "location_id": "C", "item_types": [doc_type]})
        state["agents"]["stalker_2"] = stalker2

        _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES, "секретные документы", state, 2
        )
        intel_locs = {
            m["effects"]["location_id"] for m in agent["memory"]
            if m["effects"].get("action_kind") == "intel_from_stalker"
        }
        assert intel_locs == {"B", "C"}, (
            f"Should receive intel from both stalkers about B and C, got: {intel_locs}"
        )

    def test_ask_stalker_skips_stale_intel_after_not_found(self):
        """Intel about a location that the asker already visited and found empty
        (item_not_found_here) at a turn >= the other stalker's obs turn is skipped."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        # Agent already resolved location B as not_found on turn 5
        agent["memory"].append({
            "type": "observation", "world_turn": 5,
            "label": "⚠️ Предмет исчез", "summary": "...",
            "effects": {
                "action_kind": "item_not_found_here",
                "source": "arrival",
                "location_id": "B",
                "item_types": [doc_type],
            },
        })

        # Informant saw the doc at B on turn 3 (BEFORE the asker resolved it on turn 5)
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        _add_memory(
            informant, 3, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant

        result_loc = _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES,
            "секретные документы", state, 6
        )
        assert result_loc is None, (
            f"Stale intel (obs_turn=3 <= resolved_turn=5) should be skipped; got {result_loc}"
        )
        intel_mems = [m for m in agent["memory"]
                      if m.get("effects", {}).get("action_kind") == "intel_from_stalker"]
        assert len(intel_mems) == 0, (
            f"Should not write stale intel entry; got {intel_mems}"
        )

    def test_ask_stalker_skips_stale_intel_after_picked_up(self):
        """Intel about a location that the asker already resolved via item_picked_up_here
        at a turn >= the stalker's obs turn is also skipped."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        # Agent already picked up from B on turn 7
        agent["memory"].append({
            "type": "observation", "world_turn": 7,
            "label": "✅ Нашёл", "summary": "...",
            "effects": {
                "action_kind": "item_picked_up_here",
                "source": "seek_item_arrival",
                "location_id": "B",
                "item_types": [doc_type],
            },
        })

        # Informant saw the doc at B on turn 4 (BEFORE the asker picked it up on turn 7)
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        _add_memory(
            informant, 4, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant

        result_loc = _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES,
            "секретные документы", state, 8
        )
        assert result_loc is None, (
            f"Stale intel (obs_turn=4 <= resolved_turn=7) should be skipped; got {result_loc}"
        )

    def test_ask_stalker_keeps_fresh_intel_after_resolution(self):
        """If a stalker's obs_turn is strictly NEWER than the asker's resolved_turn,
        the intel is fresh (could be a re-spawn) and must NOT be skipped."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        # Agent resolved B on turn 5 (not_found)
        agent["memory"].append({
            "type": "observation", "world_turn": 5,
            "label": "⚠️ Предмет исчез", "summary": "...",
            "effects": {
                "action_kind": "item_not_found_here",
                "source": "arrival",
                "location_id": "B",
                "item_types": [doc_type],
            },
        })

        # Informant saw a NEW doc at B on turn 6 (AFTER the not_found)
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        _add_memory(
            informant, 6, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant

        result_loc = _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES,
            "секретные документы", state, 7
        )
        # obs_turn=6 > resolved_turn=5 → fresh intel, should NOT be skipped
        assert result_loc == "B", (
            f"Fresh intel (obs_turn=6 > resolved_turn=5) should be accepted; got {result_loc}"
        )

    def test_intel_from_stalker_includes_obs_timestamp(self):
        """intel_from_stalker summary must include the date/time the informant saw the item,
        and effects must carry obs_world_turn for downstream use."""
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_item, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent

        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        # obs_turn = 1*60 + 30 = 90 → Day 1, 01:30
        obs_turn = 90
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        _add_memory(
            informant, obs_turn, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant

        _bot_ask_colocated_stalkers_about_item(
            "agent_ai_m", agent, SECRET_DOCUMENT_ITEM_TYPES,
            "секретные документы", state, obs_turn + 1
        )
        intel_mems = [m for m in agent["memory"]
                      if m.get("effects", {}).get("action_kind") == "intel_from_stalker"]
        assert intel_mems, "Should have written intel_from_stalker entry"
        entry = intel_mems[0]
        # obs_world_turn must be stored in effects
        assert entry["effects"].get("obs_world_turn") == obs_turn, (
            f"Expected obs_world_turn={obs_turn}; got {entry['effects'].get('obs_world_turn')}"
        )
        # Summary must contain the time label: obs_turn=90 → Day 1 · 01:30
        summary = entry.get("summary", "")
        assert "День 1" in summary, f"Summary should contain 'День 1'; got: {summary!r}"
        assert "01:30" in summary, f"Summary should contain '01:30'; got: {summary!r}"

    def test_set_goal_via_world_command(self):
        """debug_spawn_stalker should accept 'unravel_zone_mystery' as global_goal."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        from app.games.zone_stalkers.rules.world_rules import validate_world_command, resolve_world_command
        state = generate_zone(seed=1, num_players=0, num_ai_stalkers=0, num_mutants=0, num_traders=0)
        loc_id = next(iter(state["locations"]))
        payload = {"loc_id": loc_id, "global_goal": "unravel_zone_mystery", "name": "Тест"}
        result = validate_world_command("debug_spawn_stalker", payload, state, "any_user")
        assert result.valid, f"Validation failed: {result.error}"
        new_state, _ = resolve_world_command("debug_spawn_stalker", payload, state, "any_user")
        new_agent_ids = set(new_state["agents"]) - set(state["agents"])
        assert new_agent_ids, "Should have spawned a new agent"
        new_agent = new_state["agents"][next(iter(new_agent_ids))]
        assert new_agent["global_goal"] == "unravel_zone_mystery"

    def test_bot_travels_to_trader_when_no_leads(self):
        """With no doc intel and a reachable trader, the bot should head to the trader."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._minimal_state("A")
        # Add a trader location C reachable from B
        state["locations"]["B"]["connections"].append({"to": "C", "type": "normal"})
        state["locations"]["C"] = {
            "name": "Торговая база",
            "terrain_type": "buildings",
            "anomaly_activity": 0,
            "items": [], "artifacts": [],
            "agents": ["trader_t1"],
            "connections": [{"to": "B", "type": "normal"}],
        }
        state["traders"]["trader_t1"] = {
            "id": "trader_t1",
            "name": "Торговец",
            "location_id": "C",
            "is_alive": True,
        }
        # Place agent at B (plain terrain, no interesting terrain connections back to x_lab)
        agent = self._make_mystery_agent("B", state)
        agent["location_id"] = "B"
        state["locations"]["B"]["agents"] = ["agent_ai_m"]
        state["locations"]["A"]["agents"] = []
        state["agents"]["agent_ai_m"] = agent
        _bot_pursue_goal("agent_ai_m", agent, "unravel_zone_mystery",
                         "B", state["locations"]["B"], state, 1, random.Random(0))
        decisions = [m for m in agent["memory"] if m.get("type") == "decision"]
        assert decisions, "Bot should record a decision"
        action_kinds = {d["effects"].get("action_kind") for d in decisions}
        assert "wait_at_trader" in action_kinds, (
            f"Bot with trader reachable should head to trader, got: {action_kinds}"
        )

    def test_bot_waits_at_trader_without_stalkers(self):
        """When at trader location with no other stalkers, bot should idle (wait_at_trader)."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._minimal_state("A")
        state["traders"]["trader_t1"] = {
            "id": "trader_t1",
            "name": "Торговец",
            "location_id": "A",
            "is_alive": True,
        }
        state["locations"]["A"]["agents"] = ["agent_ai_m", "trader_t1"]
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent
        _bot_pursue_goal("agent_ai_m", agent, "unravel_zone_mystery",
                         "A", state["locations"]["A"], state, 1, random.Random(0))
        decisions = [m for m in agent["memory"] if m.get("type") == "decision"]
        assert decisions, "Bot should record a decision"
        action_kinds = {d["effects"].get("action_kind") for d in decisions}
        assert "wait_at_trader" in action_kinds, (
            f"Bot at trader with no other stalkers should wait, got: {action_kinds}"
        )
        assert agent.get("action_used"), "Bot should have consumed its action while waiting"

    def test_bot_wait_at_trader_no_spam(self):
        """Waiting at trader should not write repeated decision entries on consecutive ticks."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._minimal_state("A")
        state["traders"]["trader_t1"] = {
            "id": "trader_t1",
            "name": "Торговец",
            "location_id": "A",
            "is_alive": True,
        }
        state["locations"]["A"]["agents"] = ["agent_ai_m", "trader_t1"]
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent
        # Simulate two consecutive ticks
        _bot_pursue_goal("agent_ai_m", agent, "unravel_zone_mystery",
                         "A", state["locations"]["A"], state, 1, random.Random(0))
        agent["action_used"] = False
        _bot_pursue_goal("agent_ai_m", agent, "unravel_zone_mystery",
                         "A", state["locations"]["A"], state, 2, random.Random(0))
        wait_decisions = [
            m for m in agent["memory"]
            if m.get("type") == "decision"
            and m["effects"].get("action_kind") == "wait_at_trader"
        ]
        assert len(wait_decisions) == 1, (
            f"wait_at_trader decision should not be written more than once, got: {len(wait_decisions)}"
        )

    def test_bot_asks_stalker_at_trader_location(self):
        """When at trader and a non-trader stalker appears, bot should ask them about docs."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal, _add_memory
        from app.games.zone_stalkers.balance.items import SECRET_DOCUMENT_ITEM_TYPES
        state = self._minimal_state("A")
        state["traders"]["trader_t1"] = {
            "id": "trader_t1",
            "name": "Торговец",
            "location_id": "A",
            "is_alive": True,
        }
        state["locations"]["A"]["agents"] = ["agent_ai_m", "trader_t1", "agent_informant"]
        agent = self._make_mystery_agent("A", state)
        state["agents"]["agent_ai_m"] = agent
        # Add an informant who has seen a secret document in B
        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        informant = {
            "id": "agent_informant",
            "archetype": "stalker_agent",
            "name": "Информатор",
            "location_id": "A",
            "is_alive": True,
            "memory": [],
        }
        _add_memory(
            informant, 0, state, "observation",
            "Вижу предметы в «Равнина»",
            f"На земле: {doc_type}.",
            {"observed": "items", "location_id": "B", "item_types": [doc_type]},
        )
        state["agents"]["agent_informant"] = informant
        _bot_pursue_goal("agent_ai_m", agent, "unravel_zone_mystery",
                         "A", state["locations"]["A"], state, 1, random.Random(0))
        # Bot should have asked the informant and decided to travel to B
        decisions = [m for m in agent["memory"] if m.get("type") == "decision"]
        action_kinds = {d["effects"].get("action_kind") for d in decisions}
        assert "seek_item" in action_kinds, (
            f"Bot at trader with informed stalker should seek_item, got: {action_kinds}"
        )
        seek_decision = next(
            d for d in decisions if d["effects"].get("action_kind") == "seek_item"
        )
        assert seek_decision["effects"].get("destination") == "B"

    def test_bot_continues_seeking_with_docs(self):
        """Agent that already has docs should continue searching for more, not idle."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        from app.games.zone_stalkers.balance.items import ITEM_TYPES, SECRET_DOCUMENT_ITEM_TYPES
        doc_type = next(iter(sorted(SECRET_DOCUMENT_ITEM_TYPES)))
        doc_info = ITEM_TYPES[doc_type]
        state = self._minimal_state("A")
        agent = self._make_mystery_agent("A", state)
        agent["inventory"] = [{"id": "doc_001", "type": doc_type, "name": doc_info["name"],
                                "weight": doc_info["weight"], "value": doc_info["value"]}]
        state["agents"]["agent_ai_m"] = agent
        _bot_pursue_goal("agent_ai_m", agent, "unravel_zone_mystery",
                         "A", state["locations"]["A"], state, 1, random.Random(0))
        decisions = [m for m in agent["memory"] if m.get("type") == "decision"]
        # Agent with docs should still write a decision (seek_item, wander, wait_at_trader)
        assert decisions, "Agent with docs should still take an action and write a decision"
        # Must NOT idle with goal_unravel_has_docs any more
        action_kinds = {d["effects"].get("action_kind") for d in decisions}
        assert "goal_unravel_has_docs" not in action_kinds, (
            f"Agent should not idle with goal_unravel_has_docs, got: {action_kinds}"
        )
        # Should be doing something active (seek, wander, wait at trader)
        assert action_kinds.intersection({"seek_item", "wander", "wait_at_trader"}), (
            f"Agent with docs should actively search for more, got: {action_kinds}"
        )


class TestKillStalkerGoal:
    """Tests for the kill_stalker global goal bot behaviour."""

    def _make_hunter(self, loc_id: str, target_id: str) -> dict:
        """Return a bot agent pursuing the kill_stalker goal."""
        return {
            "id": "agent_hunter",
            "archetype": "stalker_agent",
            "name": "Охотник",
            "location_id": loc_id,
            "hp": 100, "max_hp": 100, "radiation": 0,
            "hunger": 10, "thirst": 10, "sleepiness": 10,
            "money": 5000,
            "inventory": [],
            "equipment": {"weapon": None, "armor": None, "detector": None},
            "faction": "loner",
            "controller": {"kind": "bot", "participant_id": None},
            "is_alive": True,
            "action_used": False,
            "reputation": 0,
            "experience": 0,
            "skill_combat": 1, "skill_stalker": 1, "skill_trade": 1,
            "skill_medicine": 1, "skill_social": 1, "skill_survival": 1,
            "skill_survival_xp": 0.0,
            "global_goal": "kill_stalker",
            "kill_target_id": target_id,
            "current_goal": None,
            "risk_tolerance": 0.5,
            "material_threshold": 1,
            "wealth_goal_target": 100000,
            "global_goal_achieved": False,
            "has_left_zone": False,
            "scheduled_action": None,
            "action_queue": [],
            "memory": [],
        }

    def _make_target(self, loc_id: str) -> dict:
        """Return a simple target agent."""
        return {
            "id": "agent_target",
            "archetype": "stalker_agent",
            "name": "Цель",
            "location_id": loc_id,
            "hp": 100, "max_hp": 100, "radiation": 0,
            "hunger": 10, "thirst": 10, "sleepiness": 10,
            "money": 200,
            "inventory": [],
            "equipment": {"weapon": None, "armor": None, "detector": None},
            "faction": "loner",
            "controller": {"kind": "bot", "participant_id": None},
            "is_alive": True,
            "action_used": False,
            "reputation": 0,
            "experience": 0,
            "skill_combat": 1, "skill_stalker": 1, "skill_trade": 1,
            "skill_medicine": 1, "skill_social": 1, "skill_survival": 1,
            "skill_survival_xp": 0.0,
            "global_goal": "get_rich",
            "kill_target_id": None,
            "current_goal": None,
            "risk_tolerance": 0.5,
            "material_threshold": 5000,
            "wealth_goal_target": 100000,
            "global_goal_achieved": False,
            "has_left_zone": False,
            "scheduled_action": None,
            "action_queue": [],
            "memory": [],
        }

    def _minimal_state(self) -> dict:
        """Two-location state: A-trader, B-plain, connected."""
        return {
            "locations": {
                "A": {
                    "name": "Базовый лагерь",
                    "terrain_type": "field_camp",
                    "anomaly_activity": 0,
                    "items": [],
                    "agents": [],
                    "connections": [{"to": "B", "type": "road", "travel_time": 30, "closed": False}],
                    "exit_zone": False,
                },
                "B": {
                    "name": "Промзона",
                    "terrain_type": "industrial",
                    "anomaly_activity": 0,
                    "items": [],
                    "agents": [],
                    "connections": [{"to": "A", "type": "road", "travel_time": 30, "closed": False}],
                    "exit_zone": False,
                },
            },
            "agents": {},
            "mutants": {},
            "traders": {
                "trader_0": {
                    "id": "trader_0",
                    "archetype": "trader_npc",
                    "name": "Торговец",
                    "location_id": "A",
                    "inventory": [],
                    "money": 5000,
                    "memory": [],
                },
            },
            "world_turn": 10,
            "emission_active": False,
            "emission_ends_turn": 0,
        }

    def test_kill_target_at_same_location(self):
        """Hunter at same location as target should initiate combat interaction."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._minimal_state()
        state["combat_interactions"] = {}
        hunter = self._make_hunter("B", "agent_target")
        target = self._make_target("B")
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        events = _bot_pursue_goal(
            "agent_hunter", hunter, "kill_stalker",
            "B", state["locations"]["B"], state, 10, random.Random(0)
        )
        assert hunter["action_used"]
        # New behavior: combat_initiated instead of hunt_target_killed
        combat_decisions = [
            m for m in hunter["memory"]
            if m.get("effects", {}).get("action_kind") == "combat_initiated"
        ]
        assert combat_decisions, "Should have written combat_initiated decision"
        assert combat_decisions[0]["effects"]["target_id"] == "agent_target"
        assert any(e["event_type"] == "combat_initiated" for e in events)
        # Combat interaction should be created in state
        assert len(state["combat_interactions"]) == 1
        cid = list(state["combat_interactions"].keys())[0]
        ci = state["combat_interactions"][cid]
        assert "agent_hunter" in ci["participants"]
        assert "agent_target" in ci["participants"]

    def test_hunter_asks_colocated_stalker_about_target(self):
        """Hunter with no intel, at same location as an informed stalker, gets the target location."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._minimal_state()
        # Hunter is at A, target is at B, informant is also at A and has a stalker obs at B.
        hunter = self._make_hunter("A", "agent_target")
        target = self._make_target("B")
        informant = self._make_target("A")
        informant["id"] = "agent_informant"
        informant["name"] = "Информатор"
        # Informant has observed the target at B.
        informant["memory"] = [{
            "world_turn": 8,
            "type": "observation",
            "title": "Вижу сталкеров",
            "effects": {"observed": "stalkers", "location_id": "B", "names": ["Цель"]},
            "summary": "Видел Цель в Промзоне",
        }]
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        state["agents"]["agent_informant"] = informant
        _bot_pursue_goal(
            "agent_hunter", hunter, "kill_stalker",
            "A", state["locations"]["A"], state, 10, random.Random(0)
        )
        intel_obs = [
            m for m in hunter["memory"]
            if m.get("effects", {}).get("action_kind") == "intel_from_stalker"
            and m.get("effects", {}).get("observed") == "agent_location"
        ]
        assert intel_obs, "Should have written agent_location intel from informant"
        assert intel_obs[0]["effects"]["location_id"] == "B"

    def test_hunter_goes_to_trader_when_no_intel(self):
        """Hunter with no intel and no co-located informants should travel to nearest trader."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._minimal_state()
        hunter = self._make_hunter("B", "agent_target")
        target = self._make_target("A")  # target is at A, but hunter has no intel
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        events = _bot_pursue_goal(
            "agent_hunter", hunter, "kill_stalker",
            "B", state["locations"]["B"], state, 10, random.Random(0)
        )
        # Should decide to travel to trader (at A)
        decisions = [m for m in hunter["memory"] if m.get("type") == "decision"]
        assert decisions, "Should have written a decision"
        action_kinds = {d["effects"].get("action_kind") for d in decisions}
        assert "hunt_wait_at_trader" in action_kinds, (
            f"Should head to trader when no intel; got: {action_kinds}"
        )

    def test_hunter_waits_at_trader_antispan(self):
        """Hunter already at trader should wait and not spam hunt_wait_at_trader decisions."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._minimal_state()
        hunter = self._make_hunter("A", "agent_target")
        # Pre-seed an existing wait_at_trader decision to test anti-spam.
        hunter["memory"] = [{
            "world_turn": 9, "type": "decision",
            "title": "Жду у торговца",
            "effects": {"action_kind": "hunt_wait_at_trader", "location_id": "A"},
            "summary": "",
        }]
        target = self._make_target("B")
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        _bot_pursue_goal(
            "agent_hunter", hunter, "kill_stalker",
            "A", state["locations"]["A"], state, 10, random.Random(0)
        )
        assert hunter["action_used"]
        new_decisions = [
            m for m in hunter["memory"]
            if m.get("type") == "decision"
            and m.get("world_turn") == 10
        ]
        # Anti-spam: should NOT write a second hunt_wait_at_trader this turn.
        assert not new_decisions, (
            f"Anti-spam violated — should not re-write wait_at_trader; got: {new_decisions}"
        )

    def test_check_goal_completion_on_kill(self):
        """_check_global_goal_completion sets global_goal_achieved when kill recorded."""
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion
        state = self._minimal_state()
        hunter = self._make_hunter("B", "agent_target")
        target = self._make_target("B")
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        # Pre-seed a hunt_target_killed memory entry.
        hunter["memory"] = [{
            "world_turn": 9, "type": "observation",
            "title": "⚔️ Цель устранена",
            "effects": {"action_kind": "hunt_target_killed",
                        "target_id": "agent_target", "location_id": "B"},
            "summary": "",
        }]
        _check_global_goal_completion("agent_hunter", hunter, state, 10)
        assert hunter.get("global_goal_achieved"), "Goal should be achieved after kill"
        goal_obs = [
            m for m in hunter["memory"]
            if m.get("effects", {}).get("action_kind") == "goal_achieved"
        ]
        assert goal_obs, "Should write goal_achieved observation"
        assert goal_obs[0]["effects"]["goal"] == "kill_stalker"

    def test_validate_debug_spawn_stalker_kill_stalker(self):
        """Validation rejects kill_stalker without kill_target_id."""
        from app.games.zone_stalkers.rules.world_rules import _validate_debug_spawn_stalker
        state = self._minimal_state()
        state["agents"]["agent_target"] = self._make_target("B")
        # Missing kill_target_id
        result = _validate_debug_spawn_stalker(
            {"loc_id": "A", "global_goal": "kill_stalker"}, state
        )
        assert not result.valid
        assert "kill_target_id" in result.error.lower()
        # Non-existent kill_target_id
        result2 = _validate_debug_spawn_stalker(
            {"loc_id": "A", "global_goal": "kill_stalker", "kill_target_id": "agent_ghost"}, state
        )
        assert not result2.valid
        assert "not found" in result2.error.lower()
        # Valid
        result3 = _validate_debug_spawn_stalker(
            {"loc_id": "A", "global_goal": "kill_stalker", "kill_target_id": "agent_target"}, state
        )
        assert result3.valid


class TestDepartedStalkerNotObserved:
    """Regression test: has_left_zone agents must NOT appear in location observations."""

    def _make_agent(self, agent_id: str, loc_id: str, has_left: bool = False) -> dict:
        return {
            "id": agent_id,
            "archetype": "stalker_agent",
            "name": f"Сталкер_{agent_id}",
            "location_id": loc_id,
            "hp": 100, "max_hp": 100, "radiation": 0,
            "hunger": 10, "thirst": 10, "sleepiness": 10,
            "money": 500,
            "inventory": [],
            "equipment": {"weapon": None, "armor": None, "detector": None},
            "faction": "loner",
            "controller": {"kind": "bot"},
            "is_alive": True,
            "action_used": False,
            "has_left_zone": has_left,
            "memory": [],
            "global_goal": "get_rich",
            "current_goal": None,
            "risk_tolerance": 0.5,
            "material_threshold": 5000,
            "wealth_goal_target": 100000,
            "global_goal_achieved": False,
            "scheduled_action": None,
            "action_queue": [],
        }

    def _state(self) -> dict:
        return {
            "locations": {
                "A": {
                    "name": "Локация А", "terrain_type": "plain",
                    "anomaly_activity": 0, "items": [], "agents": [],
                    "connections": [], "exit_zone": False,
                },
            },
            "agents": {},
            "mutants": {},
            "traders": {},
            "world_turn": 5,
            "emission_active": False,
            "emission_ends_turn": 0,
        }

    def test_departed_stalker_invisible_to_observers(self):
        """Agent that has_left_zone=True must not appear in another agent's observations."""
        from app.games.zone_stalkers.rules.tick_rules import _write_location_observations
        state = self._state()
        observer = self._make_agent("observer", "A")
        departed = self._make_agent("departed", "A", has_left=True)
        state["agents"]["observer"] = observer
        state["agents"]["departed"] = departed

        _write_location_observations("observer", observer, "A", state, 5)

        stalker_obs = [
            m for m in observer["memory"]
            if m.get("effects", {}).get("observed") == "stalkers"
        ]
        assert not stalker_obs, (
            "Departed stalker should NOT appear in location observations, "
            f"but got: {stalker_obs}"
        )

    def test_alive_stalker_still_visible(self):
        """A normal alive (non-departed) stalker still shows up in observations."""
        from app.games.zone_stalkers.rules.tick_rules import _write_location_observations
        state = self._state()
        observer = self._make_agent("observer", "A")
        present = self._make_agent("present", "A", has_left=False)
        state["agents"]["observer"] = observer
        state["agents"]["present"] = present

        _write_location_observations("observer", observer, "A", state, 5)

        stalker_obs = [
            m for m in observer["memory"]
            if m.get("effects", {}).get("observed") == "stalkers"
        ]
        assert stalker_obs, "A normal alive stalker should appear in observations"
        assert "Сталкер_present" in stalker_obs[0]["effects"]["names"]


class TestDijkstraReachableLocations:
    """Tests for the new _dijkstra_reachable_locations helper."""

    def _locs(self) -> dict:
        # A --(12 min)--> B --(30 min)--> C
        # (all open)
        return {
            "A": {"connections": [{"to": "B", "travel_time": 12, "closed": False}]},
            "B": {"connections": [{"to": "A", "travel_time": 12, "closed": False},
                                   {"to": "C", "travel_time": 30, "closed": False}]},
            "C": {"connections": [{"to": "B", "travel_time": 30, "closed": False}]},
        }

    def test_reaches_within_radius(self):
        from app.games.zone_stalkers.rules.tick_rules import _dijkstra_reachable_locations
        result = _dijkstra_reachable_locations("A", self._locs(), max_minutes=60)
        assert "B" in result
        assert result["B"] == pytest.approx(12.0)
        assert "C" in result
        assert result["C"] == pytest.approx(42.0)

    def test_does_not_exceed_max_minutes(self):
        from app.games.zone_stalkers.rules.tick_rules import _dijkstra_reachable_locations
        result = _dijkstra_reachable_locations("A", self._locs(), max_minutes=20)
        assert "B" in result
        assert "C" not in result  # 12+30=42 > 20

    def test_skips_closed_connections(self):
        from app.games.zone_stalkers.rules.tick_rules import _dijkstra_reachable_locations
        locs = self._locs()
        locs["A"]["connections"][0]["closed"] = True
        result = _dijkstra_reachable_locations("A", locs, max_minutes=60)
        assert not result  # A's only connection is closed

    def test_anomaly_search_uses_travel_minutes(self):
        """_bot_pursue_goal get_rich branch stores travel_minutes not distance_hops."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        # A (no anomaly) --(12 min)--> B (anomaly=5)
        state = {
            "locations": {
                "A": {
                    "name": "A", "terrain_type": "plain",
                    "anomaly_activity": 0, "items": [], "agents": [],
                    "connections": [{"to": "B", "travel_time": 12, "closed": False}],
                    "exit_zone": False,
                },
                "B": {
                    "name": "B", "terrain_type": "plain",
                    "anomaly_activity": 5, "items": [], "agents": [],
                    "connections": [{"to": "A", "travel_time": 12, "closed": False}],
                    "exit_zone": False,
                },
            },
            "agents": {},
            "mutants": {},
            "traders": {},
            "world_turn": 1,
            "emission_active": False,
            "emission_ends_turn": 0,
        }
        agent = {
            "id": "agent_a",
            "archetype": "stalker_agent",
            "name": "Тестер",
            "location_id": "A",
            "hp": 100, "max_hp": 100, "radiation": 0,
            "hunger": 10, "thirst": 10, "sleepiness": 10,
            "money": 5000,
            "inventory": [],
            "equipment": {"weapon": None, "armor": None, "detector": None},
            "faction": "loner",
            "controller": {"kind": "bot"},
            "is_alive": True,
            "action_used": False,
            "has_left_zone": False,
            "memory": [],
            "global_goal": "get_rich",
            "current_goal": None,
            "risk_tolerance": 0.5,
            "material_threshold": 1,  # already over threshold
            "wealth_goal_target": 100000,
            "global_goal_achieved": False,
            "scheduled_action": None,
            "action_queue": [],
            "skill_stalker": 1,
            "skill_combat": 1, "skill_trade": 1, "skill_medicine": 1,
            "skill_social": 1, "skill_survival": 1,
            "experience": 0, "reputation": 0,
        }
        state["agents"]["agent_a"] = agent
        _bot_pursue_goal("agent_a", agent, "get_rich",
                         "A", state["locations"]["A"], state, 1, random.Random(0))
        decisions = [m for m in agent["memory"] if m.get("type") == "decision"]
        anomaly_decisions = [
            d for d in decisions
            if d.get("effects", {}).get("action_kind") == "move_for_anomaly"
        ]
        assert anomaly_decisions, "Should have a move_for_anomaly decision"
        fx = anomaly_decisions[0]["effects"]
        assert "travel_minutes" in fx, (
            "Decision should contain travel_minutes, not distance_hops"
        )
        assert "distance_hops" not in fx, (
            "Old distance_hops key should no longer appear"
        )
        assert fx["travel_minutes"] == 12, (
            f"Travel to B (12 min edge) should be 12 minutes, got {fx['travel_minutes']}"
        )


class TestCombatInteraction:
    """Tests for the Combat Interaction (Boevoe vzaimodeistvie) mechanic."""

    def _make_minimal_state(self, hunter_loc="B", target_loc="B"):
        """Create a minimal two-location state with a hunter and target."""
        return {
            "locations": {
                "A": {
                    "name": "Базовый лагер",
                    "terrain_type": "field_camp",
                    "anomaly_activity": 0,
                    "items": [], "agents": [],
                    "connections": [{"to": "B", "type": "road", "travel_time": 30, "closed": False}],
                    "exit_zone": False,
                },
                "B": {
                    "name": "Промзона",
                    "terrain_type": "industrial",
                    "anomaly_activity": 0,
                    "items": [], "agents": [],
                    "connections": [{"to": "A", "type": "road", "travel_time": 30, "closed": False}],
                    "exit_zone": False,
                },
            },
            "agents": {},
            "mutants": {},
            "traders": {},
            "world_turn": 90,
            "emission_active": False,
            "emission_ends_turn": 0,
            "combat_interactions": {},
        }

    def _make_hunter(self, loc_id, target_id):
        return {
            "id": "agent_hunter",
            "archetype": "stalker_agent",
            "name": "Охотник",
            "location_id": loc_id,
            "hp": 100, "max_hp": 100, "radiation": 0,
            "hunger": 10, "thirst": 10, "sleepiness": 10,
            "money": 3000,
            "inventory": [{"id": "ammo1", "type": "9x18", "name": "Патроны", "value": 60}],
            "equipment": {
                "weapon": {"id": "w1", "type": "pistol", "name": "Пистолет", "damage": 15, "accuracy": 0.55},
                "armor": None,
                "detector": None,
            },
            "faction": "loner",
            "controller": {"kind": "bot"},
            "is_alive": True,
            "action_used": False,
            "has_left_zone": False,
            "global_goal": "kill_stalker",
            "kill_target_id": target_id,
            "current_goal": None,
            "risk_tolerance": 0.9,
            "material_threshold": 3000,
            "wealth_goal_target": 100000,
            "global_goal_achieved": False,
            "scheduled_action": None,
            "action_queue": [],
            "memory": [],
            "skill_stalker": 1, "skill_combat": 1, "skill_trade": 1,
            "skill_medicine": 1, "skill_social": 1, "skill_survival": 1,
            "experience": 0, "reputation": 0,
        }

    def _make_target(self, loc_id):
        return {
            "id": "agent_target",
            "archetype": "stalker_agent",
            "name": "Цель",
            "location_id": loc_id,
            "hp": 80, "max_hp": 100, "radiation": 0,
            "hunger": 10, "thirst": 10, "sleepiness": 10,
            "money": 500,
            "inventory": [],
            "equipment": {"weapon": None, "armor": None, "detector": None},
            "faction": "bandit",
            "controller": {"kind": "bot"},
            "is_alive": True,
            "action_used": False,
            "has_left_zone": False,
            "global_goal": "get_rich",
            "kill_target_id": None,
            "current_goal": None,
            "risk_tolerance": 0.3,
            "material_threshold": 3000,
            "wealth_goal_target": 100000,
            "global_goal_achieved": False,
            "scheduled_action": None,
            "action_queue": [],
            "memory": [],
            "skill_stalker": 1, "skill_combat": 1, "skill_trade": 1,
            "skill_medicine": 1, "skill_social": 1, "skill_survival": 1,
            "experience": 0, "reputation": 0,
        }

    def test_combat_initiated_by_kill_stalker(self):
        """kill_stalker agent at same location as target creates combat interaction."""
        import random
        from app.games.zone_stalkers.rules.tick_rules import _bot_pursue_goal
        state = self._make_minimal_state()
        hunter = self._make_hunter("B", "agent_target")
        target = self._make_target("B")
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        events = _bot_pursue_goal(
            "agent_hunter", hunter, "kill_stalker",
            "B", state["locations"]["B"], state, 90, random.Random(0)
        )
        # Combat should be created
        assert len(state["combat_interactions"]) == 1, "Combat interaction should be created"
        cid = list(state["combat_interactions"].keys())[0]
        ci = state["combat_interactions"][cid]
        assert ci["location_id"] == "B"
        assert "agent_hunter" in ci["participants"]
        assert "agent_target" in ci["participants"]
        assert ci["participants"]["agent_hunter"]["motive"] == "победить"
        assert ci["participants"]["agent_target"]["motive"] == "выжить"
        # Hunter memory should have combat_initiated decision
        combat_mem = [
            m for m in hunter["memory"]
            if m.get("effects", {}).get("action_kind") == "combat_initiated"
        ]
        assert combat_mem, "Hunter should have combat_initiated in memory"
        assert combat_mem[0]["effects"]["target_id"] == "agent_target"
        # Event should be emitted
        assert any(e["event_type"] == "combat_initiated" for e in events)
        assert hunter["action_used"]

    def test_combat_participant_skips_normal_decisions(self):
        """Participant in active combat doesn't take normal bot decisions."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_minimal_state()
        hunter = self._make_hunter("B", "agent_target")
        target = self._make_target("B")
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        # Manually create a combat where hunter is non-fled participant
        state["combat_interactions"]["combat_B_90"] = {
            "id": "combat_B_90",
            "location_id": "B",
            "started_turn": 90,
            "ended": False,
            "ended_turn": None,
            "participants": {
                "agent_hunter": {
                    "motive": "победить",
                    "enemies": ["agent_target"],
                    "friends": [],
                    "fled": False,
                    "fled_to": None,
                },
                "agent_target": {
                    "motive": "выжить",
                    "enemies": ["agent_hunter"],
                    "friends": [],
                    "fled": False,
                    "fled_to": None,
                },
            },
        }
        new_state, events = tick_zone_map(state)
        # Normal bot decisions (wander, seek_anomaly etc.) should NOT be written for hunter
        hunter_after = new_state["agents"]["agent_hunter"]
        normal_decisions = [
            m for m in hunter_after.get("memory", [])
            if m.get("type") == "decision"
            and m.get("effects", {}).get("action_kind") in (
                "wander", "move_for_anomaly", "explore_decision",
                "buy_item", "seek_item", "hunt_search"
            )
        ]
        assert not normal_decisions, (
            f"Combat participant should not take normal decisions; got: {normal_decisions}"
        )

    def test_combat_flee_returns_to_previous_location(self):
        """Fled agent gets scheduled travel back to previous location at half travel time."""
        from app.games.zone_stalkers.rules.tick_rules import _combat_flee
        state = self._make_minimal_state()
        hunter = self._make_hunter("B", "agent_target")
        # Add travel history: hunter came from A
        hunter["memory"] = [{
            "world_turn": 88,
            "type": "action",
            "title": "Прибыл в B",
            "effects": {"action_kind": "travel_arrived", "to_loc": "B"},
            "summary": "Прибыл",
        }, {
            "world_turn": 80,
            "type": "action",
            "title": "Прибыл в A",
            "effects": {"action_kind": "travel_arrived", "to_loc": "A"},
            "summary": "Прибыл",
        }]
        state["agents"]["agent_hunter"] = hunter
        combat = {
            "id": "combat_B_90",
            "location_id": "B",
            "started_turn": 90,
            "ended": False,
            "ended_turn": None,
            "participants": {
                "agent_hunter": {
                    "motive": "победить",
                    "enemies": ["agent_target"],
                    "friends": [], "fled": False, "fled_to": None,
                }
            }
        }
        participant = combat["participants"]["agent_hunter"]
        events = _combat_flee("agent_hunter", hunter, participant, combat, state, 90)
        assert participant["fled"] is True
        # Should schedule travel to A (previous location)
        sched = hunter.get("scheduled_action")
        assert sched is not None, "Should have scheduled travel"
        assert sched["type"] == "travel"
        assert sched["target_id"] == "A"
        # Travel time should be half of 30 = 15
        assert sched["turns_remaining"] == 15, (
            f"Flee travel time should be 15 (half of 30), got {sched['turns_remaining']}"
        )
        # Memory should have combat_flee decision
        flee_mem = [
            m for m in hunter["memory"]
            if m.get("effects", {}).get("action_kind") == "combat_flee"
        ]
        assert flee_mem, "Should have combat_flee in memory"
        # Event should be emitted
        assert any(e["event_type"] == "combat_fled" for e in events)

    def test_combat_ends_when_no_enemies(self):
        """Combat ends when no participant has any living non-fled enemy at location."""
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        state = self._make_minimal_state()
        hunter = self._make_hunter("B", "agent_target")
        target = self._make_target("B")
        # Give target very low HP so it may die
        target["hp"] = 1
        # Give hunter a weapon with 100% accuracy so it definitely hits
        hunter["equipment"]["weapon"] = {
            "id": "w1", "type": "pistol", "name": "Пистолет",
            "damage": 50, "accuracy": 1.0
        }
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        state["combat_interactions"]["combat_B_90"] = {
            "id": "combat_B_90",
            "location_id": "B",
            "started_turn": 90,
            "ended": False,
            "ended_turn": None,
            "participants": {
                "agent_hunter": {
                    "motive": "победить",
                    "enemies": ["agent_target"],
                    "friends": [], "fled": False, "fled_to": None,
                },
                "agent_target": {
                    "motive": "выжить",
                    "enemies": ["agent_hunter"],
                    "friends": [], "fled": False, "fled_to": None,
                },
            }
        }
        new_state, events = tick_zone_map(state)
        # Either target is dead OR fled, combat should end
        ci = new_state["combat_interactions"]["combat_B_90"]
        # The combat should eventually end (may take a tick if hunter misses)
        # With accuracy=1.0 and damage=50 vs hp=1, target is dead after first shot
        target_after = new_state["agents"]["agent_target"]
        assert not target_after.get("is_alive", True) or ci.get("ended", False), (
            "Target should be dead or combat should have ended"
        )
        if ci.get("ended", False):
            assert ci["ended_turn"] is not None
            assert any(e["event_type"] == "combat_ended" for e in events)

    def test_combat_shoot_kills_enemy(self):
        """Shoot action with high accuracy kills enemy and writes proper memory."""
        from app.games.zone_stalkers.rules.tick_rules import _combat_shoot
        state = self._make_minimal_state()
        hunter = self._make_hunter("B", "agent_target")
        target = self._make_target("B")
        target["hp"] = 10  # low HP so guaranteed to die
        state["agents"]["agent_hunter"] = hunter
        state["agents"]["agent_target"] = target
        import random
        # Use accuracy=1.0 via weapon override
        hunter["equipment"]["weapon"] = {
            "id": "w1", "type": "pistol", "name": "Пистолет",
            "damage": 50, "accuracy": 1.0
        }
        combat = {
            "id": "combat_B_90",
            "location_id": "B",
            "started_turn": 90,
            "ended": False,
            "ended_turn": None,
            "participants": {
                "agent_hunter": {
                    "motive": "победить",
                    "enemies": ["agent_target"],
                    "friends": [], "fled": False, "fled_to": None,
                },
                "agent_target": {
                    "motive": "выжить",
                    "enemies": ["agent_hunter"],
                    "friends": [], "fled": False, "fled_to": None,
                },
            }
        }
        participant = combat["participants"]["agent_hunter"]
        rng = random.Random(42)
        events = _combat_shoot("agent_hunter", hunter, participant, combat, state, 90, rng)
        # Target should be dead (hp=10 - 50 = -40, capped to 0)
        assert target["hp"] == 0
        assert not target.get("is_alive", True), "Target should be dead"
        # Hunter memory: combat_shoot decision + combat_kill observation
        shoot_mem = [
            m for m in hunter["memory"]
            if m.get("effects", {}).get("action_kind") == "combat_shoot"
        ]
        assert shoot_mem, "Should have combat_shoot in memory"
        assert shoot_mem[0]["effects"]["hit"] is True
        assert shoot_mem[0]["effects"]["damage"] == 50
        kill_obs = [
            m for m in hunter["memory"]
            if m.get("effects", {}).get("observed") == "combat_kill"
        ]
        assert kill_obs, "Should have combat_kill observation"
        # Target memory: combat_killed observation
        killed_obs = [
            m for m in target["memory"]
            if m.get("effects", {}).get("observed") == "combat_killed"
        ]
        assert killed_obs, "Target should have combat_killed observation"
        # Events should include agent_died
        assert any(e["event_type"] == "agent_died" for e in events)
        assert any(
            e.get("payload", {}).get("cause") == "combat" for e in events
        )
