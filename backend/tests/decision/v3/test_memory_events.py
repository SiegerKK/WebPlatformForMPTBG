"""Tests for memory_events.py — the sole memory_v3 write API (PR5).

Previously these lived in test_legacy_memory_bridge.py, testing
`bridge_legacy_entry_to_memory_v3`.  They now test `write_memory_event_to_v3`,
which is the renamed canonical function.  All removed-path tests were deleted.
"""
from __future__ import annotations

from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
from app.games.zone_stalkers.memory.memory_events import STALKERS_SEEN_MAX_EPISODIC_PER_LOCATION
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
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="trade_buy", item_type="bread", trader_id="trader_1")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
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
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="emission_imminent", location_id="loc_a")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["layer"] == "threat"
    assert rec["kind"] == "emission_warning"
    assert "emission" in rec["tags"]
    assert "danger" in rec["tags"]


def test_plan_monitor_abort_creates_record_with_tags() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(
        action_kind="plan_monitor_abort",
        dominant_pressure="thirst",
        scheduled_action_type="travel",
    )
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert "plan_monitor" in rec["tags"]
    assert "thirst" in rec["tags"]


def test_sleep_completed_maps_to_episodic_sleep_completed() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(
        memory_type="action",
        action_kind="sleep_completed",
        sleep_intervals_applied=4,
        turns_slept=120,
        hours_slept=1.0,
        sleepiness_after=30,
    )
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
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
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(
        action_kind="plan_monitor_abort",
        scheduled_action_type="sleep",
        dominant_pressure="hunger",
        sleep_progress_turns=60,
    )
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 1
    rec = list(records.values())[0]
    assert rec["kind"] == "sleep_interrupted"
    assert "sleep" in rec["tags"]
    assert "rest" in rec["tags"]


def test_sleep_interval_applied_is_not_stored() -> None:
    """sleep_interval_applied must NOT create a memory_v3 record."""
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="sleep_interval_applied", intervals=1)
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    records = ensure_memory_v3(agent)["records"]
    assert len(records) == 0


def test_write_event_indexes_trader_entity_id() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="trade_buy", trader_id="trader_1", item_type="bread")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)

    mem_v3 = ensure_memory_v3(agent)
    rec_id = next(iter(mem_v3["records"]))
    rec = mem_v3["records"][rec_id]
    assert "trader_1" in rec["entity_ids"]
    assert rec_id in mem_v3["indexes"]["by_entity"].get("trader_1", [])


def test_write_event_indexes_target_entity_id() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="target_seen", target_id="agent_target_1")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)

    mem_v3 = ensure_memory_v3(agent)
    rec_id = next(iter(mem_v3["records"]))
    rec = mem_v3["records"][rec_id]
    assert rec["kind"] == "target_seen"
    assert rec["layer"] == "social"
    assert "agent_target_1" in rec["entity_ids"]
    assert rec_id in mem_v3["indexes"]["by_entity"].get("agent_target_1", [])


def test_target_not_found_memory_kind_supported() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="target_not_found", target_id="agent_target_1", location_id="loc_b")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_not_found"
    assert rec["layer"] == "spatial"
    assert "target" in rec["tags"]


def test_target_moved_memory_kind_supported() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(
        action_kind="target_moved",
        target_id="agent_target_1",
        location_id="loc_a",
        from_location_id="loc_a",
        to_location_id="loc_c",
    )
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_moved"
    assert rec["layer"] == "spatial"
    assert rec["location_id"] == "loc_a"
    assert "agent_target_1" in rec["entity_ids"]


def test_target_seen_bridges_with_entity_and_location() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="target_seen", target_id="agent_target_1", location_id="loc_a")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    mem_v3 = ensure_memory_v3(agent)
    rec_id = next(iter(mem_v3["records"]))
    rec = mem_v3["records"][rec_id]
    assert rec["kind"] == "target_seen"
    assert rec["location_id"] == "loc_a"
    assert "agent_target_1" in rec["entity_ids"]
    assert rec_id in mem_v3["indexes"]["by_entity"].get("agent_target_1", [])
    assert rec_id in mem_v3["indexes"]["by_location"].get("loc_a", [])


def test_target_not_found_bridges_with_location() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="target_not_found", target_id="agent_target_1", location_id="loc_b")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    mem_v3 = ensure_memory_v3(agent)
    rec_id = next(iter(mem_v3["records"]))
    rec = mem_v3["records"][rec_id]
    assert rec["kind"] == "target_not_found"
    assert rec["location_id"] == "loc_b"
    assert rec_id in mem_v3["indexes"]["by_location"].get("loc_b", [])


def test_target_death_confirmed_bridges_with_entity_id() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="target_death_confirmed", target_id="agent_target_1")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    mem_v3 = ensure_memory_v3(agent)
    rec_id = next(iter(mem_v3["records"]))
    rec = mem_v3["records"][rec_id]
    assert rec["kind"] == "target_death_confirmed"
    assert "agent_target_1" in rec["entity_ids"]
    assert rec_id in mem_v3["indexes"]["by_entity"].get("agent_target_1", [])
    assert "target" in rec["tags"]
    assert "death" in rec["tags"]


def test_target_death_confirmed_importance() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(action_kind="target_death_confirmed", target_id="agent_target_1")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_death_confirmed"
    assert rec["layer"] == "threat"
    assert rec["importance"] >= 0.85


def test_intel_from_trader_bridges_to_target_intel() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(
        action_kind="intel_from_trader",
        observed="agent_location",
        target_agent_id="target_1",
        location_id="loc_target",
        source_agent_id="trader_1",
        confidence=0.69,
    )
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)

    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_intel"
    assert rec["layer"] == "social"
    assert rec["location_id"] == "loc_target"
    assert "target_1" in rec["entity_ids"]
    assert "trader_1" in rec["entity_ids"]
    assert "target" in rec["tags"]
    assert "intel" in rec["tags"]
    assert "trader" in rec["tags"]
    assert rec["confidence"] == 0.69


def test_intel_from_stalker_bridges_to_target_intel() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(
        action_kind="intel_from_stalker",
        observed="agent_location",
        target_agent_id="target_1",
        location_id="loc_target",
        source_agent_id="stalker_1",
        confidence=0.51,
    )
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)

    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "target_intel"
    assert rec["layer"] == "social"
    assert rec["location_id"] == "loc_target"
    assert "target_1" in rec["entity_ids"]
    assert "stalker_1" in rec["entity_ids"]
    assert "stalker" in rec["tags"]


def test_add_memory_writes_to_memory_v3() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
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
    recs = list(mem_v3["records"].values())
    assert recs, "write_memory_event_to_v3 must write to memory_v3"
    assert any(r.get("kind") == "item_bought" for r in recs)


def test_add_memory_does_not_bridge_sleep_interval_applied() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
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

    assert ensure_memory_v3(agent)["records"] == {}


def test_write_event_stores_memory_type_in_details() -> None:
    """write_memory_event_to_v3 must store memory_type in details."""
    agent: dict = {"name": "bot1", "memory_v3": None}
    entry = _make_entry(memory_type="observation", action_kind="trade_buy", item_type="bread")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["details"].get("memory_type") == "observation"


def test_write_event_stores_action_kind_in_details() -> None:
    """write_memory_event_to_v3 must store original action_kind in details even when kind is remapped."""
    agent: dict = {"name": "bot1", "memory_v3": None}
    # emission_imminent is remapped to kind="emission_warning" — but details.action_kind must stay
    entry = _make_entry(action_kind="emission_imminent", location_id="loc_a")
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=100)
    rec = next(iter(ensure_memory_v3(agent)["records"].values()))
    assert rec["kind"] == "emission_warning"  # remapped
    assert rec["details"].get("action_kind") == "emission_imminent"  # original preserved


def test_repeated_stalkers_seen_merges_into_semantic_record() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    for turn in range(100, 112):
        entry = _make_entry(
            world_turn=turn,
            memory_type="observation",
            observed="stalkers",
            location_id="loc_bunker",
            entity_ids=["agent_debug_0", "agent_debug_7", "trader_sidor"],
            names=["Сталкер #0", "Сталкер #7", "Сидорович"],
        )
        write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=turn)

    records = list(ensure_memory_v3(agent)["records"].values())
    semantic = [record for record in records if record.get("kind") == "semantic_stalkers_seen"]
    episodic = [
        record for record in records
        if record.get("kind") == "stalkers_seen" and record.get("location_id") == "loc_bunker"
    ]
    active_episodic = [record for record in episodic if record.get("status", "active") != "archived"]
    assert len(semantic) == 1
    assert len(active_episodic) <= 5
    sem = semantic[0]
    assert sem["layer"] == "semantic"
    assert sem["details"]["times_seen"] >= 11
    assert sem["details"]["last_seen_turn"] == 111
    assert sem["details"]["unique_entity_count"] == 3


def test_repeated_travel_hop_updates_route_semantic_memory() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    for turn in range(200, 206):
        entry = _make_entry(
            world_turn=turn,
            memory_type="action",
            action_kind="travel_hop",
            location_id="loc_b",
            from_location_id="loc_a",
            to_location_id="loc_b",
        )
        write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=turn)

    records = list(ensure_memory_v3(agent)["records"].values())
    route_semantic = [record for record in records if record.get("kind") == "semantic_route_traveled"]
    assert len(route_semantic) == 1
    route = route_semantic[0]
    assert route["layer"] == "spatial"
    assert route["details"]["from_location_id"] == "loc_a"
    assert route["details"]["to_location_id"] == "loc_b"
    assert route["details"]["times_traveled"] >= 6
    assert route["details"]["last_traveled_turn"] == 205


def test_repeated_stalkers_seen_keeps_episodic_under_budget() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    for turn in range(100, 300):
        entry = _make_entry(
            world_turn=turn,
            memory_type="observation",
            observed="stalkers",
            location_id="loc_repeat",
            entity_ids=["agent_debug_0", "agent_debug_7"],
            names=["Сталкер #0", "Сталкер #7"],
        )
        write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=turn)
    records = list(ensure_memory_v3(agent)["records"].values())
    stalkers_seen = [
        rec for rec in records
        if rec.get("kind") == "stalkers_seen" and rec.get("status", "active") != "archived"
    ]
    semantic = [rec for rec in records if rec.get("kind") == "semantic_stalkers_seen"]
    assert len(stalkers_seen) <= STALKERS_SEEN_MAX_EPISODIC_PER_LOCATION
    assert len(semantic) >= 1
    assert int(semantic[0]["details"].get("times_seen", 0)) > 1


def test_repeated_stalkers_seen_updates_semantic_last_seen_turn() -> None:
    agent: dict = {"name": "bot1", "memory_v3": None}
    for turn in (4010, 4020, 4030):
        write_memory_event_to_v3(
            agent_id="bot1",
            agent=agent,
            legacy_entry=_make_entry(
                world_turn=turn,
                memory_type="observation",
                observed="stalkers",
                location_id="loc_semantic",
                entity_ids=["agent_a", "agent_b"],
                names=["А", "Б"],
            ),
            world_turn=turn,
        )
    semantic = [
        rec for rec in ensure_memory_v3(agent)["records"].values()
        if rec.get("kind") == "semantic_stalkers_seen"
    ]
    assert len(semantic) == 1
    details = semantic[0]["details"]
    assert details["times_seen"] >= 3
    assert details["last_seen_turn"] == 4030
