from __future__ import annotations

from app.games.zone_stalkers.decision.beliefs import build_belief_state
from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.objective import ObjectiveGenerationContext
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.objectives.generator import (
    OBJECTIVE_CONTINUE_CURRENT_PLAN,
    OBJECTIVE_FIND_ARTIFACTS,
    OBJECTIVE_GATHER_INTEL,
    OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
    OBJECTIVE_HUNT_TARGET,
    OBJECTIVE_LEAVE_ZONE,
    OBJECTIVE_LOCATE_TARGET,
    OBJECTIVE_PREPARE_FOR_HUNT,
    OBJECTIVE_REST,
    OBJECTIVE_RESTORE_FOOD,
    OBJECTIVE_REACH_SAFE_SHELTER,
    OBJECTIVE_RESTORE_WATER,
    OBJECTIVE_RESUPPLY_WEAPON,
    OBJECTIVE_SELL_ARTIFACTS,
    generate_objectives,
)
from app.games.zone_stalkers.decision.objectives.selection import choose_objective
from app.games.zone_stalkers.memory.models import MemoryRecord
from app.games.zone_stalkers.memory.store import add_memory_record
from tests.decision.conftest import make_agent, make_minimal_state


def _make_ctx(agent: dict, state: dict, agent_id: str = "bot1") -> ObjectiveGenerationContext:
    ctx = build_agent_context(agent_id, agent, state)
    belief = build_belief_state(ctx, agent, state["world_turn"])
    need_result = evaluate_need_result(ctx, state)
    return ObjectiveGenerationContext(
        agent_id=agent_id,
        world_turn=state["world_turn"],
        belief_state=belief,
        need_result=need_result,
        active_plan_summary={
            "urgency": 0.4,
            "remaining_value": 0.7,
            "risk": 0.2,
            "remaining_time": 0.3,
            "resource_cost": 0.0,
            "confidence": 0.8,
            "goal_alignment": 0.8,
        } if agent.get("scheduled_action") else None,
        personality=agent,
    )


def test_generate_objectives_from_immediate_item_emission_and_goal() -> None:
    agent = make_agent(
        thirst=95,
        money=5,
        has_weapon=False,
        global_goal="get_rich",
    )
    state = make_minimal_state(agent=agent, loc_terrain="plain")
    state["emission_active"] = True

    objectives = generate_objectives(_make_ctx(agent, state))
    keys = {obj.key for obj in objectives}

    assert OBJECTIVE_RESTORE_WATER in keys
    assert OBJECTIVE_RESUPPLY_WEAPON in keys
    assert OBJECTIVE_GET_MONEY_FOR_RESUPPLY in keys
    assert OBJECTIVE_REACH_SAFE_SHELTER in keys


def test_generate_hunt_objectives_without_forcing_immediate_engage() -> None:
    agent = make_agent(global_goal="kill_stalker", kill_target_id="target_1", has_weapon=False)
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    objective_map = {obj.key: obj for obj in objectives}

    assert OBJECTIVE_HUNT_TARGET in objective_map
    assert OBJECTIVE_PREPARE_FOR_HUNT in objective_map
    assert OBJECTIVE_LOCATE_TARGET in objective_map
    assert OBJECTIVE_GATHER_INTEL in objective_map
    prepare = objective_map[OBJECTIVE_PREPARE_FOR_HUNT]
    assert prepare.metadata.get("blockers")


def test_generate_continue_current_plan_when_scheduled_action_present() -> None:
    agent = make_agent()
    agent["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 5,
        "turns_total": 10,
        "target_id": "loc_b",
    }
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    keys = {obj.key for obj in objectives}

    assert OBJECTIVE_CONTINUE_CURRENT_PLAN in keys


def test_unaffordable_resupply_weapon_prefers_get_money_objective() -> None:
    agent = make_agent(
        has_weapon=False,
        money=0,
        global_goal="get_rich",
    )
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    decision = choose_objective(objectives, personality=agent)

    assert decision.selected.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY


def test_artifact_in_inventory_generates_sell_artifacts_objective() -> None:
    agent = make_agent(global_goal="get_rich")
    agent["inventory"].append({"id": "art1", "type": "soul", "value": 1500})
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    objective_keys = {obj.key for obj in objectives}

    assert OBJECTIVE_SELL_ARTIFACTS in objective_keys


def test_memory_backed_water_objective_has_memory_source_refs_and_confidence() -> None:
    agent = make_agent(thirst=65, hunger=5)
    agent["inventory"] = [i for i in agent["inventory"] if i.get("type") not in {"water", "purified_water", "energy_drink"}]
    state = make_minimal_state(agent=agent)

    add_memory_record(
        agent,
        MemoryRecord(
            id="mem_water_known_1",
            agent_id="bot1",
            layer="spatial",
            kind="water_source_known",
            created_turn=95,
            last_accessed_turn=None,
            summary="Поблизости есть источник воды",
            details={},
            location_id="loc_b",
            item_types=("water",),
            tags=("water", "drink", "item"),
            confidence=0.9,
            importance=0.7,
        ),
    )

    objectives = generate_objectives(_make_ctx(agent, state))
    restore_water = next(obj for obj in objectives if obj.key == OBJECTIVE_RESTORE_WATER)

    assert restore_water.memory_confidence > 0.5
    assert any(ref.startswith("memory:") for ref in restore_water.source_refs)


def test_soft_restore_objectives_are_not_generated_below_thresholds() -> None:
    agent = make_agent(hunger=29, thirst=15, has_weapon=False, money=329, global_goal="get_rich")
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    keys = {obj.key for obj in objectives}

    assert OBJECTIVE_RESTORE_FOOD not in keys
    assert OBJECTIVE_RESTORE_WATER not in keys

    decision = choose_objective(objectives, personality=agent)
    assert decision.selected.key == OBJECTIVE_GET_MONEY_FOR_RESUPPLY


def test_soft_restore_food_uses_soft_need_source_and_dynamic_expected_value() -> None:
    agent = make_agent(hunger=55, thirst=0, global_goal="get_rich")
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    restore_food = next(obj for obj in objectives if obj.key == OBJECTIVE_RESTORE_FOOD)

    assert restore_food.source == "soft_need"
    assert 0.35 <= restore_food.expected_value <= 0.5


def test_rest_low_sleepiness_is_not_immediate_need() -> None:
    agent = make_agent(
        sleepiness=34,
        hp=100,
        global_goal="get_rich",
    )
    state = make_minimal_state(agent=agent, loc_terrain="buildings")
    objectives = generate_objectives(_make_ctx(agent, state))
    rest = next((obj for obj in objectives if obj.key == OBJECTIVE_REST), None)
    if rest is not None:
        assert rest.source != "immediate_need"


def test_soft_rest_does_not_beat_executable_strategic_goal_by_small_margin() -> None:
    agent = make_agent(
        sleepiness=45,
        hunger=10,
        thirst=10,
        global_goal="get_rich",
    )
    state = make_minimal_state(agent=agent)
    state["locations"]["loc_a"]["anomaly_activity"] = 5

    objectives = generate_objectives(_make_ctx(agent, state))
    decision = choose_objective(objectives, personality=agent)
    assert decision.selected.key in {OBJECTIVE_FIND_ARTIFACTS, OBJECTIVE_GET_MONEY_FOR_RESUPPLY}


def test_critical_sleepiness_selects_rest() -> None:
    agent = make_agent(
        sleepiness=90,
        global_goal="get_rich",
    )
    state = make_minimal_state(agent=agent, loc_terrain="buildings")
    objectives = generate_objectives(_make_ctx(agent, state))
    rest = next(obj for obj in objectives if obj.key == OBJECTIVE_REST)

    assert rest.source == "immediate_need"
    assert rest.metadata.get("critical") is True


def test_recovery_rest_uses_recovery_need_source() -> None:
    agent = make_agent(
        hp=40,
        sleepiness=40,
        global_goal="get_rich",
    )
    agent["radiation"] = 40
    state = make_minimal_state(agent=agent, loc_terrain="buildings")
    objectives = generate_objectives(_make_ctx(agent, state))
    rest = next(obj for obj in objectives if obj.key == OBJECTIVE_REST)

    assert rest.source == "recovery_need"
    assert "Восстановление" in " ".join(rest.reasons)


def test_completed_global_goal_adds_leave_zone_objective() -> None:
    agent = make_agent(
        global_goal="get_rich",
        global_goal_achieved=True,
    )
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    objective_map = {obj.key: obj for obj in objectives}

    assert OBJECTIVE_LEAVE_ZONE in objective_map
    assert objective_map[OBJECTIVE_LEAVE_ZONE].source == "global_goal_completed"
