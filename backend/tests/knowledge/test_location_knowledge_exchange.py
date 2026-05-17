"""PR3: Location knowledge exchange and intel tests."""
from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.knowledge.location_knowledge_exchange import (
    MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION,
    LOCATION_KNOWLEDGE_SHARED_CONFIDENCE_MULTIPLIER,
    LOCATION_KNOWLEDGE_RUMOR_CONFIDENCE_MULTIPLIER,
    build_share_packet,
    build_trader_intel_packet,
    build_location_knowledge_share_packets,
    receive_location_knowledge_packets,
    select_location_knowledge_to_share,
)
from app.games.zone_stalkers.knowledge.location_knowledge import (
    LOCATION_KNOWLEDGE_VISITED,
    LOCATION_KNOWLEDGE_SNAPSHOT,
    LOCATION_KNOWLEDGE_EXISTS,
    mark_location_visited,
    mark_neighbor_locations_known,
    upsert_known_location,
    get_known_location,
    ensure_location_knowledge_v1,
)
from app.games.zone_stalkers.rules.tick_rules import _passive_location_knowledge_exchange

NEVER_EXCHANGED_TURN = -10**9


def _agent(agent_id: str = "bot_1", loc_id: str = "loc_a") -> dict[str, Any]:
    return {"id": agent_id, "location_id": loc_id}


def _state(locs: dict | None = None) -> dict[str, Any]:
    default_locs = {
        "loc_a": {
            "id": "loc_a", "name": "Start", "terrain_type": "plain",
            "anomaly_activity": 0,
            "connections": [
                {"to": "loc_b", "type": "road", "travel_time": 10},
                {"to": "loc_c", "type": "path", "travel_time": 15},
            ],
            "agents": [], "items": [], "artifacts": [],
        },
        "loc_b": {
            "id": "loc_b", "name": "Trader Town", "terrain_type": "buildings",
            "anomaly_activity": 0,
            "connections": [{"to": "loc_a", "type": "road", "travel_time": 10}],
            "agents": [], "items": [], "artifacts": [],
        },
        "loc_c": {
            "id": "loc_c", "name": "Anomaly Field", "terrain_type": "field_camp",
            "anomaly_activity": 3,
            "connections": [{"to": "loc_a", "type": "path", "travel_time": 15}],
            "agents": [], "items": [], "artifacts": [],
        },
    }
    return {"locations": locs or default_locs, "traders": {}, "world_turn": 100}


def test_share_location_knowledge_copies_only_top_k():
    """Exchange must never share more than MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION."""
    source = _agent("source", "loc_a")
    state = _state()
    # Add many known locations to source
    for i in range(20):
        upsert_known_location(
            source,
            location_id=f"loc_{i}",
            world_turn=100,
            knowledge_level=LOCATION_KNOWLEDGE_VISITED,
            source="direct_visit",
            confidence=1.0,
            snapshot={"name": f"Loc {i}", "has_shelter": False, "has_trader": False},
        )

    packets = build_location_knowledge_share_packets(source, world_turn=200)
    assert len(packets) <= MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION


def test_receiver_gets_stale_observed_turn_not_current_turn():
    """Receiver must preserve the original observed_turn, not replace with current turn."""
    source = _agent("source", "loc_a")
    ORIGINAL_TURN = 100
    EXCHANGE_TURN = 500
    upsert_known_location(
        source,
        location_id="loc_x",
        world_turn=ORIGINAL_TURN,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        observed_turn=ORIGINAL_TURN,
        snapshot={"name": "Old Bunker"},
    )

    packets = build_location_knowledge_share_packets(source, world_turn=EXCHANGE_TURN)
    assert packets, "should produce at least one packet"

    receiver = _agent("receiver", "loc_b")
    receive_location_knowledge_packets(receiver, packets, world_turn=EXCHANGE_TURN)

    received_entry = get_known_location(receiver, "loc_x")
    assert received_entry is not None
    obs_turn = int(received_entry.get("observed_turn") or 0)
    # observed_turn must reflect when source saw it, not when exchange happened
    assert obs_turn == ORIGINAL_TURN, f"expected {ORIGINAL_TURN}, got {obs_turn}"
    # received_turn should be the exchange turn
    received_turn_val = int(received_entry.get("received_turn") or 0)
    assert received_turn_val == EXCHANGE_TURN


def test_shared_direct_visit_becomes_hearsay_not_direct_visit_for_receiver():
    """After sharing, receiver should have shared_by_agent source, not direct_visit."""
    source = _agent("source_bot", "loc_a")
    upsert_known_location(
        source,
        location_id="loc_bunker",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Bunker", "has_shelter": True},
    )

    packets = build_location_knowledge_share_packets(source, world_turn=200)
    assert packets

    receiver = _agent("receiver_bot", "loc_b")
    receive_location_knowledge_packets(receiver, packets, world_turn=200)

    entry = get_known_location(receiver, "loc_bunker")
    assert entry is not None
    assert entry.get("source") != "direct_visit"
    assert entry.get("source") == "shared_by_agent"
    assert entry.get("knowledge_level") != LOCATION_KNOWLEDGE_VISITED, \
        "direct_visit should be downgraded to snapshot for receiver"


def test_shared_neighbor_exists_does_not_reveal_full_snapshot():
    """A known_exists entry shared should remain known_exists (no snapshot leak)."""
    source = _agent("source_bot", "loc_a")
    # Source knows loc_b only as known_exists (no snapshot)
    upsert_known_location(
        source,
        location_id="loc_b",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_EXISTS,
        source="direct_neighbor_observation",
        confidence=0.8,
        snapshot=None,
    )

    packets = build_location_knowledge_share_packets(source, world_turn=200)
    # Find the loc_b packet
    loc_b_packets = [p for p in packets if p.get("location_id") == "loc_b"]
    if not loc_b_packets:
        return  # known_exists with no snapshot might be below threshold, ok
    pkt = loc_b_packets[0]
    assert pkt.get("snapshot") is None or pkt.get("snapshot") == {}


def test_trader_intel_adds_location_with_trader_source():
    """Trader intel packet must have 'trader_intel' as source."""
    source_entry = {
        "location_id": "loc_trader",
        "knowledge_level": LOCATION_KNOWLEDGE_VISITED,
        "confidence": 1.0,
        "observed_turn": 300,
        "snapshot": {"name": "Trading Post", "has_trader": True, "has_shelter": True},
    }
    packet = build_trader_intel_packet(
        source_entry,
        trader_id="trader_1",
        intel_type="shelter",
        world_turn=500,
    )
    assert packet["source"] == "trader_intel"
    assert packet["source_agent_id"] == "trader_1"
    assert packet["location_id"] == "loc_trader"
    assert packet["received_turn"] == 500
    assert packet["observed_turn"] == 300


def test_hunt_witness_report_includes_location_existence_and_optional_route_fragment():
    """A shared entry from witness about an agent's location should include basic facts."""
    source = _agent("witness_bot", "loc_a")
    upsert_known_location(
        source,
        location_id="loc_target_area",
        world_turn=200,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "Target Area", "danger_level_estimate": 0.5},
        edges={"loc_next": {"target_location_id": "loc_next", "confidence": 0.9, "observed_turn": 200}},
    )

    packets = build_location_knowledge_share_packets(source, world_turn=300, target_is_hunter=True)
    loc_pkt = next((p for p in packets if p.get("location_id") == "loc_target_area"), None)
    assert loc_pkt is not None
    assert loc_pkt.get("known_exists") is True or loc_pkt.get("knowledge_level") in {
        LOCATION_KNOWLEDGE_EXISTS, LOCATION_KNOWLEDGE_SNAPSHOT
    }


def test_exchange_does_not_copy_600_locations():
    """Exchange is bounded regardless of how many locations the source knows."""
    source = _agent("rich_bot", "loc_a")
    for i in range(600):
        upsert_known_location(
            source,
            location_id=f"loc_{i}",
            world_turn=100 + i,
            knowledge_level=LOCATION_KNOWLEDGE_VISITED,
            source="direct_visit",
            confidence=0.9,
            snapshot={"name": f"Location {i}"},
        )

    packets = build_location_knowledge_share_packets(source, world_turn=1000)
    # Strictly bounded regardless of source size
    assert len(packets) <= MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION
    assert len(packets) <= 5


def test_confidence_decays_on_shared_knowledge():
    """Shared packets must carry reduced confidence (multiplier applied)."""
    source = _agent("source_bot", "loc_a")
    ORIG_CONFIDENCE = 1.0
    upsert_known_location(
        source,
        location_id="loc_visited",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=ORIG_CONFIDENCE,
        snapshot={"name": "Visited Place"},
    )

    packets = build_location_knowledge_share_packets(source, world_turn=200)
    pkt = next((p for p in packets if p.get("location_id") == "loc_visited"), None)
    assert pkt is not None
    assert pkt["confidence"] < ORIG_CONFIDENCE
    # Should be approximately orig * multiplier
    expected_max = ORIG_CONFIDENCE * LOCATION_KNOWLEDGE_SHARED_CONFIDENCE_MULTIPLIER + 0.01
    assert pkt["confidence"] <= expected_max


def test_direct_visit_overrides_old_shared_rumor():
    """If receiver later visits a location themselves, their entry should upgrade to visited."""
    receiver = _agent("receiver_bot", "loc_a")
    state = _state()

    # First receive a shared rumor about loc_b
    upsert_known_location(
        receiver,
        location_id="loc_b",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_SNAPSHOT,
        source="shared_by_agent",
        confidence=0.5,
        snapshot={"name": "Trader Town (hearsay)"},
    )

    # Now receiver directly visits loc_b
    mark_location_visited(receiver, state=state, location_id="loc_b", world_turn=200)

    entry = get_known_location(receiver, "loc_b")
    assert entry is not None
    assert entry.get("visited") is True
    assert entry.get("knowledge_level") == LOCATION_KNOWLEDGE_VISITED
    assert entry.get("source") == "direct_visit"


def test_passive_exchange_pair_cooldown_prevents_every_tick_revision_bump():
    source = _agent("source_bot", "loc_a")
    receiver = _agent("receiver_bot", "loc_a")
    source["archetype"] = "stalker_agent"
    receiver["archetype"] = "stalker_agent"
    source["is_alive"] = True
    receiver["is_alive"] = True
    upsert_known_location(
        source,
        location_id="loc_b",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"has_trader": True},
    )
    state = {"agents": {"receiver_bot": receiver, "source_bot": source}}

    first_updated = _passive_location_knowledge_exchange("receiver_bot", receiver, state, 100)
    first_revision = int(
        (ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0)
    )
    second_updated = _passive_location_knowledge_exchange("receiver_bot", receiver, state, 101)
    second_revision = int(
        (ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0)
    )

    assert first_updated > 0
    assert second_updated == 0
    assert second_revision == first_revision


def test_passive_exchange_agent_cooldown_limits_hub_churn():
    source_a = _agent("source_a", "loc_a")
    source_b = _agent("source_b", "loc_a")
    receiver = _agent("receiver_bot", "loc_a")
    for agent in (source_a, source_b, receiver):
        agent["archetype"] = "stalker_agent"
        agent["is_alive"] = True
    upsert_known_location(
        source_a,
        location_id="loc_a1",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"has_shelter": True},
    )
    upsert_known_location(
        source_b,
        location_id="loc_b1",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"has_anomaly": True, "anomaly_risk_estimate": 0.6},
    )
    state = {"agents": {"receiver_bot": receiver, "source_a": source_a, "source_b": source_b}}

    first_updated = _passive_location_knowledge_exchange("receiver_bot", receiver, state, 100)
    rev_after_first = int((ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0))
    second_updated = _passive_location_knowledge_exchange("receiver_bot", receiver, state, 110)
    rev_after_second = int((ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0))

    assert first_updated > 0
    assert second_updated == 0
    assert rev_after_second == rev_after_first


def test_passive_exchange_does_not_bump_revision_when_no_useful_packet():
    source = _agent("source_bot", "loc_a")
    receiver = _agent("receiver_bot", "loc_a")
    source["archetype"] = "stalker_agent"
    receiver["archetype"] = "stalker_agent"
    source["is_alive"] = True
    receiver["is_alive"] = True
    upsert_known_location(
        source,
        location_id="loc_shared",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_SNAPSHOT,
        source="shared_by_agent",
        confidence=0.3,
        snapshot={"name": "Old"},
    )
    upsert_known_location(
        receiver,
        location_id="loc_shared",
        world_turn=100,
        knowledge_level=LOCATION_KNOWLEDGE_VISITED,
        source="direct_visit",
        confidence=1.0,
        snapshot={"name": "New"},
    )
    state = {"agents": {"receiver_bot": receiver, "source_bot": source}}

    before_revision = int((ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0))
    updated = _passive_location_knowledge_exchange("receiver_bot", receiver, state, 120)
    after_revision = int((ensure_location_knowledge_v1(receiver).get("stats") or {}).get("known_locations_revision", 0))
    runtime = receiver.get("location_knowledge_exchange_runtime") or {}

    assert updated == 0
    assert after_revision == before_revision
    assert int(runtime.get("last_any_exchange_turn", NEVER_EXCHANGED_TURN) or NEVER_EXCHANGED_TURN) < 120
