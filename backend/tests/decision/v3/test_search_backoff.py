from __future__ import annotations

from app.games.zone_stalkers.decision.context_builder import build_agent_context
from app.games.zone_stalkers.decision.models.intent import Intent
from app.games.zone_stalkers.decision.models.plan import STEP_TRAVEL_TO_LOCATION, STEP_WAIT
from app.games.zone_stalkers.decision.needs import evaluate_need_result
from app.games.zone_stalkers.decision.planner import _plan_get_rich
from tests.decision.conftest import make_agent


def test_exhausted_anomaly_location_not_reselected_until_cooldown() -> None:
    agent = make_agent(location_id="loc_a", global_goal="get_rich", inventory=[])
    agent["location_search_cooldowns"] = {"loc_b": 200}
    state = {
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {"bot1": agent},
        "traders": {},
        "locations": {
            "loc_a": {
                "id": "loc_a",
                "name": "A",
                "terrain_type": "plain",
                "anomaly_activity": 0,
                "dominant_anomaly_type": None,
                "connections": [{"to": "loc_b", "type": "path", "travel_time": 10, "closed": False}],
                "artifacts": [],
                "items": [],
                "agents": ["bot1"],
                "exit_zone": False,
            },
            "loc_b": {
                "id": "loc_b",
                "name": "B",
                "terrain_type": "plain",
                "anomaly_activity": 7,
                "dominant_anomaly_type": "electro",
                "connections": [{"to": "loc_a", "type": "path", "travel_time": 10, "closed": False}],
                "artifacts": [],
                "items": [],
                "agents": [],
                "exit_zone": False,
            },
        },
    }

    ctx = build_agent_context("bot1", agent, state)
    need_result = evaluate_need_result(ctx, state)
    plan = _plan_get_rich(ctx, Intent(kind="get_rich", score=1.0), state, 100, need_result)

    assert plan is not None
    assert plan.steps[0].kind in {STEP_WAIT, STEP_TRAVEL_TO_LOCATION}
    if plan.steps[0].kind == STEP_TRAVEL_TO_LOCATION:
        assert plan.steps[0].payload.get("target_id") != "loc_b"
