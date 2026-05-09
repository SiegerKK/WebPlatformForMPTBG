"""Tests for legacy memory → memory_v3 bridge (PR 3)."""
from __future__ import annotations

from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.legacy_bridge import bridge_legacy_entry_to_memory_v3, import_legacy_memory
from app.games.zone_stalkers.rules.tick_rules import _add_memory


def _make_entry(world_turn: int = 100, memory_type: str = "action", **effects) -> dict:
    return {
        "world_turn": world_turn,
        "type": memory_type,
        "title": "test",
        "effects": effects,
        "summary": "test summary",
    }


def test_trade_buy_creates_memory_v3_record() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="trade_buy", item_type="bread", trader_id="trader_1")
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["kind"] == "item_bought"
    assert rec["layer"] == "episodic"
    assert rec["agent_id"] == "bot1"
    assert "trade" in rec["tags"]
    assert "bread" in rec["tags"]
    assert "trader_1" in rec["entity_ids"]


def test_emission_imminent_creates_threat_record() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="emission_imminent", location_id="loc_a")
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["layer"] == "threat"
    assert rec["kind"] == "emission_warning"
    assert "emission" in rec["tags"]
    assert "danger" in rec["tags"]


def test_plan_monitor_abort_creates_record_with_tags() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(
        action_kind="plan_monitor_abort",
        dominant_pressure="thirst",
        scheduled_action_type="travel",
    )
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert "plan_monitor" in rec["tags"]
    assert "thirst" in rec["tags"]


def test_sleep_completed_maps_to_episodic_sleep_completed() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(
        memory_type="action",
        action_kind="sleep_completed",
        sleep_intervals_applied=4,
        turns_slept=120,
        hours_slept=1.0,
        sleepiness_after=30,
    )
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["layer"] == "episodic"
    assert rec["kind"] == "sleep_completed"
    assert "sleep" in rec["tags"]
    assert "rest" in rec["tags"]
    assert "recovery" in rec["tags"]
    assert "sleep_intervals_applied" in rec["details"]


def test_plan_monitor_abort_for_sleep_maps_to_sleep_interrupted() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(
        action_kind="plan_monitor_abort",
        scheduled_action_type="sleep",
        dominant_pressure="hunger",
        sleep_progress_turns=60,
    )
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["kind"] == "sleep_interrupted"
    assert "sleep" in rec["tags"]
    assert "rest" in rec["tags"]


def test_sleep_interval_applied_is_not_stored() -> None:
    """sleep_interval_applied must NOT create a memory_v3 record."""
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="sleep_interval_applied", intervals=1)
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 0


def test_import_legacy_memory_imports_last_n() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    agent["memory"] = [
        _make_entry(world_turn=i, action_kind="trade_buy", item_type="bread")
        for i in range(10)
    ]
    import_legacy_memory(agent, "bot1", world_turn=200)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 10


def test_import_legacy_memory_skips_if_already_populated() -> None:
    """If memory_v3 already has records, do not re-import."""
    agent: dict = {"name": "bot1"}
    agent["memory"] = [_make_entry(action_kind="trade_buy")]
    from app.games.zone_stalkers.memory.models import MemoryRecord, LAYER_EPISODIC
    from app.games.zone_stalkers.memory.store import add_memory_record

    rec = MemoryRecord(
        id="existing",
        agent_id="bot1",
        layer=LAYER_EPISODIC,
        kind="test",
        created_turn=1,
        last_accessed_turn=None,
        summary="s",
        details={},
    )
    add_memory_record(agent, rec)
    import_legacy_memory(agent, "bot1", world_turn=200)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1


def test_legacy_bridge_indexes_trader_entity_id() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="trade_buy", trader_id="trader_1", item_type="bread")
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)

    mem_v3 = ensure_memory_v3(agent)
    rec_id = next(iter(mem_v3["records"]))
    rec = mem_v3["records"][rec_id]
    assert "trader_1" in rec["entity_ids"]
    assert rec_id in mem_v3["indexes"]["by_entity"].get("trader_1", [])


def test_legacy_bridge_indexes_target_entity_id() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="target_seen", target_id="agent_target_1")
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)

    mem_v3 = ensure_memory_v3(agent)
    rec_id = next(iter(mem_v3["records"]))
    rec = mem_v3["records"][rec_id]
    assert rec["kind"] == "target_seen"
    assert rec["layer"] == "social"
    assert "agent_target_1" in rec["entity_ids"]
    assert rec_id in mem_v3["indexes"]["by_entity"].get("agent_target_1", [])


def test_target_not_found_memory_kind_supported() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="target_not_found", target_id="agent_target_1", location_id="loc_b")
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_not_found"
    assert rec["layer"] == "spatial"
    assert "target" in rec["tags"]


def test_target_moved_memory_kind_supported() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(
        action_kind="target_moved",
        target_id="agent_target_1",
        location_id="loc_a",
        from_location_id="loc_a",
        to_location_id="loc_c",
    )
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_moved"
    assert rec["layer"] == "spatial"
    assert rec["location_id"] == "loc_a"
    assert "agent_target_1" in rec["entity_ids"]


def test_target_death_confirmed_memory_kind_supported() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="target_death_confirmed", target_id="agent_target_1")
    bridge_legacy_entry_to_memory_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_death_confirmed"
    assert rec["layer"] == "threat"
    assert rec["importance"] >= 0.85


def test_add_memory_bridges_new_legacy_entry_to_memory_v3() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    state = {"agents": {"bot1": agent}}

    _add_memory(
        agent,
        100,
        state,
        "action",
        "buy",
        {"action_kind": "trade_buy", "item_type": "bread", "trader_id": "trader_1"},
        summary="купил хлеб",
        agent_id="bot1",
    )

    mem_v3 = ensure_memory_v3(agent)
    assert agent["memory"], "legacy memory must be appended"
    recs = list(mem_v3["records"].values())
    assert recs, "bridge must write to memory_v3"
    assert any(r.get("kind") == "item_bought" for r in recs)


def test_add_memory_does_not_bridge_sleep_interval_applied() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    state = {"agents": {"bot1": agent}}

    _add_memory(
        agent,
        101,
        state,
        "action",
        "sleep interval",
        {"action_kind": "sleep_interval_applied", "sleep_intervals_applied": 1},
        summary="интервал сна",
        agent_id="bot1",
    )

    assert len(agent["memory"]) == 1
    assert ensure_memory_v3(agent)["records"] == {}
