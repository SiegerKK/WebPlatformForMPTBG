from __future__ import annotations

from typing import Any

import pytest

from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store,
    ensure_agent_memory_loaded,
    flush_dirty_agent_memories,
    get_agent_memory_ref,
    mark_agent_memory_dirty,
    migrate_agent_memory_to_cold_store,
    save_agent_memory_if_dirty,
)
from app.games.zone_stalkers.memory.models import LAYER_EPISODIC, MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3


@pytest.fixture(autouse=True)
def _reset_cold_store() -> None:
    clear_in_memory_store()


def _agent(agent_id: str = "bot1") -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": agent_id,
        "memory_v3": None,
        "knowledge_v1": {
            "revision": 1,
            "known_npcs": {},
            "known_locations": {},
            "known_traders": {},
            "known_hazards": {},
            "stats": {"known_npcs_count": 0, "detailed_known_npcs_count": 0, "last_update_turn": 0},
        },
    }


def _seed_memory(agent: dict[str, Any], *, turn: int = 123) -> None:
    ensure_memory_v3(agent)
    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_seed_1",
            agent_id=str(agent.get("id") or "bot1"),
            layer=LAYER_EPISODIC,
            kind="test_event",
            created_turn=turn,
            last_accessed_turn=None,
            summary="seed record",
            details={"k": "v"},
            location_id="loc_a",
            tags=("test",),
        ),
    )


def test_migrate_hot_memory_v3_to_cold_store_sets_ref_and_summary() -> None:
    agent = _agent()
    _seed_memory(agent)

    migrate_agent_memory_to_cold_store(
        context_id="ctx_1",
        agent_id="bot1",
        agent=agent,
    )

    assert agent["memory_ref"] == get_agent_memory_ref("ctx_1", "bot1")
    summary = agent.get("memory_summary")
    assert isinstance(summary, dict)
    assert summary["records_count"] == 1
    assert summary["memory_revision"] >= 1
    assert summary["knowledge_revision"] == 1
    assert summary["is_loaded"] is False
    assert summary["dirty"] is False
    assert "memory_v3" not in agent


def test_load_save_dirty_and_flush_round_trip() -> None:
    agent = _agent()
    _seed_memory(agent, turn=300)
    migrate_agent_memory_to_cold_store(context_id="ctx_2", agent_id="bot1", agent=agent)

    # Load back into hot state for mutation.
    blob = ensure_agent_memory_loaded(context_id="ctx_2", agent_id="bot1", agent=agent)
    assert isinstance(blob, dict)
    assert isinstance(agent.get("memory_v3"), dict)
    assert int(agent["memory_summary"]["records_count"]) == 1

    # Mark dirty and persist once.
    mark_agent_memory_dirty(agent)
    assert save_agent_memory_if_dirty(context_id="ctx_2", agent_id="bot1", agent=agent) is True
    assert save_agent_memory_if_dirty(context_id="ctx_2", agent_id="bot1", agent=agent) is False

    # End-of-tick flush strips heavy memory payload from hot state.
    state = {"agents": {"bot1": agent}}
    flushed = flush_dirty_agent_memories(context_id="ctx_2", state=state)
    assert flushed == 1
    assert "memory_v3" not in agent
    assert "knowledge_v1" not in agent
    assert agent["memory_summary"]["is_loaded"] is False
