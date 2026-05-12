from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_LOOT_CORPSE
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import build_plan
from app.games.zone_stalkers.rules.agent_lifecycle import kill_agent
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
from tests.decision.conftest import make_agent, make_minimal_state
from tests.decision.v3.memory_assertions import has_v3_action, v3_action_records


def _build_loot_state() -> tuple[dict, dict, dict]:
    looter = make_agent(agent_id="looter", location_id="loc_b", has_weapon=False, has_armor=False, has_ammo=False)
    target = make_agent(
        agent_id="target",
        location_id="loc_b",
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
        inventory=[
            {"id": "water_1", "type": "water", "name": "Water", "value": 30},
            {"id": "med_1", "type": "medkit", "name": "Medkit", "value": 200},
            {"id": "art_1", "type": "soul", "name": "Soul", "value": 2000},
        ],
        money=100,
    )
    state = make_minimal_state(agent_id="looter", agent=looter)
    state["agents"]["target"] = target
    state["locations"]["loc_b"]["agents"] = ["looter", "target"]
    return looter, target, state


def _kill_target(state: dict, target: dict, world_turn: int = 100) -> dict:
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
    corpses = state["locations"]["loc_b"].get("corpses", [])
    assert corpses
    return corpses[-1]


def _loot_corpse(
    looter: dict,
    state: dict,
    corpse_id: str,
    world_turn: int = 101,
    **payload_overrides,
) -> None:
    payload = {"corpse_id": corpse_id, "location_id": "loc_b", "take_money": True}
    payload.update(payload_overrides)
    plan = Plan(
        intent_kind="loot",
        steps=[PlanStep(kind=STEP_LOOT_CORPSE, payload=payload)],
        created_turn=world_turn,
    )
    execute_plan_step(build_agent_context("looter", looter, state), plan, state, world_turn)


def test_dead_agent_inventory_moves_to_corpse() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    assert len(corpse.get("inventory", [])) == 3
    assert target.get("inventory") == []


def test_dead_agent_money_moves_to_corpse() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    assert int(corpse.get("money") or 0) == 100
    assert int(target.get("money") or 0) == 0


def test_living_agent_can_loot_corpse_items() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    _loot_corpse(looter, state, str(corpse["corpse_id"]))
    looter_types = {item.get("type") for item in looter.get("inventory", [])}
    assert {"water", "medkit", "soul"} <= looter_types


def test_looting_corpse_removes_items_from_corpse() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    _loot_corpse(looter, state, str(corpse["corpse_id"]))
    assert corpse.get("inventory") == []


def test_looting_corpse_transfers_money_once() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    money_before = int(looter.get("money") or 0)
    _loot_corpse(looter, state, str(corpse["corpse_id"]))
    money_after_first = int(looter.get("money") or 0)
    _loot_corpse(looter, state, str(corpse["corpse_id"]), world_turn=102)
    money_after_second = int(looter.get("money") or 0)
    assert money_after_first == money_before + 100
    assert money_after_second == money_after_first


def test_corpse_fully_looted_when_empty() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    _loot_corpse(looter, state, str(corpse["corpse_id"]))
    assert corpse.get("fully_looted") is True
    assert corpse.get("lootable") is False


def test_corpse_loot_does_not_duplicate_dead_agent_inventory() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    _loot_corpse(looter, state, str(corpse["corpse_id"]))
    assert target.get("inventory") == []
    assert has_v3_action(looter, "corpse_looted")
    record = v3_action_records(looter, "corpse_looted")[-1]
    assert int(record["details"].get("items_taken_count") or 0) >= 1


def test_loot_corpse_take_all_transfers_all_items() -> None:
    looter, target, state = _build_loot_state()
    target["inventory"].append({"id": "junk_1", "type": "bolt", "name": "Bolt", "value": 1})
    corpse = _kill_target(state, target)

    _loot_corpse(looter, state, str(corpse["corpse_id"]), take_all=True)

    looter_item_ids = {str(item.get("id") or "") for item in looter.get("inventory", []) if isinstance(item, dict)}
    assert {"water_1", "med_1", "art_1", "junk_1"} <= looter_item_ids
    assert corpse.get("inventory") == []


def test_loot_corpse_with_items_without_ids_does_not_remove_unselected_items() -> None:
    looter, target, state = _build_loot_state()
    target["inventory"] = [
        {"type": "water", "name": "Water", "value": 30},
        {"type": "junk", "name": "Junk", "value": 1},
    ]
    corpse = _kill_target(state, target)

    _loot_corpse(looter, state, str(corpse["corpse_id"]), max_items=1)

    remaining = corpse.get("inventory") or []
    assert len(remaining) == 1
    assert remaining[0].get("type") == "junk"


def test_corpse_removed_or_hidden_after_decay_turn() -> None:
    looter, target, state = _build_loot_state()
    corpse = _kill_target(state, target)
    corpse["decay_turn"] = int(state.get("world_turn") or 1)
    state, _ = tick_zone_map(state)
    corpses = state["locations"]["loc_b"].get("corpses", [])
    assert not any(str(item.get("corpse_id") or "") == str(corpse.get("corpse_id") or "") for item in corpses)


def test_get_money_for_resupply_can_plan_local_corpse_loot() -> None:
    looter, target, state = _build_loot_state()
    _ = target
    state["locations"]["loc_b"]["corpses"] = [
        {
            "corpse_id": "corpse_money",
            "agent_id": "dead_3",
            "visible": True,
            "lootable": True,
            "inventory": [{"id": "art_2", "type": "soul", "name": "Soul", "value": 2000}],
            "money": 150,
        }
    ]
    looter["location_id"] = "loc_b"
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["looter"]

    ctx = build_agent_context("looter", looter, state)
    need_result = evaluate_need_result(ctx, state)
    intent = Intent(kind="get_rich", score=0.8, metadata={"objective_key": "GET_MONEY_FOR_RESUPPLY"})
    plan = build_plan(ctx, intent, state, state["world_turn"], need_result=need_result)

    assert plan is not None
    assert plan.steps
    assert plan.steps[0].kind == STEP_LOOT_CORPSE
    assert plan.steps[0].payload.get("corpse_id") == "corpse_money"
