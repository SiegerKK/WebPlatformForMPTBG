from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.executors import execute_plan_step
from app.games.zone_stalkers.decision.models.intent import Intent, INTENT_LEAVE_ZONE
from app.games.zone_stalkers.decision.models.plan import Plan, PlanStep, STEP_LEAVE_ZONE
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import build_plan
from tests.decision.conftest import make_agent, make_minimal_state


def test_plan_leave_zone_uses_leave_step_when_already_at_exit() -> None:
    agent = make_agent(global_goal="get_rich", global_goal_achieved=True, location_id="loc_b")
    state = make_minimal_state(agent=agent)
    state["locations"]["loc_b"]["exit_zone"] = True
    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    intent = Intent(
        kind=INTENT_LEAVE_ZONE,
        score=1.0,
        reason="goal achieved",
        created_turn=state["world_turn"],
        source_goal="leave_zone",
    )
    plan = build_plan(ctx, intent, state, state["world_turn"], need_result=need_result)
    assert plan is not None
    assert [step.kind for step in plan.steps] == [STEP_LEAVE_ZONE]


def test_leave_zone_executor_marks_agent_left_zone() -> None:
    agent = make_agent(global_goal="get_rich", global_goal_achieved=True, location_id="loc_b")
    state = make_minimal_state(agent=agent)
    state["agents"]["bot1"] = agent
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["bot1"]
    state["locations"]["loc_b"]["exit_zone"] = True

    plan = Plan(
        intent_kind=INTENT_LEAVE_ZONE,
        steps=[PlanStep(kind=STEP_LEAVE_ZONE, payload={"reason": "leave_zone"})],
        created_turn=state["world_turn"],
    )
    ctx = build_agent_context("bot1", agent, state)
    events = execute_plan_step(ctx, plan, state, state["world_turn"])

    assert agent.get("has_left_zone") is True
    assert agent.get("scheduled_action") is None
    assert agent.get("action_queue") == []
    assert any(event.get("event_type") == "agent_left_zone" for event in events)


def test_leave_zone_freezes_active_debt_accounts() -> None:
    from app.games.zone_stalkers.economy.debts import advance_survival_credit

    agent = make_agent(global_goal="get_rich", global_goal_achieved=True, location_id="loc_b", money=0)
    state = make_minimal_state(agent=agent)
    state["agents"]["bot1"] = agent
    state["traders"] = {
        "trader_1": {"id": "trader_1", "is_alive": True, "money": 1000, "accounts_receivable": 0, "location_id": "loc_b"}
    }
    state["locations"]["loc_a"]["agents"] = []
    state["locations"]["loc_b"]["agents"] = ["bot1"]
    state["locations"]["loc_b"]["exit_zone"] = True

    account = advance_survival_credit(
        state=state,
        debtor_id="bot1",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=100,
        purpose="survival_food",
        location_id="loc_b",
        world_turn=state["world_turn"],
    )

    plan = Plan(
        intent_kind=INTENT_LEAVE_ZONE,
        steps=[PlanStep(kind=STEP_LEAVE_ZONE, payload={"reason": "leave_zone"})],
        created_turn=state["world_turn"],
    )
    ctx = build_agent_context("bot1", agent, state)
    events = execute_plan_step(ctx, plan, state, state["world_turn"])

    assert agent.get("has_left_zone") is True
    assert str(account.get("status") or "") == "debtor_left_zone"
    assert any(event.get("event_type") == "debt_account_frozen" for event in events)
