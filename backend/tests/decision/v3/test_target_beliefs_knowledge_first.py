from __future__ import annotations

import app.games.zone_stalkers.decision.target_beliefs as target_beliefs_module
from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.target_beliefs import build_target_belief
from app.games.zone_stalkers.knowledge.knowledge_store import (
    ensure_knowledge_v1,
    upsert_known_corpse,
    upsert_known_npc,
)
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from tests.decision.conftest import make_agent, make_minimal_state


def _make_target(*, location_id: str = "loc_b", hp: int = 80, is_alive: bool = True) -> dict:
    return {
        "id": "target_1",
        "name": "Цель",
        "archetype": "stalker_agent",
        "location_id": location_id,
        "is_alive": is_alive,
        "hp": hp,
        "max_hp": 100,
        "equipment": {
            "weapon": {"type": "ak74"},
            "armor": {"type": "stalker_suit"},
        },
    }


def _build_belief(agent: dict, state: dict):
    ctx = build_agent_context("bot1", agent, state)
    belief_state = build_belief_state(ctx, agent, state["world_turn"])
    return build_target_belief(
        agent_id="bot1",
        agent=agent,
        state=state,
        world_turn=state["world_turn"],
        belief_state=belief_state,
    )


def test_target_belief_uses_known_npc_last_seen_without_memory_records() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    target = _make_target(location_id="loc_z")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Цель",
        location_id="loc_b",
        world_turn=95,
        source="direct_observation",
        confidence=0.9,
        observed_agent=target,
    )

    belief = _build_belief(agent, state)
    assert belief.best_location_id == "loc_b"
    assert belief.last_seen_turn == 95
    assert agent["brain_context_metrics"]["target_belief_memory_fallbacks"] == 0


def test_target_belief_uses_hunt_evidence_last_seen_without_memory_records() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = _make_target(location_id="loc_z")

    knowledge = ensure_knowledge_v1(agent)
    knowledge["known_npcs"]["target_1"] = {
        "agent_id": "target_1",
        "name": "Цель",
        "last_seen_turn": 90,
        "last_direct_seen_turn": 90,
        "last_seen_location_id": None,
        "is_alive": True,
        "confidence": 0.8,
        "equipment_summary": {
            "weapon_class": "rifle",
            "armor_class": "medium",
            "combat_strength_estimate": 0.7,
        },
    }
    knowledge["hunt_evidence"]["target_1"] = {
        "target_id": "target_1",
        "last_seen": {"location_id": "loc_c", "turn": 99, "confidence": 0.85, "source": "witness_report"},
        "death": None,
        "route_hints": [],
        "failed_search_locations": {},
        "recent_contact": {"turn": 99, "location_id": "loc_c"},
        "revision": 1,
    }

    belief = _build_belief(agent, state)
    assert belief.best_location_id == "loc_c"
    assert belief.recent_contact_location_id == "loc_c"
    assert agent["brain_context_metrics"]["target_belief_memory_fallbacks"] == 0


def test_target_belief_uses_known_corpse_as_death_evidence() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = _make_target(location_id="loc_d", is_alive=False)

    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Цель",
        location_id="loc_d",
        world_turn=95,
        source="witness_report",
        confidence=0.8,
        death_status={
            "is_alive": False,
            "death_directly_confirmed": True,
            "corpse_id": "corpse_target_1",
            "reported_corpse_location_id": "loc_d",
        },
    )
    upsert_known_corpse(
        agent,
        corpse_id="corpse_target_1",
        dead_agent_id="target_1",
        dead_agent_name="Цель",
        location_id="loc_d",
        world_turn=96,
    )

    belief = _build_belief(agent, state)
    assert belief.best_location_id == "loc_d"
    assert belief.is_alive is False


def test_target_belief_ignores_stale_corpse_for_alive_target() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    target = _make_target(location_id="loc_b", is_alive=True)
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Цель",
        location_id="loc_b",
        world_turn=99,
        source="direct_observation",
        confidence=0.95,
        observed_agent=target,
    )
    upsert_known_corpse(
        agent,
        corpse_id="corpse_target_1",
        dead_agent_id="target_1",
        dead_agent_name="Цель",
        location_id="loc_dead",
        world_turn=98,
        is_stale=True,
        stale_reason="expired",
    )

    belief = _build_belief(agent, state)
    assert belief.best_location_id == "loc_b"
    assert "loc_dead" not in {hyp.location_id for hyp in belief.possible_locations}


def test_visible_alive_target_contradicts_reported_dead_knowledge() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    target = _make_target(location_id="loc_a", is_alive=True)
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Цель",
        location_id="loc_old",
        world_turn=90,
        source="witness_report",
        confidence=0.8,
        death_status={
            "is_alive": False,
            "death_directly_confirmed": False,
            "reported_corpse_location_id": "loc_old",
        },
    )
    major_before = int(agent["knowledge_v1"].get("major_revision", 0))

    belief = _build_belief(agent, state)
    npc = agent["knowledge_v1"]["known_npcs"]["target_1"]
    assert belief.visible_now is True
    assert belief.is_alive is True
    assert npc["death_evidence"]["status"] == "contradicted"
    assert int(agent["knowledge_v1"].get("major_revision", 0)) > major_before


def test_recently_seen_uses_hunt_evidence_recent_contact_without_memory_records() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = _make_target(location_id="loc_z")

    knowledge = ensure_knowledge_v1(agent)
    knowledge["known_npcs"]["target_1"] = {
        "agent_id": "target_1",
        "name": "Цель",
        "last_seen_turn": 90,
        "last_direct_seen_turn": 90,
        "last_seen_location_id": None,
        "is_alive": True,
        "confidence": 0.8,
        "equipment_summary": {"weapon_class": "rifle", "armor_class": "medium"},
    }
    knowledge["hunt_evidence"]["target_1"] = {
        "target_id": "target_1",
        "last_seen": {"location_id": "loc_b", "turn": 96, "confidence": 0.8, "source": "witness_report"},
        "death": None,
        "route_hints": [],
        "failed_search_locations": {},
        "recent_contact": {"turn": 96, "location_id": "loc_b"},
        "revision": 1,
    }

    belief = _build_belief(agent, state)
    assert belief.recently_seen is True
    assert belief.recent_contact_turn == 96
    assert belief.recent_contact_location_id == "loc_b"


def test_equipment_known_uses_known_npc_equipment_summary() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    target = _make_target(location_id="loc_b", hp=60)
    state["agents"]["target_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Цель",
        location_id="loc_b",
        world_turn=99,
        source="direct_observation",
        confidence=0.95,
        observed_agent=target,
    )

    belief = _build_belief(agent, state)
    assert belief.equipment_known is True
    assert belief.combat_strength is not None
    assert belief.combat_strength_confidence > 0.0


def test_failed_search_locations_from_hunt_evidence_suppress_exhausted_location() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = _make_target(location_id="loc_z")

    knowledge = ensure_knowledge_v1(agent)
    knowledge["known_npcs"]["target_1"] = {
        "agent_id": "target_1",
        "name": "Цель",
        "last_seen_turn": 95,
        "last_direct_seen_turn": 95,
        "last_seen_location_id": "loc_b",
        "is_alive": True,
        "confidence": 0.9,
        "equipment_summary": {"weapon_class": "rifle", "armor_class": "medium"},
    }
    knowledge["hunt_evidence"]["target_1"] = {
        "target_id": "target_1",
        "last_seen": {"location_id": "loc_b", "turn": 95, "confidence": 0.9, "source": "witness_report"},
        "death": None,
        "route_hints": [],
        "failed_search_locations": {
            "loc_b": {"count": 3, "turn": 99, "cooldown_until_turn": 150, "confidence": 0.8},
        },
        "recent_contact": {"turn": 95, "location_id": "loc_b"},
        "revision": 1,
    }

    belief = _build_belief(agent, state)
    assert "loc_b" in belief.exhausted_locations


def test_route_hints_from_hunt_evidence_without_memory_records() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["locations"]["loc_c"] = {
        "name": "Локация В",
        "terrain_type": "buildings",
        "anomaly_activity": 0,
        "connections": [{"to": "loc_b", "travel_time": 12}],
        "items": [],
        "agents": [],
    }
    state["agents"]["target_1"] = _make_target(location_id="loc_z")

    knowledge = ensure_knowledge_v1(agent)
    knowledge["known_npcs"]["target_1"] = {
        "agent_id": "target_1",
        "name": "Цель",
        "last_seen_turn": 95,
        "last_direct_seen_turn": 95,
        "last_seen_location_id": "loc_b",
        "is_alive": True,
        "confidence": 0.8,
        "equipment_summary": {"weapon_class": "rifle", "armor_class": "medium"},
    }
    knowledge["hunt_evidence"]["target_1"] = {
        "target_id": "target_1",
        "last_seen": {"location_id": "loc_b", "turn": 95, "confidence": 0.8, "source": "witness_report"},
        "death": None,
        "route_hints": [{"from_location_id": "loc_b", "to_location_id": "loc_c", "turn": 99, "confidence": 0.85}],
        "failed_search_locations": {},
        "recent_contact": {"turn": 95, "location_id": "loc_b"},
        "revision": 1,
    }

    belief = _build_belief(agent, state)
    assert belief.likely_routes
    assert belief.likely_routes[0].to_location_id == "loc_c"


def test_legacy_memory_fallback_used_when_knowledge_missing() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = _make_target(location_id="loc_z")

    mem = ensure_memory_v3(agent)
    mem["records"]["r1"] = {
        "id": "r1",
        "agent_id": "bot1",
        "layer": "spatial",
        "kind": "target_last_known_location",
        "title": "target lead",
        "summary": "target lead",
        "created_turn": 99,
        "status": "active",
        "location_id": "loc_b",
        "details": {"target_id": "target_1", "location_id": "loc_b"},
        "entity_ids": ["target_1"],
        "confidence": 0.9,
    }

    belief = _build_belief(agent, state)
    assert belief.best_location_id == "loc_b"
    assert agent["brain_context_metrics"]["target_belief_memory_fallbacks"] >= 1


def test_no_memory_scan_when_knowledge_is_sufficient() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    target = _make_target(location_id="loc_b")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = target

    upsert_known_npc(
        agent,
        other_agent_id="target_1",
        name="Цель",
        location_id="loc_b",
        world_turn=99,
        source="direct_observation",
        confidence=0.95,
        observed_agent=target,
    )

    # No memory records exist. Narrow negative scan is allowed but should return nothing.
    belief = _build_belief(agent, state)
    assert belief.best_location_id == "loc_b"
    assert agent["brain_context_metrics"]["target_belief_memory_fallbacks"] == 0
    assert agent["brain_context_metrics"]["target_belief_negative_memory_fallbacks"] == 0


def test_target_belief_does_not_scan_memory_when_known_npc_last_seen_is_old_but_sufficient() -> None:
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["world_turn"] = 500
    state["agents"]["target_1"] = _make_target(location_id="loc_z")

    knowledge = ensure_knowledge_v1(agent)
    knowledge["known_npcs"]["target_1"] = {
        "agent_id": "target_1",
        "name": "Цель",
        "last_seen_turn": 300,
        "last_direct_seen_turn": None,
        "last_seen_location_id": "loc_b",
        "is_alive": True,
        "confidence": 0.8,
    }

    # No memory records exist — negative scan returns nothing, but is allowed.
    belief = _build_belief(agent, state)
    assert belief.best_location_id == "loc_b"
    assert belief.last_seen_turn == 300
    assert belief.recent_contact_turn is None
    # Full memory fallback must not be used when knowledge leads exist.
    assert agent["brain_context_metrics"]["target_belief_memory_fallbacks"] == 0
    # No negative leads in empty memory — negative fallback metric stays zero.
    assert agent["brain_context_metrics"]["target_belief_negative_memory_fallbacks"] == 0


def test_target_belief_applies_legacy_target_not_found_even_when_knowledge_location_lead_exists() -> None:
    """Regression: target_not_found suppression must fire even when knowledge_v1 already
    provides a positive location lead. Before this fix, PR9 skipped the negative-only
    scan when knowledge_leads were present, so exhaustion didn't accumulate."""
    agent = make_agent(kill_target_id="target_1", location_id="loc_a")
    state = make_minimal_state(agent=agent)
    state["agents"]["target_1"] = _make_target(location_id="loc_z")

    # Positive lead from knowledge_v1 — points to loc_false.
    knowledge = ensure_knowledge_v1(agent)
    knowledge["hunt_evidence"]["target_1"] = {
        "target_id": "target_1",
        "last_seen": {"location_id": "loc_false", "turn": 90, "confidence": 0.8, "source": "witness_report"},
        "death": None,
        "route_hints": [],
        "failed_search_locations": {},
        "recent_contact": None,
        "revision": 1,
    }

    # 3 legacy target_not_found records for loc_false → should exhaust it.
    mem = ensure_memory_v3(agent)
    for i in range(3):
        rec_id = f"tnf_{i}"
        mem["records"][rec_id] = {
            "id": rec_id,
            "agent_id": "bot1",
            "layer": "spatial",
            "kind": "target_not_found",
            "title": "not found",
            "summary": "target not found",
            "created_turn": 80 + i,
            "status": "active",
            "location_id": "loc_false",
            "details": {"target_id": "target_1", "location_id": "loc_false"},
            "entity_ids": ["target_1"],
            "confidence": 0.75,
        }

    belief = _build_belief(agent, state)
    # loc_false must be in exhausted_locations because 3 target_not_found leads
    # were loaded via the narrow negative scan.
    assert "loc_false" in belief.exhausted_locations, (
        f"loc_false should be exhausted; exhausted_locations={belief.exhausted_locations}"
    )
    # Full memory fallback must NOT have been used.
    assert agent["brain_context_metrics"]["target_belief_memory_fallbacks"] == 0
    # Negative scan metric must have been incremented.
    assert agent["brain_context_metrics"]["target_belief_negative_memory_fallbacks"] >= 1
