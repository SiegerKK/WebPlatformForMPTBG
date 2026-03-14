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
        assert agent["scheduled_action"]["type"] == "explore"
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
      1. Picks up the artifact and records it in memory
      2. Decides to travel to the trader's location (records decision in memory)
      3. After arriving, sells the artifact to the trader (both sides record it in memory)

    Note: the sell action fires in the SAME tick as travel completion (bot AI
    decision step runs in the same tick immediately after the scheduled action
    clears), so we use _run_until_sold() to advance through the whole cycle.
    """

    def _tick(self, state):
        from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
        return tick_zone_map(state)

    def _run_until_sold(self, state, sid):
        """Tick until the stalker has a trade_sell (action) entry in memory or 200 ticks."""
        for _ in range(200):
            agent = state["agents"][sid]
            if any(m["type"] == "action" and m["effects"].get("action_kind") == "trade_sell"
                   for m in agent.get("memory", [])):
                return state
            state, _ = self._tick(state)
        return state

    # ── Phase 1: Artifact pickup ──────────────────────────────────────────────

    def test_tick1_picks_up_artifact(self):
        state, sid, *_ = _make_trader_scenario()
        new_state, events = self._tick(state)
        agent = new_state["agents"][sid]
        assert any(i["id"] == "art_test_001" for i in agent["inventory"])
        assert any(e["event_type"] == "artifact_picked_up" for e in events)

    def test_tick1_pickup_recorded_in_memory(self):
        state, sid, *_ = _make_trader_scenario()
        new_state, _ = self._tick(state)
        agent = new_state["agents"][sid]
        pickup_mems = [m for m in agent["memory"]
                       if m["type"] == "action" and m["effects"].get("action_kind") == "pickup"]
        assert len(pickup_mems) >= 1
        mem = pickup_mems[0]
        assert mem["effects"]["artifact_type"] == "soul"
        assert mem["effects"]["artifact_value"] > 0

    # ── Phase 2: Travel decision toward trader ────────────────────────────────

    def test_tick2_decides_to_travel_to_trader(self):
        state, sid, _, stalker_loc, trader_loc, _ = _make_trader_scenario()
        state, _ = self._tick(state)  # tick 1: pick up artifact
        new_state, events = self._tick(state)  # tick 2: travel decision
        agent = new_state["agents"][sid]
        sched = agent.get("scheduled_action")
        assert sched is not None, "Agent should have a scheduled travel action"
        assert sched["type"] == "travel"
        # With hop-by-hop travel, target_id is the first hop, final_target_id is the destination
        assert sched.get("final_target_id") == trader_loc

    def test_tick2_travel_decision_recorded_in_memory(self):
        state, sid, *_ = _make_trader_scenario()
        state, _ = self._tick(state)  # tick 1: pickup
        new_state, _ = self._tick(state)  # tick 2: travel decision
        agent = new_state["agents"][sid]
        decision_mems = [m for m in agent["memory"] if m["type"] == "decision"]
        assert len(decision_mems) >= 1
        mem = decision_mems[0]
        assert mem["effects"]["artifacts_count"] >= 1

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

    def test_observation_written_when_artifact_present(self):
        """An agent whose location has an artifact writes an artifacts observation each tick."""
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        state["locations"][loc_id].setdefault("artifacts", []).append(
            {"id": "art_obs_001", "type": "fire", "value": 100}
        )
        new_state, _ = self._tick(state)
        obs = [m for m in new_state["agents"]["agent_p0"]["memory"]
               if m["type"] == "observation" and m["effects"].get("observed") == "artifacts"]
        assert len(obs) >= 1
        assert "fire" in obs[0]["effects"]["artifact_types"]

    def test_observation_deduplicated_on_second_tick(self):
        """Identical observations are NOT re-written on the next tick — deduplication works."""
        state = _make_world()
        agent = state["agents"]["agent_p0"]
        loc_id = agent["location_id"]
        state["locations"][loc_id].setdefault("artifacts", []).append(
            {"id": "art_dedup_001", "type": "fire", "value": 100}
        )
        state1, _ = self._tick(state)
        count_after_tick1 = sum(
            1 for m in state1["agents"]["agent_p0"]["memory"]
            if m["type"] == "observation" and m["effects"].get("observed") == "artifacts"
        )
        state2, _ = self._tick(state1)
        count_after_tick2 = sum(
            1 for m in state2["agents"]["agent_p0"]["memory"]
            if m["type"] == "observation" and m["effects"].get("observed") == "artifacts"
        )
        # Should NOT have added a duplicate entry on the second tick
        assert count_after_tick2 == count_after_tick1

    def test_new_observation_written_when_content_changes(self):
        """A new observation IS written when a second artifact appears (content changed)."""
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
        # Two distinct observation entries (different content each tick)
        assert len(obs) >= 2
        # The latest observation should contain both artifact types
        latest = obs[-1]
        assert "soul" in latest["effects"]["artifact_types"]

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
            "anomalies": [],
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
        "global_goal": "survive",
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
        """When B→C is blocked but B→D→C is open, agent re-routes silently."""
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

