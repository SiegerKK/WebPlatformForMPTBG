from __future__ import annotations

from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store,
    ensure_agent_memory_loaded,
)
from app.games.zone_stalkers.memory.models import LAYER_EPISODIC, MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
from tests.decision.conftest import make_agent, make_minimal_state


def test_tick_migrates_hot_memory_v3_to_cold_store() -> None:
    clear_in_memory_store()
    agent = make_agent(agent_id="bot1")
    ensure_memory_v3(agent)
    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_pre_tick_1",
            agent_id="bot1",
            layer=LAYER_EPISODIC,
            kind="pre_tick_memory",
            created_turn=99,
            last_accessed_turn=None,
            summary="before tick",
            details={"source": "test"},
            location_id="loc_a",
            tags=("test",),
        ),
    )
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_tick_1"
    state["cpu_cold_memory_store_enabled"] = True

    new_state, _events = tick_zone_map(state)
    migrated = new_state["agents"]["bot1"]

    assert migrated.get("memory_ref") is not None
    assert isinstance(migrated.get("memory_summary"), dict)
    assert "memory_v3" not in migrated

    # Debug/on-demand load still recovers migrated memory payload.
    ensure_agent_memory_loaded(
        context_id="ctx_tick_1",
        agent_id="bot1",
        agent=migrated,
    )
    records = ((migrated.get("memory_v3") or {}).get("records") or {})
    assert "mem_pre_tick_1" in records
