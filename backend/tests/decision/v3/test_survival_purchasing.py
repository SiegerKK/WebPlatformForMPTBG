from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.plan import (
    Plan,
    PlanStep,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
    STEP_CONSUME_ITEM,
    STEP_LOOT_CORPSE,
)
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import _plan_seek_consumable, build_plan
from tests.decision.conftest import make_agent, make_state_with_trader


def _execute_entire_plan(
    agent: dict,
    state: dict,
    plan: Plan,
    start_turn: int = 100,
    *,
    max_steps: int = 10,
) -> list[dict]:
    collected_events: list[dict] = []
    turn = start_turn
    for _ in range(max_steps):
        if plan.is_complete:
            return collected_events
        ctx = build_agent_context("bot1", agent, state)
        events = execute_plan_step(ctx, plan, state, turn) or []
        collected_events.extend(events)
        turn += 1

    current_step = (
        plan.steps[plan.current_step_index]
        if plan.current_step_index < len(plan.steps)
        else None
    )
    raise AssertionError(
        "plan did not complete: "
        f"current_step_index={plan.current_step_index}, "
        f"current_step={(current_step.kind if current_step else None)}, "
        f"step_payload={(current_step.payload if current_step else None)}, "
        f"agent_money={agent.get('money')}, "
        f"inventory={agent.get('inventory')}, "
        f"events_tail={collected_events[-10:]}"
    )


def test_survival_buy_food_prefers_cheapest_affordable_item() -> None:
    agent = make_agent(money=200, inventory=[])
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["traders"]["trader_1"]["money"] = 10_000
    ctx = build_agent_context("bot1", agent, state)

    plan = Plan(
        intent_kind="seek_food",
        steps=[
            PlanStep(
                kind=STEP_TRADE_BUY_ITEM,
                payload={
                    "item_category": "food",
                    "buy_mode": "survival_cheapest",
                    "compatible_item_types": ["bread", "glucose"],
                    "reason": "buy_food_survival",
                },
            )
        ],
        created_turn=100,
    )

    events = execute_plan_step(ctx, plan, state, 100)
    assert events
    bought = events[0]["payload"]["item_type"]
    assert bought == "bread"


def test_critical_thirst_plan_includes_sell_buy_consume_chain_when_unaffordable() -> None:
    agent = make_agent(
        money=0,
        thirst=100,
        inventory=[{"id": "artifact_1", "type": "soul", "name": "Soul", "value": 2000}],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["traders"]["trader_1"]["money"] = 10_000
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = Intent(kind="seek_water", score=1.0)

    plan = _plan_seek_consumable(ctx, intent, state, 100, need_result)
    assert plan is not None
    step_kinds = [step.kind for step in plan.steps]
    assert STEP_TRADE_SELL_ITEM in step_kinds
    assert STEP_TRADE_BUY_ITEM in step_kinds
    assert STEP_CONSUME_ITEM in step_kinds


def test_critical_thirst_at_trader_sells_buys_and_consumes_water() -> None:
    agent = make_agent(
        money=38,
        thirst=100,
        inventory=[{"id": "det_1", "type": "echo_detector", "name": "Эхо", "value": 120}],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["traders"]["trader_1"]["money"] = 10_000
    thirst_before = int(agent["thirst"])
    money_before = int(agent["money"])
    need_result = evaluate_need_result(build_agent_context("bot1", agent, state), state)
    plan = _plan_seek_consumable(
        build_agent_context("bot1", agent, state),
        Intent(kind="seek_water", score=1.0),
        state,
        world_turn=100,
        need_result=need_result,
    )
    assert plan is not None
    step_kinds = [step.kind for step in plan.steps]
    assert step_kinds[:3] == [STEP_TRADE_SELL_ITEM, STEP_TRADE_BUY_ITEM, STEP_CONSUME_ITEM]

    _execute_entire_plan(agent, state, plan, start_turn=100)

    assert int(agent["money"]) >= money_before  # sell provided liquidity before buy/consume
    assert not any(item.get("type") == "water" for item in agent.get("inventory", []))  # bought then consumed
    assert int(agent["thirst"]) < thirst_before
    assert int(agent["thirst"]) <= 70


def test_emergency_survival_trade_does_not_sell_equipped_weapon_or_only_usable_ammo() -> None:
    agent = make_agent(
        money=20,
        thirst=100,
        has_weapon=True,
        has_armor=True,
        has_ammo=True,
        inventory=[
            {"id": "ammo_1", "type": "ammo_9mm", "name": "9mm", "value": 40},
            {"id": "ammo_2", "type": "ammo_9mm", "name": "9mm", "value": 40},
            {"id": "ammo_3", "type": "ammo_9mm", "name": "9mm", "value": 40},
            {"id": "artifact_1", "type": "soul", "name": "Soul", "value": 2000},
        ],
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["traders"]["trader_1"]["money"] = 10_000
    initial_weapon = dict(agent["equipment"]["weapon"])
    initial_armor = dict(agent["equipment"]["armor"])
    initial_ammo_count = sum(1 for item in agent["inventory"] if item.get("type") == "ammo_9mm")

    need_result = evaluate_need_result(build_agent_context("bot1", agent, state), state)
    plan = _plan_seek_consumable(
        build_agent_context("bot1", agent, state),
        Intent(kind="seek_water", score=1.0),
        state,
        world_turn=100,
        need_result=need_result,
    )
    assert plan is not None
    _execute_entire_plan(agent, state, plan, start_turn=100)

    assert agent["equipment"]["weapon"] == initial_weapon
    assert agent["equipment"]["armor"] == initial_armor
    assert sum(1 for item in agent["inventory"] if item.get("type") == "ammo_9mm") == initial_ammo_count


def test_seek_water_prefers_local_corpse_loot_before_trader_buy() -> None:
    agent = make_agent(
        money=0,
        thirst=100,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["locations"]["loc_a"]["corpses"] = [
        {
            "corpse_id": "corpse_water",
            "agent_id": "dead_1",
            "visible": True,
            "lootable": True,
            "inventory": [{"id": "water_from_corpse", "type": "water", "name": "Water", "value": 30}],
            "money": 0,
        }
    ]

    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_seek_consumable(ctx, Intent(kind="seek_water", score=1.0), state, 100, need_result)

    assert plan is not None
    assert plan.steps
    assert plan.steps[0].kind == STEP_LOOT_CORPSE
    assert plan.steps[0].payload.get("corpse_id") == "corpse_water"


def test_prepare_for_hunt_resupply_prefers_local_corpse_loot() -> None:
    agent = make_agent(
        money=0,
        has_weapon=True,
        has_armor=False,
        has_ammo=False,
    )
    state = make_state_with_trader(agent=agent, trader_at="loc_a")
    state["locations"]["loc_a"]["corpses"] = [
        {
            "corpse_id": "corpse_ammo",
            "agent_id": "dead_2",
            "visible": True,
            "lootable": True,
            "inventory": [{"id": "armor_loot", "type": "leather_jacket", "name": "Jacket", "value": 120}],
            "money": 0,
        }
    ]

    ctx = build_agent_context("bot1", agent, state)
    intent = Intent(
        kind="resupply",
        score=1.0,
        metadata={"objective_key": "PREPARE_FOR_HUNT"},
    )
    plan = build_plan(ctx, intent, state, 100)

    assert plan is not None
    assert plan.steps
    assert plan.steps[0].kind == STEP_LOOT_CORPSE
    assert plan.steps[0].payload.get("corpse_id") == "corpse_ammo"
