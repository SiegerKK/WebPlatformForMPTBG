from __future__ import annotations

import uuid

from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.objective import ObjectiveGenerationContext
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.objectives.generator import (
    OBJECTIVE_GATHER_INTEL,
    OBJECTIVE_TRACK_TARGET,
    generate_objectives,
)
from app.games.zone_stalkers.decision.target_beliefs import build_target_belief, _record_to_hunt_lead
from app.games.zone_stalkers.memory.models import MemoryRecord, LAYER_SOCIAL
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3
from tests.decision.conftest import make_agent, make_minimal_state


def _make_target(*, location_id: str = "loc_b", hp: int = 100, is_alive: bool = True) -> dict:
    return {
        "archetype": "stalker_agent",
        "is_alive": is_alive,
        "location_id": location_id,
        "hp": hp,
        "name": "Цель",
        "id": "target_1",
        "equipment": {"weapon": {"type": "pistol", "value": 100}},
    }


def _make_state(agent: dict, *, target_location_id: str | None = None) -> dict:
    state = make_minimal_state(agent=agent)
    if target_location_id is not None:
        state["agents"]["target_1"] = _make_target(location_id=target_location_id)
    return state


def _remember(
    agent: dict,
    *,
    kind: str,
    created_turn: int,
    confidence: float = 0.8,
    location_id: str | None = None,
    details: dict | None = None,
) -> None:
    ensure_memory_v3(agent)
    add_memory_record(
        agent,
        MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="spatial",
            kind=kind,
            created_turn=created_turn,
            last_accessed_turn=None,
            summary=kind,
            details=details or {"target_id": "target_1", **({"location_id": location_id} if location_id else {})},
            location_id=location_id,
            confidence=confidence,
        ),
    )


def _make_ctx(agent: dict, state: dict) -> ObjectiveGenerationContext:
    ctx = build_agent_context("bot1", agent, state)
    belief_state = build_belief_state(ctx, agent, state["world_turn"])
    need_result = evaluate_need_result(ctx, state)
    target_belief = build_target_belief(
        agent_id="bot1",
        agent=agent,
        state=state,
        world_turn=state["world_turn"],
        belief_state=belief_state,
    )
    return ObjectiveGenerationContext(
        agent_id="bot1",
        world_turn=state["world_turn"],
        belief_state=belief_state,
        need_result=need_result,
        active_plan_summary=None,
        personality=agent,
        target_belief=target_belief,
    )


def test_target_seen_creates_high_confidence_location_hypothesis() -> None:
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
    state = _make_state(agent, target_location_id="loc_a")
    ctx = build_agent_context("bot1", agent, state)
    belief_state = build_belief_state(ctx, agent, state["world_turn"])

    belief = build_target_belief(
        agent_id="bot1",
        agent=agent,
        state=state,
        world_turn=state["world_turn"],
        belief_state=belief_state,
    )

    assert belief.best_location_id == "loc_a"
    assert belief.best_location_confidence >= 0.95
    assert belief.possible_locations[0].reason == "target_seen"


def test_target_not_found_suppresses_old_location() -> None:
    """One miss should strongly suppress a lead but keep it non-zero.

    PR6 staged suppression uses multiplicative factors:
    - 1 miss: * 0.45
    - 2 miss: * 0.20
    - 3 miss: exhausted
    """
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
    state = _make_state(agent)
    # Positive lead: 0.85
    _remember(agent, kind="target_last_known_location", created_turn=95, location_id="loc_b", confidence=0.85)
    # Miss at same location: should apply staged suppression, not hard deletion.
    _remember(agent, kind="target_not_found", created_turn=99, location_id="loc_b", confidence=0.9)
    ctx = build_agent_context("bot1", agent, state)
    belief_state = build_belief_state(ctx, agent, state["world_turn"])

    belief = build_target_belief(
        agent_id="bot1",
        agent=agent,
        state=state,
        world_turn=state["world_turn"],
        belief_state=belief_state,
    )

    assert belief.best_location_id == "loc_b"
    assert 0.30 < belief.location_confidence < 0.45


def test_target_not_found_reduces_but_not_eliminates_with_weak_negative() -> None:
    """A single target_not_found with default confidence (0.75) against a stronger
    positive lead (0.95 target_seen) should reduce confidence significantly but
    NOT zero it out — the net score stays positive.
    """
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
    state = _make_state(agent)
    # Strong positive lead: target_seen confidence 0.95
    _remember(agent, kind="target_seen", created_turn=95, location_id="loc_b", confidence=0.95)
    # Weak negative: target_not_found with default confidence (0.75)
    _remember(agent, kind="target_not_found", created_turn=99, location_id="loc_b", confidence=0.75)
    ctx = build_agent_context("bot1", agent, state)
    belief_state = build_belief_state(ctx, agent, state["world_turn"])

    belief = build_target_belief(
        agent_id="bot1",
        agent=agent,
        state=state,
        world_turn=state["world_turn"],
        belief_state=belief_state,
    )

    # Net score is positive (0.95 - 0.75 = 0.20), so loc_b still appears.
    assert belief.best_location_id == "loc_b", (
        "Location with strong positive lead should survive one weak negative observation"
    )
    # Confidence is meaningfully lower than 0.95 (strongly reduced).
    assert belief.best_location_confidence < 0.50, (
        "One target_not_found should reduce confidence significantly"
    )
    assert belief.best_location_confidence > 0.0, (
        "One target_not_found alone should not eliminate the location"
    )


def test_repeated_target_not_found_exhausts_location() -> None:
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
    state = _make_state(agent)
    _remember(agent, kind="target_last_known_location", created_turn=90, location_id="loc_b", confidence=0.85)
    for idx in range(3):
        _remember(
            agent,
            kind="target_not_found",
            created_turn=95 + idx,
            location_id="loc_b",
            confidence=0.8,
            details={
                "target_id": "target_1",
                "location_id": "loc_b",
                "failed_search_count": idx + 1,
                "cooldown_until_turn": 150 if idx == 2 else None,
            },
        )

    ctx = _make_ctx(agent, state)
    assert "loc_b" in ctx.target_belief.exhausted_locations


def test_target_moved_updates_best_location() -> None:
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
    state = _make_state(agent)
    _remember(agent, kind="target_last_known_location", created_turn=90, location_id="loc_b", confidence=0.65)
    _remember(
        agent,
        kind="target_moved",
        created_turn=99,
        location_id="loc_c",
        confidence=0.9,
        details={"target_id": "target_1", "from_location_id": "loc_b", "to_location_id": "loc_c"},
    )

    ctx = _make_ctx(agent, state)
    assert ctx.target_belief.best_location_id == "loc_c"
    assert ctx.target_belief.likely_routes[0].to_location_id == "loc_c"


def test_track_target_uses_best_non_exhausted_location() -> None:
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
    state = _make_state(agent)
    _remember(agent, kind="target_last_known_location", created_turn=90, location_id="loc_b", confidence=0.85)
    _remember(agent, kind="target_intel", created_turn=98, location_id="loc_c", confidence=0.7)
    for idx in range(3):
        _remember(
            agent,
            kind="target_not_found",
            created_turn=95 + idx,
            location_id="loc_b",
            confidence=0.8,
            details={
                "target_id": "target_1",
                "location_id": "loc_b",
                "failed_search_count": idx + 1,
                "cooldown_until_turn": 150 if idx == 2 else None,
            },
        )

    objectives = generate_objectives(_make_ctx(agent, state))
    track = next(obj for obj in objectives if obj.key == OBJECTIVE_TRACK_TARGET)

    assert track.target == {"target_id": "target_1", "location_id": "loc_c"}


def test_trader_intel_creates_medium_confidence_location_hypothesis() -> None:
    """Trader intel (0.70) should produce a medium-confidence hypothesis,
    strictly higher than stalker intel (0.55) but lower than direct observation (0.95+).
    """
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
    state = _make_state(agent)

    # Trader intel record — simulates what _bot_buy_hunt_intel_from_trader writes.
    ensure_memory_v3(agent)
    add_memory_record(
        agent,
        MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="target_intel",
            created_turn=99,
            last_accessed_turn=None,
            summary="Купил разведданные у торговца",
            details={
                "action_kind": "intel_from_trader",
                "observed": "agent_location",
                "target_agent_id": "target_1",
                "location_id": "loc_target",
                "confidence": 0.70,
            },
            location_id="loc_target",
            confidence=0.70,
            tags=("target", "intel", "social", "trader"),
        ),
    )
    # Stalker intel record — simulates what _bot_ask_colocated_stalkers_about_agent writes.
    add_memory_record(
        agent,
        MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="target_intel",
            created_turn=98,
            last_accessed_turn=None,
            summary="Разведданные от сталкера",
            details={
                "action_kind": "intel_from_stalker",
                "observed": "agent_location",
                "target_agent_id": "target_1",
                "location_id": "loc_other",
                "confidence": 0.55,
            },
            location_id="loc_other",
            confidence=0.55,
            tags=("target", "intel", "social", "stalker"),
        ),
    )

    ctx = build_agent_context("bot1", agent, state)
    belief_state = build_belief_state(ctx, agent, state["world_turn"])
    belief = build_target_belief(
        agent_id="bot1",
        agent=agent,
        state=state,
        world_turn=state["world_turn"],
        belief_state=belief_state,
    )

    # Trader location should have higher confidence than stalker location.
    loc_hypotheses = {h.location_id: h for h in belief.possible_locations}
    assert "loc_target" in loc_hypotheses, "Trader intel should produce a location hypothesis"
    assert "loc_other" in loc_hypotheses, "Stalker intel should produce a location hypothesis"
    trader_conf = loc_hypotheses["loc_target"].confidence
    stalker_conf = loc_hypotheses["loc_other"].confidence
    # Trader (0.70) > stalker (0.55) but both < direct observation (0.95+)
    assert trader_conf > stalker_conf, (
        f"Trader intel ({trader_conf:.2f}) should have higher confidence than stalker intel ({stalker_conf:.2f})"
    )
    assert trader_conf < 0.95, f"Trader intel confidence ({trader_conf:.2f}) should be below direct-observation tier"


def test_hunt_lead_source_agent_prefers_witness_or_trader_source() -> None:
    lead = _record_to_hunt_lead(
        {
            "id": "mem_1",
            "agent_id": "hunter_1",
            "kind": "target_intel",
            "created_turn": 100,
            "location_id": "loc_a",
            "details": {
                "target_id": "target_1",
                "source_agent_id": "witness_7",
            },
            "entity_ids": ["target_1"],
        },
        target_id="target_1",
        world_turn=100,
    )
    assert lead is not None
    assert lead.source_agent_id == "witness_7"


def test_no_leads_generates_gather_intel() -> None:
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
    state = _make_state(agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    keys = {objective.key for objective in objectives}

    assert OBJECTIVE_GATHER_INTEL in keys
