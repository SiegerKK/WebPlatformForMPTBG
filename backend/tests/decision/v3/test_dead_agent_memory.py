"""Fix 6 — Dead agents must not receive new memory consolidation.

Tests that decay_memory() is a no-op for agents that have is_alive=False.
"""
from __future__ import annotations

from app.games.zone_stalkers.memory.decay import decay_memory
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3


def _make_dead_agent() -> dict:
    return {
        "name": "ghost",
        "is_alive": False,
        "memory_v3": None,
    }


def _make_alive_agent() -> dict:
    return {
        "name": "survivor",
        "is_alive": True,
        "memory_v3": None,
    }


def _add_test_record(agent: dict, turn: int = 1) -> None:
    entry = {
        "world_turn": turn,
        "type": "action",
        "title": "bought bread",
        "effects": {"action_kind": "trade_buy", "item_type": "bread", "trader_id": "trader_1"},
        "summary": "bought bread at loc_a",
    }
    write_memory_event_to_v3(agent_id="test", agent=agent, legacy_entry=entry, world_turn=turn)


def test_dead_agent_decay_is_noop() -> None:
    """decay_memory on a dead agent must not run consolidation."""
    agent = _make_dead_agent()
    ensure_memory_v3(agent)
    # Add a record directly (bypassing the guard)
    _add_test_record(agent, turn=1)
    records_before = dict(agent["memory_v3"]["records"])

    decay_memory(agent, world_turn=100)

    # Records must not change — consolidation skipped entirely
    assert agent["memory_v3"]["records"] == records_before


def test_dead_agent_last_decay_turn_not_updated() -> None:
    """stats.last_decay_turn must NOT be updated for dead agents."""
    agent = _make_dead_agent()
    ensure_memory_v3(agent)

    decay_memory(agent, world_turn=100)

    stats = agent["memory_v3"]["stats"]
    assert stats.get("last_decay_turn") is None


def test_alive_agent_decay_does_run() -> None:
    """Control: alive agent decay updates last_decay_turn."""
    agent = _make_alive_agent()
    ensure_memory_v3(agent)

    decay_memory(agent, world_turn=100)

    stats = agent["memory_v3"]["stats"]
    assert stats.get("last_decay_turn") == 100
