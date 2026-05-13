from __future__ import annotations

import random

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_CONFIRM_KILL
from app.games.zone_stalkers.rules.agent_lifecycle import kill_agent
from app.games.zone_stalkers.rules.tick_rules import (
    _bot_ask_colocated_stalkers_about_agent,
    _check_global_goal_completion,
    _write_location_observations,
)
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
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


def test_corpse_seen_still_creates_retrievable_memory_before_knowledge_tables() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    _kill_target_by_emission(state, target, world_turn=100)

    write_memory_event_to_v3(
        agent_id="hunter",
        agent=hunter,
        legacy_entry={
            "world_turn": 101,
            "type": "observation",
            "summary": "Вижу труп цели в loc_b",
            "effects": {
                "action_kind": "corpse_seen",
                "target_id": "target",
                "corpse_id": "corpse_target",
                "location_id": "loc_b",
            },
        },
        world_turn=101,
    )

    corpse_seen_records = v3_action_records(hunter, "corpse_seen")
    assert corpse_seen_records, "corpse_seen must remain retrievable before PR3 knowledge tables"
    details = corpse_seen_records[-1]["details"]
    assert details.get("target_id") == "target"
    assert details.get("location_id") == "loc_b"


def test_target_corpse_reported_still_creates_retrievable_lead_before_knowledge_tables() -> None:
    hunter, _, _ = _build_hunter_and_target_state()
    write_memory_event_to_v3(
        agent_id="hunter",
        agent=hunter,
        legacy_entry={
            "world_turn": 102,
            "type": "observation",
            "summary": "Свидетель сообщил о трупе цели в loc_b",
            "effects": {
                "action_kind": "target_corpse_reported",
                "target_id": "target",
                "corpse_id": "corpse_target",
                "location_id": "loc_b",
                "source_agent_id": "witness",
            },
        },
        world_turn=102,
    )

    corpse_report_records = v3_action_records(hunter, "target_corpse_reported")
    assert corpse_report_records, "target_corpse_reported lead must remain retrievable before PR3 knowledge tables"
    details = corpse_report_records[-1]["details"]
    assert details.get("target_id") == "target"
    assert details.get("location_id") == "loc_b"
    assert details.get("source_agent_id") == "witness"


def test_target_corpse_reported_still_indexes_target_and_location() -> None:
    hunter, _, _ = _build_hunter_and_target_state()
    write_memory_event_to_v3(
        agent_id="hunter",
        agent=hunter,
        legacy_entry={
            "world_turn": 103,
            "type": "observation",
            "summary": "Есть след по трупу цели",
            "effects": {
                "action_kind": "target_corpse_reported",
                "target_id": "target",
                "location_id": "loc_b",
                "source_agent_id": "witness",
            },
        },
        world_turn=103,
    )
    memory_v3 = hunter.get("memory_v3", {})
    records = memory_v3.get("records", {})
    indexes = memory_v3.get("indexes", {})
    assert records, "memory_v3 must contain target_corpse_reported record"
    record_id = next(iter(records.keys()))
    assert record_id in indexes.get("by_entity", {}).get("target", [])
    assert record_id in indexes.get("by_location", {}).get("loc_b", [])


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


def test_confirm_kill_requires_visible_corpse_not_only_dead_target_location() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    _kill_target_by_emission(state, target, world_turn=100)

    hunter["location_id"] = "loc_b"
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["hunter"]
    state["locations"]["loc_b"]["corpses"] = []
    target["location_id"] = "loc_b"

    plan = Plan(
        intent_kind="hunt_target",
        steps=[PlanStep(kind=STEP_CONFIRM_KILL, payload={"target_id": "target"})],
        created_turn=101,
    )
    execute_plan_step(build_agent_context("hunter", hunter, state), plan, state, 101)

    assert not has_v3_action(hunter, "target_death_confirmed")
    hunt_failed = v3_action_records(hunter, "hunt_failed")
    assert hunt_failed
    assert hunt_failed[-1]["details"].get("reason") == "no_direct_confirmation"


def test_personal_combat_kill_confirms_goal_immediately() -> None:
    from app.games.zone_stalkers.rules.tick_rules import _combat_shoot

    hunter, target, state = _build_hunter_and_target_state()
    hunter["location_id"] = "loc_b"
    hunter["kill_target_id"] = "target"
    target["location_id"] = "loc_b"
    target["hp"] = 1
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["hunter", "target"]
    hunter.setdefault("equipment", {})
    hunter["equipment"]["weapon"] = {"type": "pistol", "damage": 100, "accuracy": 1.0, "value": 100}
    participant = {"enemies": ["target"], "fled": False}
    combat = {
        "id": "combat_test",
        "participants": {
            "hunter": participant,
            "target": {"enemies": ["hunter"], "fled": False},
        },
    }

    _combat_shoot("hunter", hunter, participant, combat, state, world_turn=120, rng=random.Random(0))
    _check_global_goal_completion("hunter", hunter, state, world_turn=121)

    records = v3_action_records(hunter, "target_death_confirmed")
    assert records
    details = records[-1]["details"]
    assert details.get("confirmation_source") == "personal_combat_kill"
    assert details.get("directly_observed") is True
    assert details.get("killer_id") == "hunter"
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


def test_corpse_decay_turn_is_7200_turns_after_death() -> None:
    hunter, target, state = _build_hunter_and_target_state()
    _kill_target_by_emission(state, target, world_turn=100)
    corpses = state["locations"]["loc_b"].get("corpses", [])
    assert corpses
    assert corpses[-1]["decay_turn"] == 100 + 7200
