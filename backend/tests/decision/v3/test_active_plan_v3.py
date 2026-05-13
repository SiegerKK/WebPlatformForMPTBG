"""Tests for ActivePlan v3 model and active_plan_manager.

Coverage:
- Creating an ActivePlanV3 from an ObjectiveDecision
- Step lifecycle: pending → running → completed
- Long artifact loop scenario
- Emission interruption
- Sleep/recovery continuation
- Resupply then resume
- Failed-plan repair
- confirmed_empty memory integration
- Abort after max repairs
"""
from __future__ import annotations

from typing import Any

import pytest

from app.games.zone_stalkers.decision.models.active_plan import (
    ActivePlanStep,
    ActivePlanV3,
    ACTIVE_PLAN_STATUS_ACTIVE,
    ACTIVE_PLAN_STATUS_ABORTED,
    ACTIVE_PLAN_STATUS_COMPLETED,
    ACTIVE_PLAN_STATUS_FAILED,
    ACTIVE_PLAN_STATUS_REPAIRING,
    MAX_REPAIR_COUNT,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_FAILED,
    STEP_STATUS_PENDING,
    STEP_STATUS_RUNNING,
    STEP_STATUS_SKIPPED,
)
from app.games.zone_stalkers.decision.active_plan_manager import (
    assess_active_plan_v3,
    clear_active_plan,
    create_active_plan,
    get_active_plan,
    repair_active_plan,
    save_active_plan,
    should_replace_active_plan,
)
from app.games.zone_stalkers.decision.models.objective import (
    Objective,
    ObjectiveDecision,
    ObjectiveScore,
)
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3


def _setup_confirmed_empty(agent: dict, location_id: str, world_turn: int = 7) -> None:
    """Write a confirmed_empty (location_empty) record to memory_v3."""
    ensure_memory_v3(agent)
    entry = {
        "world_turn": world_turn,
        "type": "observation",
        "title": f"empty {location_id}",
        "effects": {"action_kind": "explore_confirmed_empty", "location_id": location_id},
    }
    write_memory_event_to_v3(
        agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=world_turn
    )


def _setup_emission_imminent(agent: dict, world_turn: int = 10) -> None:
    """Write an emission_imminent observation to memory_v3."""
    ensure_memory_v3(agent)
    entry = {
        "world_turn": world_turn,
        "type": "observation",
        "title": "emission",
        "effects": {"action_kind": "emission_imminent"},
    }
    write_memory_event_to_v3(
        agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=world_turn
    )


from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_TRAVEL_TO_LOCATION,
    STEP_EXPLORE_LOCATION,
    STEP_SLEEP_FOR_HOURS,
    STEP_TRADE_SELL_ITEM,
    STEP_TRADE_BUY_ITEM,
    STEP_CONSUME_ITEM,
)
from app.games.zone_stalkers.memory.models import MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record


# ── Helpers ───────────────────────────────────────────────────────────────────

def _objective(key: str = "FIND_ARTIFACTS") -> Objective:
    return Objective(
        key=key,
        source="test",
        urgency=0.7,
        expected_value=0.8,
        risk=0.1,
        time_cost=0.3,
        resource_cost=0.1,
        confidence=0.9,
        goal_alignment=0.8,
        memory_confidence=0.9,
        source_refs=("mem-abc", "mem-def"),
    )


def _score(key: str = "FIND_ARTIFACTS") -> ObjectiveScore:
    return ObjectiveScore(
        objective_key=key,
        raw_score=0.8,
        final_score=0.8,
        factors=(),
        penalties=(),
        decision="new_objective",
    )


def _decision(key: str = "FIND_ARTIFACTS") -> ObjectiveDecision:
    obj = _objective(key)
    return ObjectiveDecision(
        selected=obj,
        selected_score=_score(key),
        alternatives=(),
    )


def _plan(*step_kinds: str) -> Plan:
    steps = [
        PlanStep(kind=k, payload={"location_id": f"loc-{i}"})
        for i, k in enumerate(step_kinds)
    ]
    return Plan(intent_kind="explore", steps=steps)


def _base_agent() -> dict[str, Any]:
    return {
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "is_alive": True,
        "has_left_zone": False,
        "hp": 100,
        "hunger": 0,
        "thirst": 0,
        "inventory": [],
    }


def _base_state() -> dict[str, Any]:
    return {
        "world_minute": 0,
        "emission_active": False,
    }


# ── Model tests ───────────────────────────────────────────────────────────────

class TestActivePlanStep:
    def test_defaults(self) -> None:
        step = ActivePlanStep(kind="travel_to_location")
        assert step.status == STEP_STATUS_PENDING
        assert step.started_turn is None
        assert step.completed_turn is None
        assert step.failure_reason is None

    def test_serialization_roundtrip(self) -> None:
        step = ActivePlanStep(
            kind="explore_location",
            payload={"location_id": "X1"},
            status=STEP_STATUS_RUNNING,
            started_turn=10,
        )
        assert ActivePlanStep.from_dict(step.to_dict()) == step

    def test_from_dict_missing_fields_gets_defaults(self) -> None:
        step = ActivePlanStep.from_dict({"kind": "wait"})
        assert step.kind == "wait"
        assert step.status == STEP_STATUS_PENDING


class TestActivePlanV3Model:
    def test_current_step_returns_first_step(self) -> None:
        plan = ActivePlanV3(
            steps=[
                ActivePlanStep(kind="travel_to_location"),
                ActivePlanStep(kind="explore_location"),
            ]
        )
        assert plan.current_step is not None
        assert plan.current_step.kind == "travel_to_location"

    def test_current_step_none_when_complete(self) -> None:
        plan = ActivePlanV3(
            steps=[ActivePlanStep(kind="travel_to_location")],
            current_step_index=1,
        )
        assert plan.current_step is None
        assert plan.is_complete

    def test_advance_step_marks_completed_and_moves_index(self) -> None:
        plan = ActivePlanV3(
            steps=[
                ActivePlanStep(kind="travel_to_location"),
                ActivePlanStep(kind="explore_location"),
            ]
        )
        plan.advance_step(world_turn=5)
        assert plan.steps[0].status == STEP_STATUS_COMPLETED
        assert plan.steps[0].completed_turn == 5
        assert plan.current_step_index == 1
        assert plan.status == ACTIVE_PLAN_STATUS_ACTIVE

    def test_advance_step_to_completion_sets_status(self) -> None:
        plan = ActivePlanV3(
            steps=[ActivePlanStep(kind="explore_location")]
        )
        plan.advance_step(world_turn=7)
        assert plan.is_complete
        assert plan.status == ACTIVE_PLAN_STATUS_COMPLETED

    def test_mark_failed_sets_step_and_plan(self) -> None:
        plan = ActivePlanV3(
            steps=[ActivePlanStep(kind="travel_to_location")]
        )
        plan.mark_failed("route_blocked", world_turn=3)
        assert plan.steps[0].status == STEP_STATUS_FAILED
        assert plan.steps[0].failure_reason == "route_blocked"
        assert plan.status == ACTIVE_PLAN_STATUS_FAILED

    def test_request_repair_increments_count(self) -> None:
        plan = ActivePlanV3()
        plan.request_repair("emission_interrupt", world_turn=10)
        assert plan.repair_count == 1
        assert plan.status == ACTIVE_PLAN_STATUS_REPAIRING

    def test_abort_sets_status_and_reason(self) -> None:
        plan = ActivePlanV3()
        plan.abort("max_repairs_exceeded", world_turn=12)
        assert plan.status == ACTIVE_PLAN_STATUS_ABORTED
        assert plan.abort_reason == "max_repairs_exceeded"

    def test_serialization_roundtrip(self) -> None:
        plan = ActivePlanV3(
            objective_key="FIND_ARTIFACTS",
            status=ACTIVE_PLAN_STATUS_ACTIVE,
            created_turn=1,
            updated_turn=5,
            steps=[
                ActivePlanStep(kind="travel_to_location", status=STEP_STATUS_COMPLETED, completed_turn=3),
                ActivePlanStep(kind="explore_location", status=STEP_STATUS_RUNNING, started_turn=4),
            ],
            current_step_index=1,
            source_refs=["mem-1"],
            memory_refs=["mem-2"],
            repair_count=1,
        )
        restored = ActivePlanV3.from_dict(plan.to_dict())
        assert restored.objective_key == plan.objective_key
        assert restored.current_step_index == 1
        assert len(restored.steps) == 2
        assert restored.steps[0].status == STEP_STATUS_COMPLETED
        assert restored.steps[1].status == STEP_STATUS_RUNNING
        assert restored.repair_count == 1
        assert restored.source_refs == ["mem-1"]


# ── Manager tests ─────────────────────────────────────────────────────────────

class TestCreateActivePlan:
    def test_creates_plan_from_objective_decision(self) -> None:
        decision = _decision("FIND_ARTIFACTS")
        plan = _plan(STEP_TRAVEL_TO_LOCATION, STEP_EXPLORE_LOCATION)
        ap = create_active_plan(decision, world_turn=1, plan=plan)
        assert ap.objective_key == "FIND_ARTIFACTS"
        assert len(ap.steps) == 2
        assert ap.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert ap.steps[1].kind == STEP_EXPLORE_LOCATION
        assert ap.created_turn == 1
        assert ap.repair_count == 0
        assert ap.status == ACTIVE_PLAN_STATUS_ACTIVE

    def test_source_refs_copied_from_objective(self) -> None:
        decision = _decision("SELL_ARTIFACTS")
        plan = _plan(STEP_TRAVEL_TO_LOCATION)
        ap = create_active_plan(decision, world_turn=2, plan=plan)
        assert "mem-abc" in ap.source_refs
        assert "mem-def" in ap.source_refs

    def test_create_active_plan_extracts_memory_refs_from_source_refs(self) -> None:
        decision = ObjectiveDecision(
            selected=Objective(
                key="SELL_ARTIFACTS",
                source="test",
                urgency=0.7,
                expected_value=0.8,
                risk=0.1,
                time_cost=0.3,
                resource_cost=0.1,
                confidence=0.9,
                goal_alignment=0.8,
                memory_confidence=0.9,
                source_refs=("memory:mem_trader_1", "world:loc_a"),
            ),
            selected_score=_score("SELL_ARTIFACTS"),
            alternatives=(),
        )
        ap = create_active_plan(decision, world_turn=2, plan=_plan(STEP_TRAVEL_TO_LOCATION))
        assert ap.source_refs == ["memory:mem_trader_1", "world:loc_a"]
        assert ap.memory_refs == ["mem_trader_1"]

    def test_empty_plan_creates_active_plan_with_no_steps(self) -> None:
        decision = _decision("IDLE")
        plan = Plan(intent_kind="idle", steps=[])
        ap = create_active_plan(decision, world_turn=1, plan=plan)
        assert ap.steps == []
        assert ap.is_complete


class TestAgentDictIO:
    def test_get_returns_none_when_key_absent(self) -> None:
        agent = _base_agent()
        assert get_active_plan(agent) is None

    def test_save_and_get_roundtrip(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        assert "active_plan_v3" in agent
        restored = get_active_plan(agent)
        assert restored is not None
        assert restored.objective_key == "FIND_ARTIFACTS"

    def test_clear_removes_key(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        clear_active_plan(agent)
        assert "active_plan_v3" not in agent
        assert get_active_plan(agent) is None

    def test_get_ignores_non_dict_value(self) -> None:
        agent = _base_agent()
        agent["active_plan_v3"] = "corrupt"
        assert get_active_plan(agent) is None


class TestAssessActivePlanV3:
    def test_continue_when_plan_valid(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "continue"
        assert reason is None

    def test_complete_when_all_steps_done(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        ap.advance_step(world_turn=2)  # single step → complete
        save_active_plan(agent, ap)
        op, _ = assess_active_plan_v3(agent, _base_state(), world_turn=3)
        assert op == "complete"

    def test_abort_when_no_active_plan(self) -> None:
        agent = _base_agent()
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=1)
        assert op == "abort"
        assert reason == "no_active_plan"

    def test_repair_on_emission_threat(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        state = _base_state()
        state["emission_active"] = True
        op, reason = assess_active_plan_v3(agent, state, world_turn=2)
        assert op == "repair"
        assert reason == "emission_interrupt"

    def test_repair_on_critical_thirst(self) -> None:
        agent = _base_agent()
        agent["thirst"] = 95
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "repair"
        assert reason == "critical_thirst"

    def test_repair_on_critical_hunger(self) -> None:
        agent = _base_agent()
        agent["hunger"] = 90
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "repair"
        assert reason == "critical_hunger"

    def test_repair_on_critical_hp(self) -> None:
        agent = _base_agent()
        agent["hp"] = 20
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "repair"
        assert reason == "critical_hp"

    def test_repair_trader_unavailable(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="trade",
            steps=[PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"trader_id": "trader-001"})],
        )
        ap = create_active_plan(_decision("RESUPPLY"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        state = _base_state()
        state["known_traders"] = []  # trader-001 not in list
        op, reason = assess_active_plan_v3(agent, state, world_turn=2)
        assert op == "repair"
        assert reason == "trader_unavailable"

    def test_continue_when_trader_available(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="trade",
            steps=[PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"trader_id": "trader-001"})],
        )
        ap = create_active_plan(_decision("RESUPPLY"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        state = _base_state()
        state["known_traders"] = ["trader-001"]
        op, reason = assess_active_plan_v3(agent, state, world_turn=2)
        assert op == "continue"

    def test_active_plan_trader_check_uses_trader_location_when_trader_id_missing(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="trade",
            steps=[PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"trader_location_id": "loc_b"})],
        )
        ap = create_active_plan(_decision("RESUPPLY"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        state = _base_state()
        state["traders"] = {"trader-001": {"id": "trader-001", "location_id": "loc_b", "is_alive": True}}
        op, reason = assess_active_plan_v3(agent, state, world_turn=2)
        assert op == "continue"

    def test_repair_target_location_empty(self) -> None:
        agent = _base_agent()
        _setup_confirmed_empty(agent, "loc-0", world_turn=5)
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        # loc-0 is set in _plan helper
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=6)
        assert op == "repair"
        assert reason == "target_location_empty"

    def test_active_plan_repair_uses_target_id_alias_for_location(self) -> None:
        agent = _base_agent()
        _setup_confirmed_empty(agent, "loc-alias", world_turn=5)
        plan = Plan(
            intent_kind="explore",
            steps=[PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"target_id": "loc-alias"})],
        )
        ap = create_active_plan(_decision(), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=6)
        assert op == "repair"
        assert reason == "target_location_empty"

    def test_repair_supplies_consumed_mid_plan(self) -> None:
        agent = _base_agent()
        agent["inventory"] = []  # no food
        plan = Plan(
            intent_kind="consume",
            steps=[PlanStep(kind=STEP_CONSUME_ITEM, payload={"required_item": "food_can"})],
        )
        ap = create_active_plan(_decision("RESTORE_FOOD"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "repair"
        assert reason == "supplies_consumed_mid_plan"

    def test_active_plan_supply_check_uses_item_type_alias(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="consume",
            steps=[PlanStep(kind=STEP_CONSUME_ITEM, payload={"item_type": "food_can"})],
        )
        ap = create_active_plan(_decision("RESTORE_FOOD"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "repair"
        assert reason == "supplies_consumed_mid_plan"

    def test_continue_when_supply_present(self) -> None:
        agent = _base_agent()
        agent["inventory"] = [{"type": "food_can", "id": "item-1"}]
        plan = Plan(
            intent_kind="consume",
            steps=[PlanStep(kind=STEP_CONSUME_ITEM, payload={"required_item": "food_can"})],
        )
        ap = create_active_plan(_decision("RESTORE_FOOD"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        op, _ = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "continue"

    def test_abort_after_max_repairs(self) -> None:
        agent = _base_agent()
        agent["emission_active"] = True  # will trigger repair
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        ap.repair_count = MAX_REPAIR_COUNT  # already at limit
        save_active_plan(agent, ap)
        state = _base_state()
        state["emission_active"] = True
        op, reason = assess_active_plan_v3(agent, state, world_turn=2)
        assert op == "abort"
        assert reason == "max_repairs_exceeded"

    def test_abort_on_failed_trade_sell_step_no_items_sold(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="trade",
            steps=[PlanStep(kind=STEP_TRADE_SELL_ITEM, payload={"item_category": "artifact"})],
        )
        ap = create_active_plan(_decision("SELL_ARTIFACTS"), world_turn=1, plan=plan)
        assert ap.current_step is not None
        ap.current_step.status = STEP_STATUS_FAILED
        ap.current_step.failure_reason = "trade_sell_failed:no_items_sold"
        save_active_plan(agent, ap)

        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "abort"
        assert reason == "trade_sell_failed:no_items_sold"

    def test_failed_trade_sell_no_trader_requests_repair(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="trade",
            steps=[PlanStep(kind=STEP_TRADE_SELL_ITEM, payload={"item_category": "artifact"})],
        )
        ap = create_active_plan(_decision("SELL_ARTIFACTS"), world_turn=1, plan=plan)
        assert ap.current_step is not None
        ap.current_step.status = STEP_STATUS_FAILED
        ap.current_step.failure_reason = "trade_sell_failed:no_trader_at_location"
        save_active_plan(agent, ap)

        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "repair"
        assert reason == "trader_unavailable"

    def test_repair_on_failed_non_trade_step(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        assert ap.current_step is not None
        ap.current_step.status = STEP_STATUS_FAILED
        ap.current_step.failure_reason = "path_blocked"
        save_active_plan(agent, ap)

        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "repair"
        assert reason == "path_blocked"


class TestRepairActivePlan:
    def test_repair_increments_count_and_resets_step(self) -> None:
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        step = ap.current_step
        assert step is not None
        step.status = STEP_STATUS_RUNNING
        step.started_turn = 1
        agent = _base_agent()
        result = repair_active_plan(agent, ap, "emission_interrupt", world_turn=2)
        assert result.repair_count == 1
        assert result.status == ACTIVE_PLAN_STATUS_ACTIVE
        assert ap.current_step is not None
        assert ap.current_step.status == STEP_STATUS_PENDING
        assert ap.current_step.started_turn is None

    def test_repair_aborts_when_max_reached(self) -> None:
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        ap.repair_count = MAX_REPAIR_COUNT - 1
        agent = _base_agent()
        result = repair_active_plan(agent, ap, "emission_interrupt", world_turn=5)
        assert result.status == ACTIVE_PLAN_STATUS_ABORTED
        assert "max_repairs_exceeded" in result.abort_reason

    def test_emission_interrupt_inserts_shelter_steps(self) -> None:
        agent = _base_agent()
        agent["location_id"] = "loc_a"
        state = {
            "world_minute": 0,
            "emission_active": True,
            "locations": {
                "loc_a": {"connections": [{"to": "loc_b"}], "terrain_type": "plain"},
                "loc_b": {"connections": [{"to": "loc_a"}], "terrain_type": "buildings"},
            },
            "agents": {},
        }
        plan = _plan(STEP_EXPLORE_LOCATION, STEP_TRAVEL_TO_LOCATION)
        ap = create_active_plan(_decision(), world_turn=1, plan=plan)
        result = repair_active_plan(agent, ap, "emission_interrupt", world_turn=2, state=state)
        assert result.repair_count == 1
        assert result.steps[0].kind == STEP_TRAVEL_TO_LOCATION
        assert result.steps[1].kind == "wait"
        assert result.steps[2].kind == STEP_EXPLORE_LOCATION


class TestShouldReplaceActivePlan:
    def test_replace_when_no_plan(self) -> None:
        agent = _base_agent()
        assert should_replace_active_plan(agent, "FIND_ARTIFACTS") is True

    def test_no_replace_when_same_objective(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision("FIND_ARTIFACTS"), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        assert should_replace_active_plan(agent, "FIND_ARTIFACTS") is False

    def test_replace_when_different_objective(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision("FIND_ARTIFACTS"), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        assert should_replace_active_plan(agent, "RESUPPLY_FOOD") is True

    def test_replace_when_plan_aborted(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision("FIND_ARTIFACTS"), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        ap.abort("test", world_turn=2)
        save_active_plan(agent, ap)
        assert should_replace_active_plan(agent, "FIND_ARTIFACTS") is True

    def test_replace_when_plan_completed(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision("FIND_ARTIFACTS"), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        ap.advance_step(world_turn=2)
        save_active_plan(agent, ap)
        assert should_replace_active_plan(agent, "FIND_ARTIFACTS") is True


# ── Scenario tests ────────────────────────────────────────────────────────────

class TestLongArtifactLoopScenario:
    """FIND_ARTIFACTS: travel → explore → travel → explore → sell."""

    def _make_plan(self) -> tuple[dict, ActivePlanV3]:
        agent = _base_agent()
        plan = Plan(
            intent_kind="explore",
            steps=[
                PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"location_id": "A1"}),
                PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"location_id": "A1"}),
                PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"location_id": "A3"}),
                PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"location_id": "A3"}),
                PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"location_id": "trader-loc"}),
                PlanStep(kind=STEP_TRADE_SELL_ITEM, payload={"trader_id": "trader-1"}),
            ],
        )
        ap = create_active_plan(_decision("FIND_ARTIFACTS"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        return agent, ap

    def test_plan_created_with_all_steps(self) -> None:
        _, ap = self._make_plan()
        assert len(ap.steps) == 6
        assert ap.objective_key == "FIND_ARTIFACTS"

    def test_step_by_step_progression(self) -> None:
        agent, ap = self._make_plan()
        state = _base_state()
        state["known_traders"] = ["trader-1"]

        for turn in range(1, 7):
            op, _ = assess_active_plan_v3(agent, state, world_turn=turn)
            assert op == "continue", f"Expected continue at step {turn}, got {op}"
            ap.advance_step(world_turn=turn)
            save_active_plan(agent, ap)

        op, _ = assess_active_plan_v3(agent, state, world_turn=7)
        assert op == "complete"

    def test_plan_tracks_current_step_correctly(self) -> None:
        agent, ap = self._make_plan()
        assert ap.current_step_index == 0
        assert ap.current_step is not None
        assert ap.current_step.kind == STEP_TRAVEL_TO_LOCATION

        ap.advance_step(world_turn=2)
        save_active_plan(agent, ap)
        restored = get_active_plan(agent)
        assert restored is not None
        assert restored.current_step_index == 1
        assert restored.current_step is not None
        assert restored.current_step.kind == STEP_EXPLORE_LOCATION


class TestEmissionInterruption:
    def test_emission_triggers_repair(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(
            _decision("FIND_ARTIFACTS"),
            world_turn=1,
            plan=_plan(STEP_TRAVEL_TO_LOCATION, STEP_EXPLORE_LOCATION),
        )
        save_active_plan(agent, ap)

        # Emission starts
        state = _base_state()
        state["emission_active"] = True
        op, reason = assess_active_plan_v3(agent, state, world_turn=5)
        assert op == "repair"
        assert reason == "emission_interrupt"

    def test_after_repair_plan_is_active_and_step_pending(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(
            _decision("FIND_ARTIFACTS"),
            world_turn=1,
            plan=_plan(STEP_TRAVEL_TO_LOCATION, STEP_EXPLORE_LOCATION),
        )
        # Simulate step already running
        ap.steps[0].status = STEP_STATUS_RUNNING
        result = repair_active_plan(agent, ap, "emission_interrupt", world_turn=5)
        assert result.status == ACTIVE_PLAN_STATUS_ACTIVE
        assert result.steps[0].status == STEP_STATUS_PENDING

    def test_emission_memory_triggers_repair(self) -> None:
        """Emission detected via memory (not state flag)."""
        agent = _base_agent()
        _setup_emission_imminent(agent, world_turn=10)
        ap = create_active_plan(
            _decision("FIND_ARTIFACTS"),
            world_turn=1,
            plan=_plan(STEP_EXPLORE_LOCATION),
        )
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=11)
        assert op == "repair"
        assert reason == "emission_interrupt"


class TestSleepRecoveryContinuation:
    """Plan with a REST step advances properly after the step completes."""

    def test_sleep_step_advances_plan(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="rest",
            steps=[
                PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"location_id": "shelter-1"}),
                PlanStep(kind=STEP_SLEEP_FOR_HOURS, payload={"hours": 8}),
                PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"location_id": "A1"}),
            ],
        )
        ap = create_active_plan(_decision("REST_AND_RECOVER"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)

        # Travel step
        op, _ = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "continue"
        ap.advance_step(world_turn=2)
        save_active_plan(agent, ap)

        # Sleep step
        op, _ = assess_active_plan_v3(agent, _base_state(), world_turn=3)
        assert op == "continue"
        ap.advance_step(world_turn=11)  # 8 ticks of sleep
        save_active_plan(agent, ap)

        # After sleep: back to exploring
        op, _ = assess_active_plan_v3(agent, _base_state(), world_turn=12)
        assert op == "continue"
        assert ap.current_step is not None
        assert ap.current_step.kind == STEP_EXPLORE_LOCATION

    def test_sleepiness_high_but_no_emission_continues(self) -> None:
        agent = _base_agent()
        agent["sleepiness"] = 75  # high but plan is for rest, not an abort condition
        ap = create_active_plan(
            _decision("REST"),
            world_turn=1,
            plan=_plan(STEP_SLEEP_FOR_HOURS),
        )
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=2)
        assert op == "continue"


class TestResupplyThenResume:
    """Plan continues after a RESUPPLY step completes."""

    def test_resupply_step_then_continues_to_explore(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="resupply",
            steps=[
                PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"location_id": "trader-loc"}),
                PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"trader_id": "t1", "item": "food_can"}),
                PlanStep(kind=STEP_TRAVEL_TO_LOCATION, payload={"location_id": "A2"}),
                PlanStep(kind=STEP_EXPLORE_LOCATION, payload={"location_id": "A2"}),
            ],
        )
        ap = create_active_plan(_decision("RESUPPLY_THEN_EXPLORE"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)

        state = _base_state()
        state["known_traders"] = ["t1"]

        # Execute travel + buy
        for turn in [2, 3]:
            op, _ = assess_active_plan_v3(agent, state, world_turn=turn)
            assert op == "continue"
            ap.advance_step(world_turn=turn)
            save_active_plan(agent, ap)

        # Now on travel to A2
        op, _ = assess_active_plan_v3(agent, state, world_turn=4)
        assert op == "continue"
        assert ap.current_step is not None
        assert ap.current_step.kind == STEP_TRAVEL_TO_LOCATION

    def test_trader_gone_triggers_repair_during_resupply(self) -> None:
        agent = _base_agent()
        plan = Plan(
            intent_kind="trade",
            steps=[PlanStep(kind=STEP_TRADE_BUY_ITEM, payload={"trader_id": "gone-trader"})],
        )
        ap = create_active_plan(_decision("RESUPPLY"), world_turn=1, plan=plan)
        save_active_plan(agent, ap)
        state = _base_state()
        state["known_traders"] = []  # trader not found
        op, reason = assess_active_plan_v3(agent, state, world_turn=2)
        assert op == "repair"
        assert reason == "trader_unavailable"


class TestFailedPlanRepair:
    """When a step fails, plan goes to repair state; repair_count increments."""

    def test_failed_step_mark_and_repair(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        ap.mark_failed("path_blocked", world_turn=3)
        save_active_plan(agent, ap)

        # Plan is in failed state; manager should abort (not continue).
        # We call repair directly on the failed plan.
        result = repair_active_plan(agent, ap, "path_blocked", world_turn=4)
        assert result.repair_count == 1
        assert result.status == ACTIVE_PLAN_STATUS_ACTIVE

    def test_repair_three_times_leads_to_abort(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))

        for attempt in range(MAX_REPAIR_COUNT):
            result = repair_active_plan(agent, ap, "path_blocked", world_turn=attempt + 2)
            if result.status == ACTIVE_PLAN_STATUS_ABORTED:
                break

        assert ap.status == ACTIVE_PLAN_STATUS_ABORTED
        assert ap.repair_count == MAX_REPAIR_COUNT

    def test_assess_returns_abort_after_max_repair_count_stored(self) -> None:
        agent = _base_agent()
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        ap.repair_count = MAX_REPAIR_COUNT
        save_active_plan(agent, ap)
        # Even with no emergency, should abort.
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=5)
        assert op == "abort"
        assert reason == "max_repairs_exceeded"


class TestConfirmedEmptyMemoryIntegration:
    """confirmed_empty in memory triggers repair when plan targets that location."""

    def test_confirmed_empty_triggers_repair(self) -> None:
        agent = _base_agent()
        _setup_confirmed_empty(agent, "loc-0", world_turn=7)
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=8)
        assert op == "repair"
        assert reason == "target_location_empty"

    def test_confirmed_empty_for_different_location_does_not_trigger(self) -> None:
        agent = _base_agent()
        _setup_confirmed_empty(agent, "other-loc", world_turn=7)
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, _ = assess_active_plan_v3(agent, _base_state(), world_turn=8)
        assert op == "continue"

    def test_stale_memory_invalidation_repair(self) -> None:
        """Memory shows location was empty on turn 3; plan is being executed on turn 10."""
        agent = _base_agent()
        _setup_confirmed_empty(agent, "loc-0", world_turn=3)
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=10)
        # confirmed_empty is still a repair trigger regardless of age
        assert op == "repair"
        assert reason == "target_location_empty"

    def test_target_moved_memory_triggers_target_moved_repair(self) -> None:
        agent = _base_agent()
        add_memory_record(
            agent,
            MemoryRecord(
                id="mem_target_moved_1",
                agent_id="bot1",
                layer="spatial",
                kind="target_moved",
                created_turn=7,
                last_accessed_turn=None,
                summary="Цель сместилась",
                details={"target_id": "target_1", "from_location_id": "loc-0", "to_location_id": "loc-1"},
                location_id="loc-0",
                confidence=0.8,
            ),
        )
        ap = create_active_plan(_decision(), world_turn=1, plan=_plan(STEP_EXPLORE_LOCATION))
        save_active_plan(agent, ap)
        op, reason = assess_active_plan_v3(agent, _base_state(), world_turn=8)
        assert op == "repair"
        assert reason == "target_moved"
