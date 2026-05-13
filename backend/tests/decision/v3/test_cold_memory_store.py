from __future__ import annotations

from typing import Any

import pytest

from app.games.zone_stalkers.memory.cold_store import (
    clear_in_memory_store,
    ensure_agent_memory_loaded,
    flush_dirty_agent_memories,
    get_cold_metrics,
    get_agent_memory_ref,
    load_agent_memory,
    mark_agent_memory_dirty,
    migrate_agent_memory_to_cold_store,
    refresh_agent_memory_summary,
    reset_cold_metrics,
    resolve_agent_memory_ref,
    save_agent_memory_if_dirty,
)
from app.games.zone_stalkers.memory.models import LAYER_EPISODIC, MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3


@pytest.fixture(autouse=True)
def _reset_cold_store() -> None:
    clear_in_memory_store()
    reset_cold_metrics()


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
    assert flushed == 0
    assert "memory_v3" not in agent
    assert "knowledge_v1" not in agent
    assert agent["memory_summary"]["is_loaded"] is False


def test_load_uses_existing_memory_ref_even_if_context_id_differs() -> None:
    agent = _agent()
    _seed_memory(agent)
    migrate_agent_memory_to_cold_store(context_id="ctx_real", agent_id="bot1", agent=agent)
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"]["is_loaded"] = False

    ensure_agent_memory_loaded(context_id="ctx_other", agent_id="bot1", agent=agent)
    records = ((agent.get("memory_v3") or {}).get("records") or {})
    assert "mem_seed_1" in records


def test_save_uses_existing_memory_ref_even_if_context_id_differs() -> None:
    agent = _agent()
    _seed_memory(agent)
    migrate_agent_memory_to_cold_store(context_id="ctx_real", agent_id="bot1", agent=agent)
    ensure_agent_memory_loaded(context_id="ctx_real", agent_id="bot1", agent=agent)
    mark_agent_memory_dirty(agent)

    assert save_agent_memory_if_dirty(context_id="ctx_other", agent_id="bot1", agent=agent) is True
    assert save_agent_memory_if_dirty(context_id="ctx_other", agent_id="bot1", agent=agent) is False


def test_missing_cold_key_falls_back_to_hot_memory_if_available() -> None:
    agent = _agent()
    _seed_memory(agent)
    agent["memory_ref"] = get_agent_memory_ref("ctx_missing", "bot1")
    refresh_agent_memory_summary(agent, is_loaded=False, dirty=False)

    blob = load_agent_memory(context_id="ctx_missing", agent_id="bot1", agent=agent)
    assert isinstance(blob, dict)
    assert "memory_v3" in blob
    assert ((blob.get("memory_v3") or {}).get("records") or {}).get("mem_seed_1")
    assert "cold_load_error" not in (agent.get("memory_summary") or {})


def test_missing_cold_key_without_hot_memory_sets_warning_not_empty_success() -> None:
    agent = _agent()
    agent["memory_ref"] = get_agent_memory_ref("ctx_missing", "bot1")
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"] = {"is_loaded": False, "dirty": False}

    blob = load_agent_memory(context_id="ctx_missing", agent_id="bot1", agent=agent)
    assert blob == {}
    summary = agent["memory_summary"]
    assert summary.get("cold_load_error") == "missing_cold_memory_key"
    assert summary.get("is_loaded") is False


def test_flush_dirty_agent_memories_counts_only_actual_saves() -> None:
    dirty_agent = _agent("dirty")
    clean_agent = _agent("clean")
    _seed_memory(dirty_agent)
    _seed_memory(clean_agent)
    migrate_agent_memory_to_cold_store(context_id="ctx_flush", agent_id="dirty", agent=dirty_agent)
    migrate_agent_memory_to_cold_store(context_id="ctx_flush", agent_id="clean", agent=clean_agent)
    ensure_agent_memory_loaded(context_id="ctx_flush", agent_id="dirty", agent=dirty_agent)
    ensure_agent_memory_loaded(context_id="ctx_flush", agent_id="clean", agent=clean_agent)
    mark_agent_memory_dirty(dirty_agent)
    clean_agent["memory_summary"]["dirty"] = False

    saved = flush_dirty_agent_memories(
        context_id="ctx_flush",
        state={"agents": {"dirty": dirty_agent, "clean": clean_agent}},
    )
    assert saved == 1


def test_flush_strips_loaded_memory_even_when_clean() -> None:
    agent = _agent()
    _seed_memory(agent)
    migrate_agent_memory_to_cold_store(context_id="ctx_strip", agent_id="bot1", agent=agent)
    ensure_agent_memory_loaded(context_id="ctx_strip", agent_id="bot1", agent=agent)
    agent["memory_summary"]["dirty"] = False

    flush_dirty_agent_memories(context_id="ctx_strip", state={"agents": {"bot1": agent}})
    assert "memory_v3" not in agent
    assert "knowledge_v1" not in agent
    assert agent["memory_summary"]["is_loaded"] is False


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.set_calls: list[tuple[str, bytes, int | None]] = []

    def get(self, key: str) -> bytes | None:
        return self.values.get(key)

    def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))


def test_redis_backed_cold_store_get_set_with_ttl_and_compression_metrics() -> None:
    agent = _agent()
    _seed_memory(agent, turn=777)
    redis = FakeRedis()
    migrate_agent_memory_to_cold_store(
        context_id="ctx_redis",
        agent_id="bot1",
        agent=agent,
        redis_client=redis,
    )
    # TTL must be passed to redis set.
    assert redis.set_calls
    assert redis.set_calls[0][2] == 60 * 60 * 24 * 30

    # Stored payload is compressed bytes and smaller than raw for repetitive data.
    ensure_agent_memory_loaded(
        context_id="ctx_redis",
        agent_id="bot1",
        agent=agent,
        redis_client=redis,
    )
    metrics = get_cold_metrics()
    assert int(metrics["cold_memory_bytes_raw"]) >= int(metrics["cold_memory_bytes_stored"])
    assert float(metrics["cold_memory_compression_ratio"]) <= 1.0


def test_in_memory_fallback_used_when_no_redis_client() -> None:
    agent = _agent()
    _seed_memory(agent)
    migrate_agent_memory_to_cold_store(context_id="ctx_mem", agent_id="bot1", agent=agent)
    agent.pop("memory_v3", None)
    agent.pop("knowledge_v1", None)
    agent["memory_summary"]["is_loaded"] = False
    ensure_agent_memory_loaded(context_id="ctx_mem", agent_id="bot1", agent=agent, redis_client=None)
    assert ((agent.get("memory_v3") or {}).get("records") or {}).get("mem_seed_1")


def test_resolve_agent_memory_ref_preserves_existing() -> None:
    agent = {"memory_ref": "ctx:agent_memory:fixed:bot1"}
    ref = resolve_agent_memory_ref("ctx_other", "bot1", agent)
    assert ref == "ctx:agent_memory:fixed:bot1"
    assert agent["memory_ref"] == "ctx:agent_memory:fixed:bot1"
