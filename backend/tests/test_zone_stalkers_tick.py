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
        assert any(m["type"] == "travel" for m in memory)

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
        assert any(m["type"] == "explore" for m in memory)

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
        assert any(m["type"] == "sleep" for m in state["agents"]["agent_p0"]["memory"])


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
