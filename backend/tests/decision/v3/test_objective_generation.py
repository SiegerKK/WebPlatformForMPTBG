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
    assert OBJECTIVE_RESUPPLY_WEAPON not in keys
    assert OBJECTIVE_FIND_ARTIFACTS in keys
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

    assert decision.selected.key == OBJECTIVE_FIND_ARTIFACTS


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
    assert decision.selected.key == OBJECTIVE_FIND_ARTIFACTS


def test_phase1_non_hunter_does_not_generate_resupply_weapon_objective() -> None:
    agent = make_agent(has_weapon=False, global_goal="get_rich", money=100, material_threshold=3000)
    state = make_minimal_state(agent=agent)
    objectives = generate_objectives(_make_ctx(agent, state))
    keys = {obj.key for obj in objectives}
    assert OBJECTIVE_RESUPPLY_WEAPON not in keys
    assert OBJECTIVE_FIND_ARTIFACTS in keys


def test_phase1_hunter_can_generate_weapon_resupply_objective() -> None:
    agent = make_agent(
        has_weapon=False,
        global_goal="kill_stalker",
        money=100,
        material_threshold=3000,
    )
    state = make_minimal_state(agent=agent)
    objectives = generate_objectives(_make_ctx(agent, state))
    keys = {obj.key for obj in objectives}
    assert OBJECTIVE_RESUPPLY_WEAPON in keys


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


def test_debt_total_above_5000_selects_leave_zone_from_debt() -> None:
    agent = make_agent(thirst=10, hunger=10, global_goal="get_rich")
    agent["economic_state"] = {
        "debt_total": 5200,
        "creditors": ["trader_1"],
        "next_due_turn_min": 180,
        "should_escape_zone_due_to_debt": True,
    }
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    decision = choose_objective(objectives, personality=agent)

    assert decision.selected.key == OBJECTIVE_LEAVE_ZONE
    assert decision.selected.source == "debt_escape"
    assert decision.selected.metadata.get("reason") == "debt_escape_threshold"


def test_debt_escape_priority_below_critical_survival() -> None:
    agent = make_agent(thirst=100, hunger=10, global_goal="get_rich")
    agent["economic_state"] = {
        "debt_total": 5200,
        "creditors": ["trader_1"],
        "next_due_turn_min": 150,
        "should_escape_zone_due_to_debt": True,
    }
    state = make_minimal_state(agent=agent)

    objectives = generate_objectives(_make_ctx(agent, state))
    decision = choose_objective(objectives, personality=agent)

    assert any(obj.key == OBJECTIVE_LEAVE_ZONE and obj.source == "debt_escape" for obj in objectives)
    assert decision.selected.key == OBJECTIVE_RESTORE_WATER


# ── Equipment class detection regression tests ─────────────────────────────────

def test_generator_weapon_class_uses_item_mapping_for_ak74() -> None:
    """_get_weapon_class must return 'rifle' for ak74, not fallback 'pistol'."""
    from app.games.zone_stalkers.decision.objectives.generator import _get_weapon_class

    agent: dict = {
        "equipment": {
            "weapon": {"type": "ak74", "value": 1500},
        },
    }
    assert _get_weapon_class(agent) == "rifle"


def test_generator_armor_class_uses_item_mapping_for_stalker_suit() -> None:
    """_get_armor_class must return 'medium' for stalker_suit, not fallback 'light'."""
    from app.games.zone_stalkers.decision.objectives.generator import _get_armor_class

    agent: dict = {
        "equipment": {
            "armor": {"type": "stalker_suit", "value": 1200},
        },
    }
    assert _get_armor_class(agent) == "medium"


def test_ready_hunter_not_marked_weapon_inferior_due_to_ak74_mapping() -> None:
    """A hunter equipped with ak74 and stalker_suit should NOT receive
    equipment_disadvantage or weapon_inferior hints in generated objectives.
    The canonical item mapping must prevent ak74 from being misclassified as
    'pistol' and triggering PREPARE_FOR_HUNT unnecessarily.
    """
    agent = make_agent(
        global_goal="kill_stalker",
        kill_target_id="target_1",
        money=5000,
    )
    agent["equipment"] = {
        "weapon": {"type": "ak74", "value": 1500},
        "armor": {"type": "stalker_suit", "value": 1200},
    }
    # Add ammo matching the ak74 (ammo_545)
    agent["inventory"] += [
        {"id": f"ammo545_{i}", "type": "ammo_545", "value": 0}
        for i in range(30)
    ]
    # Add medkits
    agent["inventory"] += [
        {"id": "med_0", "type": "medkit", "value": 0},
        {"id": "med_1", "type": "medkit", "value": 0},
    ]
    state = make_minimal_state(agent=agent)
    # Give target known location
    state["agents"]["target_1"] = {
        "id": "target_1",
        "is_alive": True,
        "location_id": "loc_b",
        "equipment": {"weapon": {"type": "pistol", "value": 300}},
        "inventory": [],
    }
    state["locations"]["loc_b"] = {
        "name": "Target",
        "terrain_type": "buildings",
        "anomaly_activity": 0,
        "connections": [{"to": "loc_a", "travel_time": 2}],
        "agents": ["target_1"],
        "items": [],
    }

    objectives = generate_objectives(_make_ctx(agent, state))
    meta_tags = set()
    for obj in objectives:
        for tag in (obj.metadata or {}).get("hints", []):
            meta_tags.add(tag)
        if obj.key == "PREPARE_FOR_HUNT":
            reasons = (obj.metadata or {}).get("reasons", [])
            assert "weapon_inferior" not in reasons, (
                f"ak74 wrongly flagged as weapon_inferior: {reasons}"
            )
            assert "equipment_disadvantage" not in reasons, (
                f"ak74 wrongly flagged as equipment_disadvantage: {reasons}"
            )
