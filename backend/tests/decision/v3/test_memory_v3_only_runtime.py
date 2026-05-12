"""PR5: Verify that legacy list-style memory is never written at runtime.

These integration-level tests run _add_memory (and full tick) and assert that:
1. Legacy list memory is never populated (field doesn't exist or is empty).
2. memory_v3 is the sole write target.
3. The _v3_records_desc / _v3_action_kind / _v3_memory_type helpers work correctly.
4. The old removed bridge module is not importable.
"""
from __future__ import annotations

import pytest

from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
from app.games.zone_stalkers.rules.tick_rules import (
    _add_memory,
    _v3_records_desc,
    _v3_action_kind,
    _v3_memory_type,
    _v3_details,
    _v3_turn,
)

LEGACY_MEMORY_KEY = "memory"


def _base_agent(agent_id: str = "bot1") -> dict:
    return {
        "id": agent_id,
        "name": "TestAgent",
        "archetype": "stalker_agent",
        "memory_v3": None,
    }


def _base_state(agent: dict, agent_id: str = "bot1") -> dict:
    return {"agents": {agent_id: agent}}


# ── Test 1: _add_memory never writes to legacy list memory ────────────────────

def test_add_memory_never_writes_legacy_memory_list() -> None:
    """After PR5, _add_memory must NOT write to agent['memory']."""
    agent = _base_agent()
    state = _base_state(agent)

    _add_memory(
        agent, 10, state, "action", "buy",
        {"action_kind": "trade_buy", "item_type": "bread", "trader_id": "t1"},
        summary="bought bread",
        agent_id="bot1",
    )

    assert agent.get(LEGACY_MEMORY_KEY) is None or agent.get(LEGACY_MEMORY_KEY) == []
    # memory_v3 must have the record
    recs = list(ensure_memory_v3(agent)["records"].values())
    assert any(r.get("kind") == "item_bought" for r in recs)


def test_multiple_add_memory_calls_never_populate_legacy_list() -> None:
    agent = _base_agent()
    state = _base_state(agent)

    for i in range(5):
        _add_memory(
            agent, i + 1, state, "observation", f"obs {i}",
            {"action_kind": "travel_arrived", "to_loc": f"loc_{i}"},
            agent_id="bot1",
        )

    assert agent.get(LEGACY_MEMORY_KEY) is None or agent.get(LEGACY_MEMORY_KEY) == []
    recs = ensure_memory_v3(agent)["records"]
    assert len(recs) == 5


# ── Test 2: v3 helper functions work correctly ────────────────────────────────

def test_v3_records_desc_returns_newest_first() -> None:
    agent = _base_agent()
    state = _base_state(agent)

    for turn in [1, 5, 3]:
        _add_memory(
            agent, turn, state, "action", f"action at {turn}",
            {"action_kind": "travel_arrived", "to_loc": "loc_x"},
            agent_id="bot1",
        )

    records = _v3_records_desc(agent)
    turns = [_v3_turn(r) for r in records]
    assert turns == sorted(turns, reverse=True), f"Expected desc order, got {turns}"


def test_v3_action_kind_returns_details_action_kind_when_remapped() -> None:
    """When kind is remapped (e.g. emission_imminent → emission_warning),
    _v3_action_kind must return the original action_kind from details."""
    agent = _base_agent()
    entry = {
        "world_turn": 10, "type": "observation", "title": "emission",
        "effects": {"action_kind": "emission_imminent"},
    }
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=10)

    recs = _v3_records_desc(agent)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.get("kind") == "emission_warning"       # v3 kind is remapped
    assert _v3_action_kind(rec) == "emission_imminent"  # helper returns original


def test_v3_memory_type_returns_correct_type() -> None:
    agent = _base_agent()
    state = _base_state(agent)

    _add_memory(
        agent, 10, state, "decision", "decide",
        {"action_kind": "seek_item", "item_category": "food"},
        agent_id="bot1",
    )
    _add_memory(
        agent, 10, state, "observation", "observe",
        {"action_kind": "travel_arrived", "to_loc": "loc_a"},
        agent_id="bot1",
    )

    types = {_v3_memory_type(r) for r in _v3_records_desc(agent)}
    assert "decision" in types
    assert "observation" in types


# ── Test 3: old removed bridge module is not importable ───────────────────────

def test_old_memory_bridge_module_not_importable() -> None:
    """The removed bridge module should stay absent."""

    import sys
    for mod_name in list(sys.modules.keys()):
        if "old_memory_bridge" in mod_name:
            del sys.modules[mod_name]

    with pytest.raises((ImportError, ModuleNotFoundError)):
        from app.games.zone_stalkers.memory import old_memory_bridge  # noqa: F401

    assert True
