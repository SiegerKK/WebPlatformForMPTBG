from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_CONFIRM_KILL
from app.games.zone_stalkers.rules.agent_lifecycle import kill_agent
from app.games.zone_stalkers.rules.tick_rules import (
    _bot_ask_colocated_stalkers_about_agent,
    _check_global_goal_completion,
    _write_location_observations,
)
from tests.decision.conftest import make_agent, make_minimal_state
from tests.decision.v3.memory_assertions import has_v3_action, v3_action_records


def _build_hunter_and_target_state() -> tuple[dict, dict, dict]:
    hunter = make_agent(agent_id="hunter", location_id="loc_a", kill_target_id="target")
    hunter["global_goal"] = "kill_stalker"
    target = make_agent(agent_id="target", location_id="loc_b", has_weapon=False, has_armor=False, has_ammo=False)
    state = make_minimal_state(agent_id="hunter", agent=hunter)
    state["agents"]["target"] = target
    state["locations"]["loc_b"]["agents"] = ["target"]
    return hunter, target, state


def _kill_target_by_emission(state: dict, target: dict, world_turn: int = 100) -> None:
    events: list[dict] = []
    kill_agent(
        agent_id="target",
        agent=target,
        state=state,
        world_turn=world_turn,
        cause="emission",
        location_id="loc_b",
        events=events,
    )


def test_target_dies_from_emission_does_not_auto_complete_kill_goal() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    _kill_target_by_emission(state, target, world_turn=100)

    _check_global_goal_completion("hunter", hunter, state, world_turn=101)

    assert hunter.get("global_goal_achieved") is not True
    assert hunter.get("has_left_zone") is not True
    assert not has_v3_action(hunter, "target_death_confirmed")


def test_witness_sees_corpse_and_remembers_corpse_location() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    witness = make_agent(agent_id="witness", location_id="loc_b", has_weapon=False, has_armor=False, has_ammo=False)
    state["agents"]["witness"] = witness
    state["locations"]["loc_b"]["agents"] = ["target", "witness"]
    _kill_target_by_emission(state, target, world_turn=100)

    _write_location_observations("witness", witness, "loc_b", state, world_turn=101)

    assert has_v3_action(witness, "corpse_seen")


def test_hunter_gets_corpse_report_but_goal_not_completed() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    witness = make_agent(agent_id="witness", location_id="loc_b", has_weapon=False, has_armor=False, has_ammo=False)
    state["agents"]["witness"] = witness
    state["locations"]["loc_b"]["agents"] = ["target", "witness"]
    _kill_target_by_emission(state, target, world_turn=100)
    _write_location_observations("witness", witness, "loc_b", state, world_turn=101)

    witness["location_id"] = "loc_a"
    state["locations"]["loc_b"]["agents"] = []
    state["locations"]["loc_a"]["agents"] = ["hunter", "witness"]
    _bot_ask_colocated_stalkers_about_agent("hunter", hunter, "target", "target", state, world_turn=102)

    assert has_v3_action(hunter, "target_corpse_reported")
    _check_global_goal_completion("hunter", hunter, state, world_turn=103)
    assert hunter.get("global_goal_achieved") is not True


def test_hunter_travels_to_corpse_and_confirms_kill() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    _kill_target_by_emission(state, target, world_turn=100)

    hunter["location_id"] = "loc_b"
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["hunter"]
    plan = Plan(
        intent_kind="hunt_target",
        steps=[PlanStep(kind=STEP_CONFIRM_KILL, payload={"target_id": "target"})],
        created_turn=101,
    )
    execute_plan_step(build_agent_context("hunter", hunter, state), plan, state, 101)
    _check_global_goal_completion("hunter", hunter, state, world_turn=102)

    assert has_v3_action(hunter, "target_death_confirmed")
    assert hunter.get("global_goal_achieved") is True


def test_personal_combat_kill_confirms_goal_immediately() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    hunter["memory_v3"] = {
        "records": {
            "m1": {
                "kind": "target_death_confirmed",
                "created_turn": 120,
                "details": {
                    "action_kind": "target_death_confirmed",
                    "target_id": "target",
                    "confirmation_source": "personal_combat_kill",
                    "directly_observed": True,
                    "killer_id": "hunter",
                },
            }
        }
    }
    _check_global_goal_completion("hunter", hunter, state, world_turn=121)
    assert hunter.get("global_goal_achieved") is True


def test_target_death_confirmed_contains_confirmation_source() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    _kill_target_by_emission(state, target, world_turn=100)
    hunter["location_id"] = "loc_b"
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["hunter"]
    plan = Plan(
        intent_kind="hunt_target",
        steps=[PlanStep(kind=STEP_CONFIRM_KILL, payload={"target_id": "target"})],
        created_turn=101,
    )
    execute_plan_step(build_agent_context("hunter", hunter, state), plan, state, 101)
    records = v3_action_records(hunter, "target_death_confirmed")
    assert records
    details = records[-1]["details"]
    assert details.get("confirmation_source") in {"self_observed_body", "personal_combat_kill"}
    assert details.get("directly_observed") is True


def test_hunter_leaves_zone_only_after_confirming_corpse() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    _kill_target_by_emission(state, target, world_turn=100)
    _check_global_goal_completion("hunter", hunter, state, world_turn=101)
    assert hunter.get("global_goal_achieved") is not True
    assert hunter.get("has_left_zone") is not True

    hunter["location_id"] = "loc_b"
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["hunter"]
    execute_plan_step(
        build_agent_context("hunter", hunter, state),
        Plan(intent_kind="hunt_target", steps=[PlanStep(kind=STEP_CONFIRM_KILL, payload={"target_id": "target"})], created_turn=102),
        state,
        102,
    )
    _check_global_goal_completion("hunter", hunter, state, world_turn=103)
    assert hunter.get("global_goal_achieved") is True
