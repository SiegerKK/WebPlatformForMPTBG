"""PR7 Part 3 — Corpse lifecycle validation helpers.

Tests for:
- is_valid_corpse_object
- cleanup_stale_corpses
"""
from __future__ import annotations

from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_dead_agent(agent_id: str = "dead_1") -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": agent_id,
        "is_alive": False,
        "hp": 0,
        "location_id": "loc_a",
        "inventory": [],
        "equipment": {},
        "money": 0,
        "faction": "loner",
    }


def _make_alive_agent(agent_id: str = "alive_1") -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": agent_id,
        "is_alive": True,
        "hp": 100,
        "max_hp": 100,
        "location_id": "loc_a",
        "inventory": [],
        "equipment": {},
        "money": 100,
        "faction": "loner",
    }


def _make_corpse(agent_id: str = "dead_1", *, use_old_field: bool = False) -> dict[str, Any]:
    """Create a corpse dict. If use_old_field=True, use dead_agent_id instead of agent_id."""
    if use_old_field:
        return {"dead_agent_id": agent_id, "decay_turn": 9999, "items": []}
    return {"agent_id": agent_id, "decay_turn": 9999, "items": []}


def _make_state(
    agents: dict[str, dict],
    locations: dict[str, dict] | None = None,
) -> dict[str, Any]:
    return {
        "world_turn": 10,
        "agents": agents,
        "locations": locations or {
            "loc_a": {
                "id": "loc_a",
                "name": "Test",
                "corpses": [],
                "agents": [],
            }
        },
    }


# ── is_valid_corpse_object tests ──────────────────────────────────────────────

class TestIsValidCorpseObject:
    from app.games.zone_stalkers.rules.agent_lifecycle import is_valid_corpse_object  # type: ignore

    def _call(self, corpse: dict, state: dict) -> bool:
        from app.games.zone_stalkers.rules.agent_lifecycle import is_valid_corpse_object
        return is_valid_corpse_object(corpse, state)

    def test_valid_corpse_of_confirmed_dead_agent(self) -> None:
        dead = _make_dead_agent("dead_1")
        state = _make_state({"dead_1": dead})
        corpse = _make_corpse("dead_1")
        assert self._call(corpse, state) is True

    def test_valid_corpse_using_dead_agent_id_field(self) -> None:
        """Backwards-compat: corpse may have dead_agent_id instead of agent_id."""
        dead = _make_dead_agent("dead_2")
        state = _make_state({"dead_2": dead})
        corpse = _make_corpse("dead_2", use_old_field=True)
        assert self._call(corpse, state) is True

    def test_invalid_corpse_when_agent_is_alive(self) -> None:
        """A corpse pointing to a living agent is stale / invalid."""
        alive = _make_alive_agent("alive_1")
        state = _make_state({"alive_1": alive})
        corpse = _make_corpse("alive_1")
        assert self._call(corpse, state) is False

    def test_valid_corpse_when_agent_absent_from_state(self) -> None:
        """Agent not in state at all → treated as dead/removed → valid corpse."""
        state = _make_state({})   # no agents
        corpse = _make_corpse("ghost_agent")
        assert self._call(corpse, state) is True

    def test_invalid_corpse_missing_agent_id(self) -> None:
        """Corpse with no agent_id / dead_agent_id is generic and therefore valid."""
        state = _make_state({})
        corpse: dict[str, Any] = {"decay_turn": 9999, "items": []}  # no id field
        assert self._call(corpse, state) is True

    def test_invalid_corpse_empty_agent_id_string(self) -> None:
        state = _make_state({})
        corpse = {"agent_id": "", "decay_turn": 9999}
        assert self._call(corpse, state) is True

    def test_valid_corpse_is_alive_false_explicitly(self) -> None:
        """is_alive=False explicitly marks agent as dead."""
        dead = _make_dead_agent("dead_3")
        dead["is_alive"] = False
        state = _make_state({"dead_3": dead})
        corpse = _make_corpse("dead_3")
        assert self._call(corpse, state) is True

    def test_invalid_corpse_is_alive_true_explicitly(self) -> None:
        alive = _make_alive_agent("alive_2")
        alive["is_alive"] = True
        state = _make_state({"alive_2": alive})
        corpse = _make_corpse("alive_2")
        assert self._call(corpse, state) is False


# ── cleanup_stale_corpses tests ───────────────────────────────────────────────

class TestCleanupStaleCorpses:
    def _call(self, state: dict) -> dict[str, int]:
        from app.games.zone_stalkers.rules.agent_lifecycle import cleanup_stale_corpses
        return cleanup_stale_corpses(state)

    def test_no_corpses_returns_zero_removed(self) -> None:
        state = _make_state({})
        result = self._call(state)
        assert result["stale_corpses_removed"] == 0

    def test_valid_corpse_not_removed(self) -> None:
        dead = _make_dead_agent("dead_1")
        state = _make_state({"dead_1": dead})
        state["locations"]["loc_a"]["corpses"] = [_make_corpse("dead_1")]
        result = self._call(state)
        assert result["stale_corpses_removed"] == 0
        assert len(state["locations"]["loc_a"]["corpses"]) == 1

    def test_stale_corpse_removed_when_agent_alive(self) -> None:
        alive = _make_alive_agent("alive_1")
        state = _make_state({"alive_1": alive})
        state["locations"]["loc_a"]["corpses"] = [_make_corpse("alive_1")]
        result = self._call(state)
        assert result["stale_corpses_removed"] == 1
        assert state["locations"]["loc_a"]["corpses"] == []

    def test_generic_corpse_without_id_not_removed(self) -> None:
        state = _make_state({})
        state["locations"]["loc_a"]["corpses"] = [{"decay_turn": 9999}]  # no id
        result = self._call(state)
        assert result["stale_corpses_removed"] == 0
        assert len(state["locations"]["loc_a"]["corpses"]) == 1

    def test_mixed_corpses_only_stale_removed(self) -> None:
        dead = _make_dead_agent("dead_1")
        alive = _make_alive_agent("alive_1")
        state = _make_state({"dead_1": dead, "alive_1": alive})
        state["locations"]["loc_a"]["corpses"] = [
            _make_corpse("dead_1"),   # valid
            _make_corpse("alive_1"),  # stale
        ]
        result = self._call(state)
        assert result["stale_corpses_removed"] == 1
        remaining = state["locations"]["loc_a"]["corpses"]
        assert len(remaining) == 1
        assert remaining[0].get("agent_id") == "dead_1"

    def test_multiple_locations_each_cleaned(self) -> None:
        alive = _make_alive_agent("alive_1")
        state = _make_state({"alive_1": alive})
        state["locations"]["loc_b"] = {
            "id": "loc_b", "name": "B", "corpses": [_make_corpse("alive_1")], "agents": [],
        }
        state["locations"]["loc_a"]["corpses"] = [_make_corpse("alive_1")]
        result = self._call(state)
        assert result["stale_corpses_removed"] == 2
        assert state["locations"]["loc_a"]["corpses"] == []
        assert state["locations"]["loc_b"]["corpses"] == []

    def test_stale_corpses_ignored_is_zero(self) -> None:
        state = _make_state({})
        result = self._call(state)
        assert result["stale_corpses_ignored"] == 0

    def test_corpse_using_dead_agent_id_field_valid(self) -> None:
        dead = _make_dead_agent("dead_4")
        state = _make_state({"dead_4": dead})
        state["locations"]["loc_a"]["corpses"] = [_make_corpse("dead_4", use_old_field=True)]
        result = self._call(state)
        assert result["stale_corpses_removed"] == 0


class TestCorpseLifecycleIntegration:
    def _v3_action_records(self, agent: dict[str, Any], action_kind: str) -> list[dict[str, Any]]:
        records = ((agent.get("memory_v3") or {}).get("records") or {}).values()
        return [
            r for r in records
            if isinstance(r, dict)
            and str((r.get("details") or {}).get("action_kind") or r.get("kind") or "") == action_kind
        ]

    def test_alive_agent_stale_corpse_does_not_emit_corpse_seen(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _write_location_observations
        from tests.decision.conftest import make_agent, make_minimal_state

        observer = make_agent(agent_id="observer", location_id="loc_a")
        alive_victim = make_agent(agent_id="victim", location_id="loc_a")
        state = make_minimal_state(agent_id="observer", agent=observer)
        state["agents"]["victim"] = alive_victim
        state["locations"]["loc_a"]["agents"].append("victim")
        state["locations"]["loc_a"]["corpses"] = [
            {"corpse_id": "corpse_victim", "agent_id": "victim", "visible": True, "location_id": "loc_a"}
        ]

        _write_location_observations("observer", observer, "loc_a", state, world_turn=101)
        assert self._v3_action_records(observer, "corpse_seen") == []

    def test_alive_agent_stale_corpse_does_not_mark_known_npc_dead(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _write_location_observations
        from tests.decision.conftest import make_agent, make_minimal_state

        observer = make_agent(agent_id="observer", location_id="loc_a")
        alive_victim = make_agent(agent_id="victim", location_id="loc_a")
        state = make_minimal_state(agent_id="observer", agent=observer)
        state["agents"]["victim"] = alive_victim
        state["locations"]["loc_a"]["agents"].append("victim")
        state["locations"]["loc_a"]["corpses"] = [
            {"corpse_id": "corpse_victim", "agent_id": "victim", "visible": True, "location_id": "loc_a"}
        ]

        _write_location_observations("observer", observer, "loc_a", state, world_turn=101)
        known = (observer.get("knowledge_v1") or {}).get("known_npcs", {})
        victim_entry = known.get("victim")
        assert not victim_entry or victim_entry.get("is_alive") is not False

    def test_cleanup_runs_once_per_tick(self, monkeypatch) -> None:
        from app.games.zone_stalkers.rules import tick_rules
        from tests.decision.conftest import make_agent, make_minimal_state

        observer = make_agent(agent_id="observer", location_id="loc_a")
        state = make_minimal_state(agent_id="observer", agent=observer)
        calls = {"count": 0}
        original = tick_rules.cleanup_stale_corpses

        def _wrapped_cleanup(local_state: dict[str, Any]) -> dict[str, int]:
            calls["count"] += 1
            return original(local_state)

        monkeypatch.setattr(tick_rules, "cleanup_stale_corpses", _wrapped_cleanup)
        tick_rules.tick_zone_map(state)
        assert calls["count"] == 1

    def test_valid_dead_agent_corpse_seen_still_works(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _write_location_observations
        from tests.decision.conftest import make_agent, make_minimal_state

        observer = make_agent(agent_id="observer", location_id="loc_a")
        dead_victim = make_agent(agent_id="victim", location_id="loc_a")
        dead_victim["is_alive"] = False
        dead_victim["hp"] = 0
        state = make_minimal_state(agent_id="observer", agent=observer)
        state["agents"]["victim"] = dead_victim
        state["locations"]["loc_a"]["corpses"] = [
            {"corpse_id": "corpse_victim", "agent_id": "victim", "visible": True, "location_id": "loc_a"}
        ]

        _write_location_observations("observer", observer, "loc_a", state, world_turn=101)
        corpse_seen = self._v3_action_records(observer, "corpse_seen")
        assert corpse_seen, "Expected corpse_seen memory for valid corpse"
