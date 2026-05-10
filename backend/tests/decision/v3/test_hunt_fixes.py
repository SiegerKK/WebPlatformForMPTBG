"""Tests for hunt/kill-NPC system fixes (PR5 Stage 3).

Covers:
  1. VERIFY_LEAD plan completes early after search_target finds target (Fix 2).
  2. target_seen memory_v3 record generates ENGAGE_TARGET objective (Fix 3).
  3. Soft thirst should NOT override recently_seen ENGAGE_TARGET (Fix 3 scoring).
  4. question_witnesses writes witness_source_exhausted when no witnesses (Fix 6).
  5. Zero-confidence possible_locations are excluded from TargetBelief (Fix 8).
  6. Route hints ignore exhausted destinations (Fix 9).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.games.zone_stalkers.decision.active_plan_manager import (
    get_active_plan,
    save_active_plan,
)
from app.games.zone_stalkers.decision.active_plan_runtime import (
    start_or_continue_active_plan_step,
)
from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.active_plan import (
    ActivePlanStep,
    ActivePlanV3,
    ACTIVE_PLAN_STATUS_ACTIVE,
    STEP_STATUS_PENDING,
)
from app.games.zone_stalkers.decision.models.objective import ObjectiveGenerationContext
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_QUESTION_WITNESSES,
    STEP_SEARCH_TARGET,
    STEP_LOOK_FOR_TRACKS,
)
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.objectives.generator import (
    OBJECTIVE_ENGAGE_TARGET,
    generate_objectives,
)
from app.games.zone_stalkers.decision.target_beliefs import (
    POSSIBLE_LOCATION_MIN_CONFIDENCE,
    RECENT_TARGET_CONTACT_TURNS,
    build_target_belief,
)
from app.games.zone_stalkers.memory.models import MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3
from app.games.zone_stalkers.rules.tick_rules import _add_memory
from tests.decision.conftest import make_agent, make_minimal_state


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_target(
    agent_id: str = "target_1",
    location_id: str = "loc_a",
    is_alive: bool = True,
) -> dict[str, Any]:
    return {
        "archetype": "stalker_agent",
        "is_alive": is_alive,
        "location_id": location_id,
        "hp": 100,
        "name": "Victim",
        "id": agent_id,
        "equipment": {"weapon": {"type": "pistol", "value": 200}},
    }


def _make_state_with_target(
    agent: dict[str, Any],
    agent_id: str = "bot1",
    target_agent_id: str = "target_1",
    target_location_id: str = "loc_a",
    target_alive: bool = True,
) -> dict[str, Any]:
    state = make_minimal_state(agent_id=agent_id, agent=agent)
    state["agents"][target_agent_id] = _make_target(
        agent_id=target_agent_id,
        location_id=target_location_id,
        is_alive=target_alive,
    )
    return state


def _noop_add_memory(*args: Any, **kwargs: Any) -> None:
    pass


def _build_ctx(agent: dict, state: dict, agent_id: str = "bot1") -> ObjectiveGenerationContext:
    ctx = build_agent_context(agent_id, agent, state)
    belief = build_belief_state(ctx, agent, state["world_turn"])
    need_result = evaluate_need_result(ctx, state)
    target_belief = build_target_belief(
        agent_id=agent_id, agent=agent, state=state,
        world_turn=state["world_turn"], belief_state=belief,
    )
    return ObjectiveGenerationContext(
        agent_id=agent_id,
        world_turn=state["world_turn"],
        belief_state=belief,
        need_result=need_result,
        active_plan_summary=None,
        personality=agent,
        target_belief=target_belief,
    )


def _make_verify_lead_plan(world_turn: int) -> ActivePlanV3:
    return ActivePlanV3(
        objective_key="VERIFY_LEAD",
        status=ACTIVE_PLAN_STATUS_ACTIVE,
        created_turn=world_turn,
        updated_turn=world_turn,
        steps=[
            ActivePlanStep(
                kind=STEP_SEARCH_TARGET,
                payload={"target_id": "target_1", "target_location_id": "loc_a"},
                status=STEP_STATUS_PENDING,
            ),
            ActivePlanStep(
                kind=STEP_LOOK_FOR_TRACKS,
                payload={"target_id": "target_1"},
                status=STEP_STATUS_PENDING,
            ),
            ActivePlanStep(
                kind=STEP_QUESTION_WITNESSES,
                payload={"target_id": "target_1"},
                status=STEP_STATUS_PENDING,
            ),
        ],
        current_step_index=0,
        source_refs=["test"],
        memory_refs=[],
    )


def _add_target_seen_memory(
    agent: dict,
    target_id: str,
    location_id: str,
    created_turn: int,
) -> None:
    ensure_memory_v3(agent)
    rec = MemoryRecord(
        id=str(uuid.uuid4()),
        agent_id="bot1",
        layer="spatial",
        kind="target_seen",
        created_turn=created_turn,
        last_accessed_turn=None,
        summary=f"Seen {target_id} at {location_id}",
        details={"target_id": target_id, "location_id": location_id},
        location_id=location_id,
        confidence=0.9,
    )
    add_memory_record(agent, rec)


# ---------------------------------------------------------------------------
# Fix 2: VERIFY_LEAD completes early when search_target finds target
# ---------------------------------------------------------------------------

class TestVerifyLeadEarlyCompletion:

    def test_verify_lead_completes_early_when_target_found(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = _make_state_with_target(agent, target_location_id="loc_a")
        world_turn = state["world_turn"]

        active_plan = _make_verify_lead_plan(world_turn)
        save_active_plan(agent, active_plan)

        start_or_continue_active_plan_step(
            "bot1", agent, active_plan, state, world_turn,
            add_memory=_noop_add_memory,
        )

        assert get_active_plan(agent) is None, (
            "VERIFY_LEAD plan should be cleared after search_target finds target"
        )

    def test_verify_lead_stays_active_when_target_absent(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = _make_state_with_target(agent, target_location_id="loc_b")
        world_turn = state["world_turn"]

        active_plan = _make_verify_lead_plan(world_turn)
        save_active_plan(agent, active_plan)

        start_or_continue_active_plan_step(
            "bot1", agent, active_plan, state, world_turn,
            add_memory=_noop_add_memory,
        )

        remaining_plan = get_active_plan(agent)
        assert remaining_plan is not None, (
            "VERIFY_LEAD plan should stay active when target is not found"
        )


# ---------------------------------------------------------------------------
# Fix 3: ENGAGE_TARGET for recently seen target
# ---------------------------------------------------------------------------

class TestEngageTargetForRecentlySeen:

    def test_recent_target_seen_generates_engage_target(self) -> None:
        world_turn = 100
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1",
            location_id="loc_a", thirst=50,
        )
        state = _make_state_with_target(agent, target_location_id="loc_b")
        state["world_turn"] = world_turn
        state["debug_omniscient_targets"] = False

        _add_target_seen_memory(
            agent, "target_1", "loc_a", created_turn=world_turn - 3
        )

        ctx = _build_ctx(agent, state)
        objectives = generate_objectives(ctx)
        keys = [o.key for o in objectives]
        assert OBJECTIVE_ENGAGE_TARGET in keys, (
            f"Expected ENGAGE_TARGET for recently-seen target, got: {keys}"
        )

    def test_stale_target_seen_recently_seen_is_false(self) -> None:
        world_turn = 100
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1",
            location_id="loc_a", thirst=30,
        )
        state = _make_state_with_target(agent, target_location_id="loc_b")
        state["world_turn"] = world_turn
        state["debug_omniscient_targets"] = False

        _add_target_seen_memory(
            agent, "target_1", "loc_a",
            created_turn=world_turn - (RECENT_TARGET_CONTACT_TURNS + 5)
        )

        ctx = _build_ctx(agent, state)
        tb = ctx.target_belief
        assert tb is None or not tb.recently_seen, (
            "recently_seen should be False for stale target_seen memory"
        )


# ---------------------------------------------------------------------------
# Fix 3 scoring: ENGAGE_TARGET beats soft thirst
# ---------------------------------------------------------------------------

class TestEngageTargetBeatsThirst:

    def test_engage_target_beats_thirst_60(self) -> None:
        from app.games.zone_stalkers.decision.objectives.selection import choose_objective

        world_turn = 100
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1",
            location_id="loc_a", thirst=60,
        )
        state = _make_state_with_target(agent, target_location_id="loc_b")
        state["world_turn"] = world_turn
        state["debug_omniscient_targets"] = False

        _add_target_seen_memory(agent, "target_1", "loc_a", created_turn=world_turn - 2)

        ctx = _build_ctx(agent, state)
        objectives = generate_objectives(ctx)
        keys = [o.key for o in objectives]

        assert OBJECTIVE_ENGAGE_TARGET in keys, (
            f"Expected ENGAGE_TARGET with thirst=60 and recently seen target, got: {keys}"
        )

        decision = choose_objective(objectives, agent)
        assert decision is not None
        chosen_key = decision.selected.key
        assert chosen_key == OBJECTIVE_ENGAGE_TARGET, (
            f"ENGAGE_TARGET should win over RESTORE_WATER at thirst=60, chose: {chosen_key}"
        )


# ---------------------------------------------------------------------------
# Fix 6: no_witnesses writes witness_source_exhausted
# ---------------------------------------------------------------------------

class TestWitnessSourceExhaustion:

    def test_no_witnesses_writes_exhausted_memory(self) -> None:
        from app.games.zone_stalkers.decision.executors import _exec_question_witnesses

        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = make_minimal_state(agent=agent)
        world_turn = state["world_turn"]

        ctx = build_agent_context("bot1", agent, state)
        step = PlanStep(
            kind=STEP_QUESTION_WITNESSES,
            payload={"target_id": "target_1"},
        )

        _exec_question_witnesses("bot1", agent, step, ctx, state, world_turn)

        action_kinds = [m["effects"].get("action_kind") for m in agent.get("memory", [])]
        assert "witness_source_exhausted" in action_kinds, (
            f"Expected witness_source_exhausted in memory, got: {action_kinds}"
        )

        exhausted_mem = next(
            m for m in agent["memory"]
            if m["effects"].get("action_kind") == "witness_source_exhausted"
        )
        effects = exhausted_mem["effects"]
        assert effects["target_id"] == "target_1"
        assert effects["location_id"] == "loc_a"
        cooldown = effects.get("cooldown_until_turn")
        assert isinstance(cooldown, int) and cooldown > world_turn


# ---------------------------------------------------------------------------
# Fix 8: Zero-confidence possible_locations excluded
# ---------------------------------------------------------------------------

class TestZeroConfidenceFiltering:

    def test_zero_confidence_location_excluded(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = make_minimal_state(agent=agent)
        state["agents"]["target_1"] = _make_target(location_id="loc_b")
        state["debug_omniscient_targets"] = False

        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="spatial",
            kind="target_last_known_location",
            created_turn=95,
            last_accessed_turn=None,
            summary="Rumour",
            details={"target_id": "target_1", "location_id": "phantom_loc"},
            location_id="phantom_loc",
            confidence=0.01,
        )
        add_memory_record(agent, rec)

        ctx = build_agent_context("bot1", agent, state)
        belief = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief,
        )

        loc_ids = [pl.location_id for pl in tb.possible_locations]
        assert "phantom_loc" not in loc_ids, (
            f"phantom_loc with confidence=0.01 should be excluded, got: {loc_ids}"
        )

    def test_high_confidence_location_kept(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = make_minimal_state(agent=agent)
        state["agents"]["target_1"] = _make_target(location_id="loc_b")
        state["debug_omniscient_targets"] = False

        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="spatial",
            kind="target_last_known_location",
            created_turn=98,
            last_accessed_turn=None,
            summary="Reliable lead",
            details={"target_id": "target_1", "location_id": "loc_b"},
            location_id="loc_b",
            confidence=0.75,
        )
        add_memory_record(agent, rec)

        ctx = build_agent_context("bot1", agent, state)
        belief = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief,
        )

        loc_ids = [pl.location_id for pl in tb.possible_locations]
        assert "loc_b" in loc_ids, (
            f"loc_b with confidence=0.75 should be in possible_locations, got: {loc_ids}"
        )


# ---------------------------------------------------------------------------
# Fix 9: Route hints ignore exhausted destinations
# ---------------------------------------------------------------------------

class TestRouteHintsIgnoreExhausted:

    def _add_target_loc_memory(
        self, agent: dict, state: dict, target_id: str, location_id: str
    ) -> None:
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="spatial",
            kind="target_last_known_location",
            created_turn=state["world_turn"] - 2,
            last_accessed_turn=None,
            summary=f"Target at {location_id}",
            details={"target_id": target_id, "location_id": location_id},
            location_id=location_id,
            confidence=0.8,
        )
        add_memory_record(agent, rec)

    def _exhaust_location(
        self, agent: dict, target_id: str, location_id: str, world_turn: int
    ) -> None:
        _add_memory(
            agent, world_turn, {},
            "observation", "Exhausted",
            {
                "action_kind": "witness_source_exhausted",
                "target_id": target_id,
                "location_id": location_id,
                "source_kind": "location_witnesses",
                "cooldown_until_turn": world_turn + 180,
            },
            summary=f"Exhausted witnesses at {location_id}.",
        )

    def test_exhausted_location_excluded_from_route_hints(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = make_minimal_state(agent=agent)
        state["agents"]["target_1"] = _make_target(location_id="loc_b")
        state["debug_omniscient_targets"] = False
        world_turn = state["world_turn"]

        self._add_target_loc_memory(agent, state, "target_1", "loc_b")
        self._exhaust_location(agent, "target_1", "loc_b", world_turn)

        ctx = build_agent_context("bot1", agent, state)
        belief = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=world_turn, belief_state=belief,
        )

        route_dest_ids = [rh.to_location_id for rh in tb.route_hints]
        assert "loc_b" not in route_dest_ids, (
            f"Exhausted loc_b should not appear in route_hints, got: {route_dest_ids}"
        )

    def test_non_exhausted_location_in_route_or_possible(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = make_minimal_state(agent=agent)
        state["agents"]["target_1"] = _make_target(location_id="loc_b")
        state["debug_omniscient_targets"] = False
        world_turn = state["world_turn"]

        self._add_target_loc_memory(agent, state, "target_1", "loc_b")

        ctx = build_agent_context("bot1", agent, state)
        belief = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=world_turn, belief_state=belief,
        )

        route_dest_ids = [rh.to_location_id for rh in tb.route_hints]
        loc_ids = [pl.location_id for pl in tb.possible_locations]
        has_loc_b = "loc_b" in route_dest_ids or "loc_b" in loc_ids
        assert has_loc_b, (
            f"Non-exhausted loc_b should appear in route_hints or possible_locations. "
            f"route_hints={route_dest_ids}, possible_locations={loc_ids}"
        )

    def test_route_hint_to_target_not_found_exhausted_destination_is_removed(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a"
        )
        state = make_minimal_state(agent=agent)
        state["agents"]["target_1"] = _make_target(location_id="loc_c")
        state["debug_omniscient_targets"] = False
        world_turn = state["world_turn"]

        ensure_memory_v3(agent)
        route_record = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="spatial",
            kind="target_route_observed",
            created_turn=world_turn - 2,
            last_accessed_turn=None,
            summary="Route loc_a -> loc_b",
            details={
                "target_id": "target_1",
                "from_location_id": "loc_a",
                "to_location_id": "loc_b",
            },
            location_id="loc_b",
            confidence=0.9,
        )
        add_memory_record(agent, route_record)

        exhausted_record = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="spatial",
            kind="target_not_found",
            created_turn=world_turn - 1,
            last_accessed_turn=None,
            summary="Target not found at loc_b",
            details={
                "target_id": "target_1",
                "location_id": "loc_b",
                "failed_search_count": 3,
                "cooldown_until_turn": world_turn + 180,
            },
            location_id="loc_b",
            confidence=0.95,
        )
        add_memory_record(agent, exhausted_record)

        ctx = build_agent_context("bot1", agent, state)
        belief = build_belief_state(ctx, agent, world_turn)
        tb = build_target_belief(
            agent_id="bot1",
            agent=agent,
            state=state,
            world_turn=world_turn,
            belief_state=belief,
        )

        route_dest_ids = [rh.to_location_id for rh in tb.route_hints]
        assert "loc_b" not in route_dest_ids, (
            f"Route hint destination loc_b should be excluded when exhausted, got: {route_dest_ids}"
        )
        assert "loc_b" in tb.exhausted_locations
        routed_to_loc_b = [route for route in tb.likely_routes if route.to_location_id == "loc_b"]
        assert all(route.confidence <= 0.1 for route in routed_to_loc_b), (
            f"Routes to exhausted loc_b should be dropped or heavily penalized, got: {routed_to_loc_b}"
        )
