from __future__ import annotations

from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store,
    ensure_agent_memory_loaded,
    get_cold_metrics,
    migrate_agent_memory_to_cold_store,
    reset_cold_metrics,
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


def test_scheduled_action_tick_does_not_load_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent(agent_id="bot1")
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_sched_1"
    state["cpu_cold_memory_store_enabled"] = True
    migrate_agent_memory_to_cold_store(context_id="ctx_sched_1", agent_id="bot1", agent=agent)
    ensure_agent_memory_loaded(context_id="ctx_sched_1", agent_id="bot1", agent=agent)
    reset_cold_metrics()
    agent["max_hp"] = int(agent.get("max_hp", agent.get("hp", 100)))
    agent["scheduled_action"] = {
        "type": "sleep",
        "turns_remaining": 3,
        "turns_total": 3,
        "started_turn": state["world_turn"],
        "ends_turn": state["world_turn"] + 3,
    }

    _new_state, _events = tick_zone_map(state)
    assert int(get_cold_metrics()["cold_memory_loads"]) == 0


def test_brain_decision_loads_cold_memory_when_decision_runs() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent(agent_id="bot1")
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_brain_1"
    state["cpu_cold_memory_store_enabled"] = True
    migrate_agent_memory_to_cold_store(context_id="ctx_brain_1", agent_id="bot1", agent=agent)
    # No scheduled action => decision pipeline runs.
    agent.pop("scheduled_action", None)
    agent["memory_summary"]["is_loaded"] = False
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)

    _new_state, _events = tick_zone_map(state)
    assert int(get_cold_metrics()["cold_memory_loads"]) >= 1
