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
from app.games.zone_stalkers.projections import json_size_bytes
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map, tick_zone_map_many
from tests.decision.conftest import make_agent, make_minimal_state


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))


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


def test_tick_cold_memory_uses_configured_redis_client() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    redis = FakeRedis()
    agent = make_agent(agent_id="bot1")
    ensure_memory_v3(agent)
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_tick_redis"
    state["cpu_cold_memory_store_enabled"] = True
    state["_zone_cold_memory_redis_client"] = redis

    _new_state, _events = tick_zone_map(state)
    assert redis.set_calls


def test_travel_tick_does_not_load_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent(agent_id="bot1")
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_travel_1"
    state["cpu_cold_memory_store_enabled"] = True
    migrate_agent_memory_to_cold_store(context_id="ctx_travel_1", agent_id="bot1", agent=agent)
    ensure_agent_memory_loaded(context_id="ctx_travel_1", agent_id="bot1", agent=agent)
    reset_cold_metrics()
    agent["scheduled_action"] = {
        "type": "travel",
        "to_location_id": "loc_b",
        "turns_remaining": 3,
        "turns_total": 3,
        "started_turn": state["world_turn"],
        "ends_turn": state["world_turn"] + 3,
    }

    _new_state, _events = tick_zone_map(state)
    assert int(get_cold_metrics()["cold_memory_loads"]) == 0


def test_wait_in_shelter_tick_does_not_load_cold_memory() -> None:
    clear_in_memory_store()
    reset_cold_metrics()
    agent = make_agent(agent_id="bot1")
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_wait_1"
    state["cpu_cold_memory_store_enabled"] = True
    migrate_agent_memory_to_cold_store(context_id="ctx_wait_1", agent_id="bot1", agent=agent)
    ensure_agent_memory_loaded(context_id="ctx_wait_1", agent_id="bot1", agent=agent)
    reset_cold_metrics()
    agent["scheduled_action"] = {
        "type": "wait_in_shelter",
        "turns_remaining": 2,
        "turns_total": 2,
        "started_turn": state["world_turn"],
        "ends_turn": state["world_turn"] + 2,
    }

    _new_state, _events = tick_zone_map(state)
    assert int(get_cold_metrics()["cold_memory_loads"]) == 0


def test_hot_agent_state_keeps_only_memory_ref_and_summary() -> None:
    clear_in_memory_store()
    agent = make_agent(agent_id="bot1")
    ensure_memory_v3(agent)
    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_hot_1",
            agent_id="bot1",
            layer=LAYER_EPISODIC,
            kind="note",
            created_turn=1,
            last_accessed_turn=None,
            summary="hot",
            details={},
            location_id="loc_a",
            tags=("test",),
        ),
    )
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_hot_only"
    state["cpu_cold_memory_store_enabled"] = True

    new_state, _events = tick_zone_map(state)
    migrated = new_state["agents"]["bot1"]
    assert isinstance(migrated.get("memory_ref"), str)
    assert isinstance(migrated.get("memory_summary"), dict)
    assert "memory_v3" not in migrated
    assert "knowledge_v1" not in migrated


def test_batch_tick_does_not_serialize_full_memory_in_hot_state() -> None:
    clear_in_memory_store()
    agent = make_agent(agent_id="bot1")
    ensure_memory_v3(agent)
    for i in range(20):
        add_memory_record(
            agent,
            MemoryRecord(
                id=f"mem_batch_{i}",
                agent_id="bot1",
                layer=LAYER_EPISODIC,
                kind="note",
                created_turn=i,
                last_accessed_turn=None,
                summary=f"record {i}",
                details={"i": i},
                location_id="loc_a",
                tags=("test",),
            ),
        )
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_batch"
    state["cpu_cold_memory_store_enabled"] = True

    new_state, _events, _ticks, _stop = tick_zone_map_many(state, 3)
    migrated = new_state["agents"]["bot1"]
    assert "memory_v3" not in migrated
    assert "knowledge_v1" not in migrated


def test_hot_state_json_size_decreases_after_cold_migration() -> None:
    clear_in_memory_store()
    agent = make_agent(agent_id="bot1")
    ensure_memory_v3(agent)
    for i in range(50):
        add_memory_record(
            agent,
            MemoryRecord(
                id=f"mem_size_{i}",
                agent_id="bot1",
                layer=LAYER_EPISODIC,
                kind="note",
                created_turn=i,
                last_accessed_turn=None,
                summary="x" * 80,
                details={"payload": "y" * 120},
                location_id="loc_a",
                tags=("test",),
            ),
        )
    state = make_minimal_state(agent_id="bot1", agent=agent)
    state["context_id"] = "ctx_size"
    state["cpu_cold_memory_store_enabled"] = True
    size_before = json_size_bytes(state)

    new_state, _events = tick_zone_map(state)
    size_after = json_size_bytes(new_state)
    assert size_after < size_before
