"""
Tests for debug_spawn_stalker backend hardening.
Verifies that spawned agents have all UI-required fields and that
location.agents stays consistent with state.agents.
"""
from __future__ import annotations

import pytest
from app.games.zone_stalkers.rules.world_rules import (
    validate_world_command,
    resolve_world_command,
)


def _make_minimal_state(loc_id: str = "loc_A") -> dict:
    """Return a minimal but valid zone_map state with one location."""
    return {
        "context_type": "zone_map",
        "world_turn": 1,
        "world_day": 1,
        "world_hour": 6,
        "world_minute": 0,
        "emission_active": False,
        "emission_scheduled_turn": 9999,
        "emission_ends_turn": 0,
        "locations": {
            loc_id: {
                "id": loc_id,
                "name": "Test Location",
                "terrain_type": "plain",
                "anomaly_activity": 0,
                "dominant_anomaly_type": None,
                "connections": [],
                "artifacts": [],
                "items": [],
                "agents": [],
            }
        },
        "agents": {},
        "mutants": {},
        "traders": {},
        "player_agents": {},
        "active_events": [],
        "game_over": False,
    }


class TestDebugSpawnStalkerValidation:
    def test_spawn_stalker_valid(self):
        state = _make_minimal_state()
        result = validate_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A", "name": "Test Stalker"},
            state,
            player_id="debug",
        )
        assert result.valid

    def test_spawn_stalker_missing_loc(self):
        state = _make_minimal_state()
        result = validate_world_command(
            "debug_spawn_stalker",
            {"loc_id": "non_existent"},
            state,
            player_id="debug",
        )
        assert not result.valid


class TestDebugSpawnStalkerResolve:
    def test_spawned_agent_id_in_state_agents(self):
        state = _make_minimal_state()
        new_state, events = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A", "name": "Test Stalker"},
            state,
            player_id="debug",
        )
        assert events, "Should emit at least one event"
        ev = events[0]
        assert ev["event_type"] == "debug_stalker_spawned"
        agent_id = ev["payload"]["agent_id"]
        assert agent_id in new_state["agents"], "Agent id must be in state.agents"

    def test_spawned_agent_id_in_location_agents(self):
        state = _make_minimal_state()
        new_state, events = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A", "name": "Test Stalker"},
            state,
            player_id="debug",
        )
        agent_id = events[0]["payload"]["agent_id"]
        loc = new_state["locations"]["loc_A"]
        assert agent_id in loc["agents"], "Agent id must be in location.agents"

    def test_spawned_agent_location_id_matches(self):
        state = _make_minimal_state()
        new_state, events = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A"},
            state,
            player_id="debug",
        )
        agent_id = events[0]["payload"]["agent_id"]
        agent = new_state["agents"][agent_id]
        assert agent["location_id"] == "loc_A"

    def test_spawned_agent_has_all_ui_required_fields(self):
        state = _make_minimal_state()
        new_state, events = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A", "name": "UI Test Stalker"},
            state,
            player_id="debug",
        )
        agent_id = events[0]["payload"]["agent_id"]
        agent = new_state["agents"][agent_id]

        assert agent.get("id") == agent_id, "agent.id must equal the agent_id"
        assert agent.get("name"), "agent.name must be set"
        assert agent.get("location_id") == "loc_A"
        assert "hp" in agent, "hp must be present"
        assert "max_hp" in agent, "max_hp must be present"
        assert "is_alive" in agent, "is_alive must be present"
        assert "controller" in agent, "controller must be present"
        assert "inventory" in agent, "inventory must be present"
        assert "equipment" in agent, "equipment must be present"
        assert "scheduled_action" in agent, "scheduled_action must be present"
        assert "action_queue" in agent, "action_queue must be present"

    def test_no_duplicate_agent_id_in_location(self):
        """Spawning the same agent twice (e.g. duplicate call) should not add id twice."""
        state = _make_minimal_state()
        new_state, events1 = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A", "name": "Stalker One"},
            state,
            player_id="debug",
        )
        agent1_id = events1[0]["payload"]["agent_id"]

        # Verify no duplicates after first spawn
        loc_agents = new_state["locations"]["loc_A"]["agents"]
        assert loc_agents.count(agent1_id) == 1, "agent1_id must appear exactly once"

        # Spawn a second agent in the same location
        new_state2, events2 = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A", "name": "Stalker Two"},
            new_state,
            player_id="debug",
        )
        agent2_id = events2[0]["payload"]["agent_id"]
        loc_agents2 = new_state2["locations"]["loc_A"]["agents"]
        assert agent1_id in loc_agents2, "agent1_id should still be in loc after second spawn"
        assert agent2_id in loc_agents2, "agent2_id should be added"
        assert loc_agents2.count(agent1_id) == 1, "agent1_id must not be duplicated"
        assert loc_agents2.count(agent2_id) == 1, "agent2_id must not be duplicated"

    def test_spawn_without_agents_key_in_state(self):
        """State may arrive without agents key — should not crash."""
        state = _make_minimal_state()
        del state["agents"]
        new_state, events = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A", "name": "Ghost"},
            state,
            player_id="debug",
        )
        assert events[0]["event_type"] == "debug_stalker_spawned"
        agent_id = events[0]["payload"]["agent_id"]
        assert agent_id in new_state["agents"]

    def test_spawn_without_loc_agents_key(self):
        """Location may arrive without agents list — should not crash."""
        state = _make_minimal_state()
        del state["locations"]["loc_A"]["agents"]
        new_state, events = resolve_world_command(
            "debug_spawn_stalker",
            {"loc_id": "loc_A"},
            state,
            player_id="debug",
        )
        assert events[0]["event_type"] == "debug_stalker_spawned"
        agent_id = events[0]["payload"]["agent_id"]
        assert agent_id in new_state["locations"]["loc_A"]["agents"]

    def test_spawn_multiple_stalkers_sequential(self):
        """Spawn several stalkers in sequence; all should land in loc.agents."""
        state = _make_minimal_state()
        spawned_ids = []
        current_state = state
        for i in range(5):
            current_state, evs = resolve_world_command(
                "debug_spawn_stalker",
                {"loc_id": "loc_A", "name": f"Сталкер #{i}"},
                current_state,
                player_id="debug",
            )
            spawned_ids.append(evs[0]["payload"]["agent_id"])

        loc_agents = current_state["locations"]["loc_A"]["agents"]
        for aid in spawned_ids:
            assert aid in loc_agents, f"{aid} should be in loc.agents"
            assert aid in current_state["agents"], f"{aid} should be in state.agents"
        # No duplicates
        assert len(set(loc_agents)) == len(loc_agents)
        assert len(set(spawned_ids)) == 5, "All 5 ids should be unique"
