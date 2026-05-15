"""Tests for the hunt/kill-NPC goal implementation (PR5, Stage 2).

Covers:
  - TargetBelief construction with all permutations of input data.
  - Objective generation for all hunt stages (LOCATE, TRACK, ENGAGE, CONFIRM, PREPARE).
  - Planner step sequences for each hunt stage objective.
  - Executor side-effects: search_target, start_combat, confirm_kill.
  - Global goal completion: kill_stalker → global_goal_achieved=True.
  - brain_v3_context carries hunt_target_belief snapshot.
"""
from __future__ import annotations

from typing import Any
from dataclasses import replace

import pytest

from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.objective import ObjectiveGenerationContext
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_ASK_FOR_INTEL,
    STEP_CONFIRM_KILL,
    STEP_LOOK_FOR_TRACKS,
    STEP_MONITOR_COMBAT,
    STEP_QUESTION_WITNESSES,
    STEP_SEARCH_TARGET,
    STEP_START_COMBAT,
    STEP_TRAVEL_TO_LOCATION,
    STEP_WAIT,
)
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.objectives.generator import (
    OBJECTIVE_CONFIRM_KILL,
    OBJECTIVE_ENGAGE_TARGET,
    OBJECTIVE_GATHER_INTEL,
    OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
    OBJECTIVE_HUNT_TARGET,
    OBJECTIVE_LOCATE_TARGET,
    OBJECTIVE_PREPARE_FOR_HUNT,
    OBJECTIVE_TRACK_TARGET,
    OBJECTIVE_VERIFY_LEAD,
    generate_objectives,
)
from app.games.zone_stalkers.decision.objectives.selection import choose_objective
from app.games.zone_stalkers.decision.planner import build_plan
from app.games.zone_stalkers.decision.target_beliefs import build_target_belief
from app.games.zone_stalkers.memory.models import LAYER_SOCIAL, MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record, ensure_memory_v3
from tests.decision.conftest import make_agent, make_minimal_state
from tests.decision.v3.memory_assertions import v3_records


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_target(
    *,
    agent_id: str = "target_1",
    location_id: str = "loc_b",
    hp: int = 100,
    is_alive: bool = True,
    has_weapon: bool = True,
) -> dict[str, Any]:
    return {
        "archetype": "stalker_agent",
        "is_alive": is_alive,
        "location_id": location_id,
        "hp": hp,
        "name": "Жертва",
        "id": agent_id,
        "equipment": {
            "weapon": {"type": "pistol", "value": 200} if has_weapon else None,
        },
    }


def _make_state_with_target(
    *,
    agent: dict[str, Any],
    agent_id: str = "bot1",
    target_agent_id: str = "target_1",
    target_location_id: str = "loc_b",
    target_hp: int = 100,
    target_alive: bool = True,
    extra_locations: dict | None = None,
) -> dict[str, Any]:
    state = make_minimal_state(agent_id=agent_id, agent=agent)
    state["agents"][target_agent_id] = _make_target(
        agent_id=target_agent_id,
        location_id=target_location_id,
        hp=target_hp,
        is_alive=target_alive,
    )
    if extra_locations:
        state["locations"].update(extra_locations)
    return state


def _make_ctx(agent: dict, state: dict, agent_id: str = "bot1") -> ObjectiveGenerationContext:
    from app.games.zone_stalkers.decision.target_beliefs import build_target_belief
    ctx = build_agent_context(agent_id, agent, state)
    belief = build_belief_state(ctx, agent, state["world_turn"])
    need_result = evaluate_need_result(ctx, state)
    target_belief = build_target_belief(
        agent_id=agent_id, agent=agent, state=state,
        world_turn=state["world_turn"], belief_state=belief
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


def _remember_target_location(agent: dict, state: dict, *, target_id: str, location_id: str) -> None:
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    _add_memory(
        agent,
        state["world_turn"],
        state,
        "observation",
        "📍 Известна локация цели",
        {"action_kind": "target_last_known_location", "target_id": target_id, "location_id": location_id},
        summary=f"Цель замечена в {location_id}",
        agent_id="bot1",
    )


def _v3r(agent: dict[str, Any]) -> list[dict[str, Any]]:
    return v3_records(agent)


def _v3_ak(record: dict[str, Any]) -> str | None:
    return record.get("kind") or (record.get("details") or {}).get("action_kind")


def _v3_fx(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("details") or {}


# ─────────────────────────────────────────────────────────────────────────────
# TargetBelief construction
# ─────────────────────────────────────────────────────────────────────────────

class TestTargetBeliefConstruction:
    def test_empty_target_id_returns_unknown_belief(self) -> None:
        agent = make_agent(global_goal="get_rich")
        state = make_minimal_state(agent=agent)
        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        assert not tb.is_known
        assert tb.target_id == ""
        assert tb.last_known_location_id is None

    def test_target_in_different_location(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        state["debug_omniscient_targets"] = True
        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        assert tb.is_known
        assert not tb.visible_now
        assert not tb.co_located
        assert tb.last_known_location_id == "loc_b"
        assert tb.is_alive is True

    def test_target_unknown_without_visibility_or_memory_when_omniscience_disabled(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        state["debug_omniscient_targets"] = False
        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        assert tb.visible_now is False
        assert tb.co_located is False
        assert tb.last_known_location_id is None

    def test_target_dead_in_state(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=False)
        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        assert tb.is_alive is False

    def test_target_memory_v3_record_sets_last_known_location(self) -> None:
        import uuid

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = make_minimal_state(agent=agent)
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="spatial",
            kind="target_last_known_location",
            created_turn=90,
            last_accessed_turn=None,
            summary="Видел цель в loc_b",
            details={"target_id": "target_1", "location_id": "loc_b"},
            location_id="loc_b",
            confidence=0.8,
        )
        add_memory_record(agent, rec)

        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        assert tb.last_known_location_id == "loc_b"
        assert tb.location_confidence >= 0.7
        assert any("memory:" in s for s in tb.source_refs)

    def test_target_belief_reads_target_intel_location(self) -> None:
        import uuid

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = make_minimal_state(agent=agent)
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="target_intel",
            created_turn=95,
            last_accessed_turn=None,
            summary="Разведданные о цели",
            details={"target_agent_id": "target_1", "location_id": "loc_target"},
            location_id="loc_target",
            confidence=0.69,
        )
        add_memory_record(agent, rec)

        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        assert tb.last_known_location_id == "loc_target"
        assert tb.location_confidence == pytest.approx(0.69, abs=0.01)
        assert f"memory:{rec.id}" in tb.source_refs

    def test_target_belief_reads_legacy_intel_from_trader_alias(self) -> None:
        import uuid

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = make_minimal_state(agent=agent)
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="intel_from_trader",
            created_turn=95,
            last_accessed_turn=None,
            summary="Торговец подсказал, где цель",
            details={"target_agent_id": "target_1", "location_id": "loc_target", "source_agent_id": "trader_1"},
            location_id="loc_target",
            confidence=0.69,
        )
        add_memory_record(agent, rec)

        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        assert tb.last_known_location_id == "loc_target"
        assert f"memory:{rec.id}" in tb.source_refs

    def test_target_death_confirmed_memory_overrides_state_alive(self) -> None:
        import uuid

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=True)
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="threat",
            kind="target_death_confirmed",
            created_turn=95,
            last_accessed_turn=None,
            summary="Цель мертва",
            details={"target_id": "target_1", "killer_id": "bot1"},
            confidence=1.0,
        )
        add_memory_record(agent, rec)

        ctx = build_agent_context("bot1", agent, state)
        belief_state = build_belief_state(ctx, agent, state["world_turn"])
        tb = build_target_belief(
            agent_id="bot1", agent=agent, state=state,
            world_turn=state["world_turn"], belief_state=belief_state,
        )
        # Memory mark "target_death_confirmed" must override state-derived alive=True
        assert tb.is_alive is False


# ─────────────────────────────────────────────────────────────────────────────
# Objective generation — hunt stage selection
# ─────────────────────────────────────────────────────────────────────────────

class TestHuntObjectiveGeneration:
    def test_locate_stage_when_no_target_location_known(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = make_minimal_state(agent=agent)  # no target in agents dict
        objectives = generate_objectives(_make_ctx(agent, state))
        keys = {o.key for o in objectives}
        assert OBJECTIVE_LOCATE_TARGET in keys
        assert OBJECTIVE_GATHER_INTEL in keys
        assert OBJECTIVE_HUNT_TARGET in keys

    def test_locate_stage_generates_prepare_when_no_weapon(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", has_weapon=False)
        state = make_minimal_state(agent=agent)
        objectives = generate_objectives(_make_ctx(agent, state))
        objective_map = {o.key: o for o in objectives}
        assert OBJECTIVE_GET_MONEY_FOR_RESUPPLY in objective_map
        assert OBJECTIVE_PREPARE_FOR_HUNT in objective_map
        prepare = objective_map[OBJECTIVE_PREPARE_FOR_HUNT]
        blockers = prepare.metadata.get("blockers", [])
        assert any(b["key"] == "no_weapon" for b in blockers)

    def test_killer_without_min_equipment_prefers_get_money_for_hunt(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker",
            kill_target_id="target_1",
            has_weapon=False,
            money=0,
        )
        state = make_minimal_state(agent=agent)
        objectives = generate_objectives(_make_ctx(agent, state))
        decision = choose_objective(objectives, personality=agent)

        assert any(obj.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY for obj in objectives)
        assert decision.selected.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY

    def test_no_weapon_no_money_get_money_beats_resupply_weapon(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker",
            kill_target_id="target_1",
            has_weapon=False,
            money=0,
            inventory=[],
        )
        state = make_minimal_state(agent=agent)
        objectives = generate_objectives(_make_ctx(agent, state))
        decision = choose_objective(objectives, personality=agent)

        assert any(obj.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY for obj in objectives)
        assert decision.selected.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY

    def test_killer_without_actionable_intel_and_no_money_prefers_get_money_for_hunt(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker",
            kill_target_id="target_1",
            money=0,
        )
        state = make_minimal_state(agent=agent)
        objectives = generate_objectives(_make_ctx(agent, state))
        decision = choose_objective(objectives, personality=agent)

        assert any(obj.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY for obj in objectives)
        assert decision.selected.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY

    def test_track_stage_when_location_known_but_not_co_located(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        _remember_target_location(agent, state, target_id="target_1", location_id="loc_b")
        objectives = generate_objectives(_make_ctx(agent, state))
        keys = {o.key for o in objectives}
        assert OBJECTIVE_VERIFY_LEAD in keys
        assert OBJECTIVE_TRACK_TARGET in keys

    def test_after_buying_intel_next_objective_is_track_target(self) -> None:
        import uuid

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = make_minimal_state(agent=agent)
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="target_intel",
            created_turn=95,
            last_accessed_turn=None,
            summary="Купил intel о цели",
            details={"target_agent_id": "target_1", "location_id": "loc_b", "source_agent_id": "trader_1"},
            location_id="loc_b",
            confidence=0.69,
        )
        add_memory_record(agent, rec)

        objectives = generate_objectives(_make_ctx(agent, state))
        objective_keys = {objective.key for objective in objectives}
        assert OBJECTIVE_VERIFY_LEAD in objective_keys
        assert OBJECTIVE_TRACK_TARGET in objective_keys
        decision = choose_objective(objectives, personality=agent)
        assert decision.selected.key in {OBJECTIVE_VERIFY_LEAD, OBJECTIVE_TRACK_TARGET}

    def test_target_intel_without_last_seen_turn_but_actionable_location_does_not_force_get_money(self) -> None:
        import uuid

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = make_minimal_state(agent=agent)
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer=LAYER_SOCIAL,
            kind="target_intel",
            created_turn=95,
            last_accessed_turn=None,
            summary="Есть свежий intel о цели",
            details={"target_agent_id": "target_1", "location_id": "loc_b", "source_agent_id": "trader_1"},
            location_id="loc_b",
            confidence=0.69,
        )
        add_memory_record(agent, rec)

        ctx = _make_ctx(agent, state)
        assert ctx.target_belief is not None
        ctx = ObjectiveGenerationContext(
            agent_id=ctx.agent_id,
            world_turn=ctx.world_turn,
            belief_state=ctx.belief_state,
            need_result=ctx.need_result,
            active_plan_summary=ctx.active_plan_summary,
            personality=ctx.personality,
            target_belief=replace(ctx.target_belief, last_seen_turn=None),
        )

        objectives = generate_objectives(ctx)
        decision = choose_objective(objectives, personality=agent)
        assert decision.selected.key in {OBJECTIVE_VERIFY_LEAD, OBJECTIVE_TRACK_TARGET}

    def test_track_stage_hunt_stage_metadata(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        _remember_target_location(agent, state, target_id="target_1", location_id="loc_b")
        objectives = generate_objectives(_make_ctx(agent, state))
        track_obj = next(o for o in objectives if o.key == OBJECTIVE_TRACK_TARGET)
        assert track_obj.metadata.get("hunt_stage") == "track"
        assert track_obj.metadata.get("target_id") == "target_1"

    def test_engage_stage_when_target_co_located_and_ready(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        ctx = _make_ctx(agent, state)
        assert ctx.target_belief is not None
        assert ctx.target_belief.visible_now is True
        assert ctx.target_belief.co_located is True
        assert ctx.target_belief.best_location_id == "loc_a"
        objectives = generate_objectives(ctx)
        keys = {o.key for o in objectives}
        assert OBJECTIVE_ENGAGE_TARGET in keys

    def test_visible_kill_target_beats_normal_resupply(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker",
            kill_target_id="target_1",
            location_id="loc_a",
            money=0,
            has_weapon=True,
            has_armor=True,
            has_ammo=True,
        )
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        objectives = generate_objectives(_make_ctx(agent, state))
        decision = choose_objective(objectives, personality=agent)
        assert decision.selected.key == OBJECTIVE_ENGAGE_TARGET

    def test_equipment_disadvantage_is_advisory_for_visible_ready_target(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker",
            kill_target_id="target_1",
            location_id="loc_a",
            has_weapon=True,
            has_armor=True,
            has_ammo=True,
        )
        agent["knowledge_v1"] = {
            "known_npcs": {
                "target_1": {
                    "is_alive": True,
                    "confidence": 0.9,
                    "last_seen_turn": 95,
                    "equipment_summary": {
                        "weapon_class": "rifle",
                        "armor_class": "medium",
                        "combat_strength_estimate": 0.9,
                    },
                }
            }
        }
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        objectives = generate_objectives(_make_ctx(agent, state))
        objective_keys = {objective.key for objective in objectives}
        decision = choose_objective(objectives, personality=agent)

        assert OBJECTIVE_ENGAGE_TARGET in objective_keys
        assert decision.selected.key == OBJECTIVE_ENGAGE_TARGET

    def test_critical_survival_can_override_visible_kill_target(self) -> None:
        agent = make_agent(
            global_goal="kill_stalker",
            kill_target_id="target_1",
            location_id="loc_a",
            thirst=100,
        )
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        objectives = generate_objectives(_make_ctx(agent, state))
        decision = choose_objective(objectives, personality=agent)
        assert decision.selected.key != OBJECTIVE_ENGAGE_TARGET

    def test_engage_blocked_by_no_weapon_when_co_located(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1",
                           location_id="loc_a", has_weapon=False)
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        objectives = generate_objectives(_make_ctx(agent, state))
        keys = {o.key for o in objectives}
        # Should not engage without weapon — should prepare instead
        assert OBJECTIVE_ENGAGE_TARGET not in keys
        assert OBJECTIVE_PREPARE_FOR_HUNT in keys

    def test_confirm_kill_stage_when_target_believed_dead(self) -> None:
        from app.games.zone_stalkers.memory.store import ensure_memory_v3, add_memory_record
        from app.games.zone_stalkers.memory.models import MemoryRecord
        import uuid

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=False)
        ensure_memory_v3(agent)
        rec = MemoryRecord(
            id=str(uuid.uuid4()),
            agent_id="bot1",
            layer="threat",
            kind="target_death_confirmed",
            created_turn=90,
            last_accessed_turn=None,
            summary="Цель мертва",
            details={"target_id": "target_1", "corpse_location_id": "loc_b", "location_id": "loc_b"},
            confidence=1.0,
        )
        add_memory_record(agent, rec)

        objectives = generate_objectives(_make_ctx(agent, state))
        keys = {o.key for o in objectives}
        assert OBJECTIVE_GATHER_INTEL in keys or OBJECTIVE_CONFIRM_KILL in keys

    def test_prepare_track_both_generated_when_location_known_but_blockers(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1",
                           location_id="loc_a", has_weapon=False)
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        _remember_target_location(agent, state, target_id="target_1", location_id="loc_b")
        objectives = generate_objectives(_make_ctx(agent, state))
        keys = {o.key for o in objectives}
        assert OBJECTIVE_TRACK_TARGET in keys
        assert OBJECTIVE_PREPARE_FOR_HUNT in keys


# ─────────────────────────────────────────────────────────────────────────────
# Planner step sequences for hunt objectives
# ─────────────────────────────────────────────────────────────────────────────

class TestHuntPlannerSteps:
    def _build_hunt_plan(
        self,
        objective_key: str,
        agent: dict[str, Any],
        state: dict[str, Any],
        target_loc: str | None = None,
        agent_id: str = "bot1",
    ):
        from app.games.zone_stalkers.decision.objectives.intent_adapter import objective_to_intent
        from app.games.zone_stalkers.decision.objectives.generator import Objective
        from app.games.zone_stalkers.decision.models.objective import ObjectiveScore
        ctx = build_agent_context(agent_id, agent, state)
        belief = build_belief_state(ctx, agent, state["world_turn"])
        need_result = evaluate_need_result(ctx, state)
        score = ObjectiveScore(
            objective_key=objective_key, raw_score=0.8, final_score=0.8,
            factors=(), penalties=(), decision="selected",
        )
        obj = Objective(
            key=objective_key, source="global_goal", urgency=0.8,
            expected_value=0.9, risk=0.3, time_cost=0.3, resource_cost=0.1,
            confidence=0.8, goal_alignment=1.0, memory_confidence=0.5,
            reasons=("test",), source_refs=(),
            metadata={
                "hunt_stage": objective_key.lower(),
                "target_id": agent.get("kill_target_id"),
                "target_location_id": target_loc,
                "objective_key": objective_key,
            },
            target={"target_id": agent.get("kill_target_id"), "location_id": target_loc},
        )
        intent = objective_to_intent(obj, score, world_turn=state["world_turn"])
        return build_plan(ctx, intent, state, state["world_turn"], need_result=need_result)

    def test_locate_plan_includes_ask_for_intel(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = make_minimal_state(agent=agent)
        plan = self._build_hunt_plan("LOCATE_TARGET", agent, state)
        assert plan is not None
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_QUESTION_WITNESSES in step_kinds or STEP_ASK_FOR_INTEL in step_kinds

    def test_gather_intel_exhausted_hub_expands_search_instead_of_wait(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = make_minimal_state(agent=agent)
        state["agents"]["target_1"] = _make_target(location_id="loc_b")
        ensure_memory_v3(agent)
        agent["memory_v3"]["records"] = {
            "exhausted_here": {
                "id": "exhausted_here",
                "agent_id": "bot1",
                "layer": "observation",
                "kind": "witness_source_exhausted",
                "created_turn": state["world_turn"],
                "details": {
                    "action_kind": "witness_source_exhausted",
                    "target_id": "target_1",
                    "location_id": "loc_a",
                    "cooldown_until_turn": state["world_turn"] + 180,
                },
            }
        }

        plan = self._build_hunt_plan("GATHER_INTEL", agent, state)
        assert plan is not None
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_WAIT not in step_kinds
        assert STEP_TRAVEL_TO_LOCATION in step_kinds or STEP_LOOK_FOR_TRACKS in step_kinds

    def test_track_plan_with_known_location_includes_travel_and_search(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        plan = self._build_hunt_plan("TRACK_TARGET", agent, state, target_loc="loc_b")
        assert plan is not None
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_TRAVEL_TO_LOCATION in step_kinds
        assert STEP_SEARCH_TARGET in step_kinds
        assert STEP_LOOK_FOR_TRACKS in step_kinds

    def test_engage_plan_at_target_location_includes_combat_monitor_confirm(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        plan = self._build_hunt_plan("ENGAGE_TARGET", agent, state, target_loc="loc_a")
        assert plan is not None
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_START_COMBAT in step_kinds
        assert STEP_MONITOR_COMBAT in step_kinds
        assert STEP_CONFIRM_KILL in step_kinds

    def test_engage_plan_remote_target_includes_travel_combat_monitor_confirm(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        plan = self._build_hunt_plan("ENGAGE_TARGET", agent, state, target_loc="loc_b")
        assert plan is not None
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_TRAVEL_TO_LOCATION in step_kinds
        assert STEP_START_COMBAT in step_kinds
        assert STEP_MONITOR_COMBAT in step_kinds
        assert STEP_CONFIRM_KILL in step_kinds

    def test_confirm_kill_plan_remote_target_includes_travel_and_confirm(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b", target_alive=False)
        plan = self._build_hunt_plan("CONFIRM_KILL", agent, state, target_loc="loc_b")
        assert plan is not None
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_TRAVEL_TO_LOCATION in step_kinds
        assert STEP_CONFIRM_KILL in step_kinds

    def test_confirm_kill_plan_local_target_is_confirm_only(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a", target_alive=False)
        plan = self._build_hunt_plan("CONFIRM_KILL", agent, state, target_loc="loc_a")
        assert plan is not None
        step_kinds = [s.kind for s in plan.steps]
        assert STEP_CONFIRM_KILL in step_kinds
        assert STEP_TRAVEL_TO_LOCATION not in step_kinds


# ─────────────────────────────────────────────────────────────────────────────
# Executor side-effects
# ─────────────────────────────────────────────────────────────────────────────

class TestHuntExecutors:
    def _run_executor(
        self,
        step_kind: str,
        payload: dict[str, Any],
        agent: dict[str, Any],
        state: dict[str, Any],
        agent_id: str = "bot1",
    ) -> list[dict[str, Any]]:
        from app.games.zone_stalkers.decision.executors import (
            _exec_search_target,
            _exec_start_combat,
            _exec_confirm_kill,
            _exec_monitor_combat,
        )
        from app.games.zone_stalkers.decision.models.plan import PlanStep
        step = PlanStep(kind=step_kind, payload=payload)
        ctx = build_agent_context(agent_id, agent, state)
        dispatch = {
            STEP_SEARCH_TARGET: _exec_search_target,
            STEP_START_COMBAT: _exec_start_combat,
            STEP_CONFIRM_KILL: _exec_confirm_kill,
            STEP_MONITOR_COMBAT: _exec_monitor_combat,
        }
        return dispatch[step_kind](agent_id, agent, step, ctx, state, state["world_turn"])

    def test_search_target_found_writes_target_seen_memory(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        self._run_executor(
            STEP_SEARCH_TARGET, {"target_id": "target_1", "target_location_id": "loc_a"},
            agent, state,
        )
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        # PR10: target_seen is a milestone for the kill target (first encounter).
        assert "target_seen" in memory_kinds
        # PR10: target_last_known_location is a routine refresh → knowledge only, no memory record.
        assert "target_last_known_location" not in memory_kinds
        # Knowledge tables must reflect the sighting regardless.
        known = (agent.get("knowledge_v1") or {}).get("known_npcs", {}).get("target_1", {})
        assert known.get("last_seen_location_id") == "loc_a"

    def test_search_target_not_found_updates_hunt_evidence_without_memory_record(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")  # target elsewhere
        self._run_executor(
            STEP_SEARCH_TARGET, {"target_id": "target_1", "target_location_id": "loc_a"},
            agent, state,
        )
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        # PR10: target_not_found is knowledge-only; no memory_v3 record.
        assert "target_not_found" not in memory_kinds
        # hunt_evidence.failed_search_locations must be updated.
        hunt_ev = ((agent.get("knowledge_v1") or {}).get("hunt_evidence") or {}).get("target_1", {})
        failed = hunt_ev.get("failed_search_locations", {})
        assert failed.get("loc_a", {}).get("count", 0) >= 1, (
            "Expected loc_a in failed_search_locations with count >= 1"
        )

    def test_start_combat_creates_combat_interaction(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        state.setdefault("combat_interactions", {})
        self._run_executor(
            STEP_START_COMBAT, {"target_id": "target_1"},
            agent, state,
        )
        assert len(state["combat_interactions"]) == 1
        cid = list(state["combat_interactions"].keys())[0]
        combat = state["combat_interactions"][cid]
        assert "bot1" in combat["participants"]
        assert "target_1" in combat["participants"]

    def test_start_combat_no_weapon_skips_combat(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1",
                           location_id="loc_a", has_weapon=False)
        state = _make_state_with_target(agent=agent, target_location_id="loc_a")
        state.setdefault("combat_interactions", {})
        self._run_executor(
            STEP_START_COMBAT, {"target_id": "target_1"},
            agent, state,
        )
        # Should skip: no weapon equipped
        assert len(state.get("combat_interactions", {})) == 0

    def test_start_combat_target_not_colocated_writes_target_moved(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        self._run_executor(
            STEP_START_COMBAT, {"target_id": "target_1"},
            agent, state,
        )
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "target_moved" in memory_kinds

    def test_engage_target_does_not_confirm_before_combat_resolves(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a", target_alive=True, target_hp=100)
        plan = Plan(
            intent_kind="hunt_target",
            steps=[
                PlanStep(STEP_START_COMBAT, {"target_id": "target_1"}),
                PlanStep(STEP_MONITOR_COMBAT, {"target_id": "target_1"}),
                PlanStep(STEP_CONFIRM_KILL, {"target_id": "target_1"}),
            ],
        )
        ctx = build_agent_context("bot1", agent, state)
        _ = execute_plan_step(ctx, plan, state, state["world_turn"])
        assert len(state.get("combat_interactions", {})) == 1
        assert plan.current_step_index == 1

        state["world_turn"] += 1
        ctx = build_agent_context("bot1", agent, state)
        _ = execute_plan_step(ctx, plan, state, state["world_turn"])

        assert plan.current_step_index == 1
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "target_death_confirmed" not in memory_kinds

    def test_engage_target_confirms_after_combat_target_dead(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a", target_alive=True, target_hp=100)
        plan = Plan(
            intent_kind="hunt_target",
            steps=[
                PlanStep(STEP_START_COMBAT, {"target_id": "target_1"}),
                PlanStep(STEP_MONITOR_COMBAT, {"target_id": "target_1"}),
                PlanStep(STEP_CONFIRM_KILL, {"target_id": "target_1"}),
            ],
        )
        ctx = build_agent_context("bot1", agent, state)
        _ = execute_plan_step(ctx, plan, state, state["world_turn"])

        target = state["agents"]["target_1"]
        target["is_alive"] = False
        state["locations"]["loc_a"]["corpses"] = [
            {
                "corpse_id": "corpse_target_1_100",
                "agent_id": "target_1",
                "agent_name": "Target 1",
                "location_id": "loc_a",
                "created_turn": state["world_turn"],
                "death_cause": "combat",
                "killer_id": "bot1",
                "visible": True,
                "decay_turn": state["world_turn"] + 7200,
            }
        ]
        for combat in state.get("combat_interactions", {}).values():
            combat["ended"] = True
            combat["ended_turn"] = state["world_turn"]

        state["world_turn"] += 1
        ctx = build_agent_context("bot1", agent, state)
        _ = execute_plan_step(ctx, plan, state, state["world_turn"])
        assert plan.current_step_index == 2

        state["world_turn"] += 1
        ctx = build_agent_context("bot1", agent, state)
        _ = execute_plan_step(ctx, plan, state, state["world_turn"])
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "target_death_confirmed" in memory_kinds

    def test_confirm_kill_dead_target_writes_death_confirmed(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_alive=False)
        state["locations"]["loc_a"]["corpses"] = [
            {
                "corpse_id": "corpse_target_1_100",
                "agent_id": "target_1",
                "agent_name": "Target 1",
                "location_id": "loc_a",
                "created_turn": 100,
                "death_cause": "emission",
                "killer_id": None,
                "visible": True,
                "decay_turn": 1000,
            }
        ]
        self._run_executor(
            STEP_CONFIRM_KILL, {"target_id": "target_1"},
            agent, state,
        )
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "target_death_confirmed" in memory_kinds

    def test_confirm_kill_alive_target_writes_hunt_failed(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_alive=True)
        self._run_executor(
            STEP_CONFIRM_KILL, {"target_id": "target_1"},
            agent, state,
        )
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "hunt_failed" in memory_kinds

    def test_monitor_combat_end_without_kill_writes_replan_marker(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b", target_alive=True)
        state["combat_interactions"] = {
            "combat_1": {
                "id": "combat_1",
                "location_id": "loc_a",
                "ended": True,
                "ended_turn": state["world_turn"],
                "participants": {
                    "bot1": {"enemies": ["target_1"], "fled": False},
                    "target_1": {"enemies": ["bot1"], "fled": True},
                },
            }
        }
        self._run_executor(
            STEP_MONITOR_COMBAT,
            {"target_id": "target_1"},
            agent,
            state,
        )
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "combat_ended_without_kill" in memory_kinds

    def test_search_target_found_writes_combat_strength_memory(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_a", target_hp=60)
        self._run_executor(
            STEP_SEARCH_TARGET, {"target_id": "target_1", "target_location_id": "loc_a"},
            agent, state,
        )
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "target_combat_strength_observed" in memory_kinds
        strength_entry = next(
            r for r in _v3r(agent)
            if _v3_ak(r) == "target_combat_strength_observed"
        )
        assert _v3_fx(strength_entry)["combat_strength"] == pytest.approx(0.6, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# Global goal completion
# ─────────────────────────────────────────────────────────────────────────────

class TestKillStalkerGoalCompletion:
    def test_kill_target_requires_death_confirmation_to_set_goal_achieved(self) -> None:
        # Dead target alone is insufficient — direct confirmation is required.
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=False)
        _check_global_goal_completion("bot1", agent, state, state["world_turn"])
        assert agent.get("global_goal_achieved") is not True

    def test_kill_target_with_personal_evidence_sets_goal_achieved_without_direct_observation(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion, _add_memory

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=False)
        _add_memory(
            agent,
            state["world_turn"],
            state,
            "observation",
            "🎯 Цель ликвидирована",
            {
                "action_kind": "hunt_target_killed",
                "target_id": "target_1",
                "combat_id": "combat_42",
            },
            summary="Я ликвидировал цель в бою.",
        )
        _check_global_goal_completion("bot1", agent, state, state["world_turn"])
        assert agent.get("global_goal_achieved") is True

    def test_target_death_confirmed_sets_kill_goal_achieved(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion, _add_memory

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=False)
        _add_memory(
            agent,
            state["world_turn"],
            state,
            "observation",
            "✅ Подтверждена ликвидация цели",
            {
                "action_kind": "target_death_confirmed",
                "target_id": "target_1",
                "directly_observed": True,
                "confirmation_source": "personal_combat_kill",
            },
            summary="Цель устранена и подтверждена.",
        )
        _check_global_goal_completion("bot1", agent, state, state["world_turn"])
        assert agent.get("global_goal_achieved") is True

    def test_alive_target_does_not_set_global_goal_achieved(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=True)
        _check_global_goal_completion("bot1", agent, state, state["world_turn"])
        assert not agent.get("global_goal_achieved", False)

    def test_kill_completion_writes_goal_achieved_memory(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _check_global_goal_completion, _add_memory

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1")
        state = _make_state_with_target(agent=agent, target_alive=False)
        _add_memory(
            agent,
            state["world_turn"],
            state,
            "observation",
            "✅ Подтверждена ликвидация цели",
            {
                "action_kind": "target_death_confirmed",
                "target_id": "target_1",
                "directly_observed": True,
                "confirmation_source": "personal_combat_kill",
            },
            summary="Цель устранена и подтверждена.",
        )
        _check_global_goal_completion("bot1", agent, state, state["world_turn"])
        memory_kinds = [_v3_ak(r) for r in _v3r(agent)]
        assert "goal_achieved" in memory_kinds

    def test_objective_generation_confirms_kill_after_goal_achieved(self) -> None:
        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1",
                           global_goal_achieved=True)
        state = _make_state_with_target(agent=agent, target_alive=False)
        from app.games.zone_stalkers.decision.objectives.generator import OBJECTIVE_LEAVE_ZONE
        objectives = generate_objectives(_make_ctx(agent, state))
        keys = {o.key for o in objectives}
        assert OBJECTIVE_LEAVE_ZONE in keys


# ─────────────────────────────────────────────────────────────────────────────
# brain_v3_context carries hunt_target_belief snapshot
# ─────────────────────────────────────────────────────────────────────────────

class TestBrainV3ContextHuntBelief:
    def test_brain_v3_context_has_hunt_target_belief_for_kill_stalker(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _run_npc_brain_v3_decision_inner

        agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", location_id="loc_a")
        state = _make_state_with_target(agent=agent, target_location_id="loc_b")
        state["agents"]["bot1"] = agent

        _run_npc_brain_v3_decision_inner("bot1", agent, state, state["world_turn"])
        ctx = agent.get("brain_v3_context", {})
        tb = ctx.get("hunt_target_belief")
        assert tb is not None
        assert tb["target_id"] == "target_1"
        assert tb["is_known"] is True
        assert "last_known_location_id" in tb
        assert "location_confidence" in tb

    def test_brain_v3_context_no_hunt_belief_for_non_kill_goal(self) -> None:
        from app.games.zone_stalkers.rules.tick_rules import _run_npc_brain_v3_decision_inner

        agent = make_agent(global_goal="get_rich", location_id="loc_a")
        state = make_minimal_state(agent=agent)
        state["agents"]["bot1"] = agent

        _run_npc_brain_v3_decision_inner("bot1", agent, state, state["world_turn"])
        ctx = agent.get("brain_v3_context", {})
        assert ctx.get("hunt_target_belief") is None
