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
        """zone_generator should always generate 'get_rich' as the only global goal."""
        from app.games.zone_stalkers.generators.zone_generator import generate_zone
        goals_found = set()
        for seed in range(50):
            state = generate_zone(seed=seed, num_players=0, num_ai_stalkers=5, num_mutants=0, num_traders=0)
            for ag in state["agents"].values():
                goals_found.add(ag.get("global_goal"))
        assert goals_found == {"get_rich"}, (
            f"Generator should only create agents with goal 'get_rich'; found goals: {goals_found}"
        )

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
        """When the weapon IS on the ground and is picked up as the last item,
        a pickup-induced item_not_found_here observation IS written (prevents re-visit).
        The arrival-based observation from _maybe_record_item_not_found is NOT written
        because _bot_pickup_item_from_ground returns early on success, never reaching
        the _maybe_record_item_not_found call."""
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
        # The new behavior: pickup of the last item writes item_not_found_here so
        # the agent won't plan another trip here for the same category.
        assert len(not_found) == 1, (
            f"Expected 1 item_not_found_here written by pickup; got {len(not_found)}"
        )
        # Verify it is the pickup-kind, not the arrival-kind.
        assert not_found[0]["effects"].get("source") == "pickup", (
            f"Expected source='pickup'; got {not_found[0]['effects'].get('source')!r}"
        )

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
