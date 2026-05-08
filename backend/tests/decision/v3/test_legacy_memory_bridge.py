"""Tests for legacy memory → memory_v3 bridge (PR 3)."""
from __future__ import annotations

import pytest
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.legacy_bridge import bridge_legacy_entry_to_memory_v3, import_legacy_memory


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
    entry = _make_entry(action_kind="trade_buy", item_type="bread")
    bridge_legacy_entry_to_memory_v3(agent, entry, 100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["kind"] == "item_bought"
    assert rec["layer"] == "episodic"
    assert "trade" in rec["tags"]
    assert "bread" in rec["tags"]


def test_emission_imminent_creates_threat_record() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(action_kind="emission_imminent", location_id="loc_a")
    bridge_legacy_entry_to_memory_v3(agent, entry, 100)
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
    bridge_legacy_entry_to_memory_v3(agent, entry, 100)
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
    bridge_legacy_entry_to_memory_v3(agent, entry, 100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["layer"] == "episodic"
    assert rec["kind"] == "sleep_completed"
    assert "sleep" in rec["tags"]
    assert "rest" in rec["tags"]
    assert "recovery" in rec["tags"]
    # Sleep-specific details should be preserved.
    assert "sleep_intervals_applied" in rec["details"]


def test_plan_monitor_abort_for_sleep_maps_to_sleep_interrupted() -> None:
    agent: dict = {"name": "bot1", "memory": [], "memory_v3": None}
    entry = _make_entry(
        action_kind="plan_monitor_abort",
        scheduled_action_type="sleep",
        dominant_pressure="hunger",
        sleep_progress_turns=60,
    )
    bridge_legacy_entry_to_memory_v3(agent, entry, 100)
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
    bridge_legacy_entry_to_memory_v3(agent, entry, 100)
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
    # Manually add one record to memory_v3.
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
    # Should still be just 1 — the pre-existing record.
    assert len(records) == 1
