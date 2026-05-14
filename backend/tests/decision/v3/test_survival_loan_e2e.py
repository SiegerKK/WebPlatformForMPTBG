from __future__ import annotations

from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
from tests.decision.conftest import make_agent
from tests.decision.v3.e2e_helpers import run_until


def _state() -> dict:
    agent = make_agent(
        money=20,
        thirst=100,
        hunger=10,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
    )
    agent["global_goal"] = "restore_needs"
    return {
        "seed": 5,
        "world_turn": 1,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "emission_scheduled_turn": None,
        "emission_ends_turn": None,
        "agents": {"bot1": agent},
        "traders": {
            "trader_1": {
                "id": "trader_1",
                "name": "Trader",
                "location_id": "loc_a",
                "is_alive": True,
                "money": 0,
                "accounts_receivable": 0,
                "inventory": [],
            }
        },
        "locations": {
            "loc_a": {
                "id": "loc_a",
                "name": "Trader Bunker",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [{"to": "loc_b", "travel_time": 12}],
                "items": [],
                "agents": ["bot1", "trader_1"],
            },
            "loc_b": {
                "id": "loc_b",
                "name": "Field",
                "terrain_type": "wasteland",
                "anomaly_activity": 3,
                "connections": [{"to": "loc_a", "travel_time": 12}],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def test_e2e_poor_stalker_survives_at_trader_by_taking_survival_credit() -> None:
    state = _state()
    state, _ = run_until(
        state,
        lambda s, _events: int(s["agents"]["bot1"].get("thirst") or 0) <= 70,
        max_ticks=30,
    )
    agent = state["agents"]["bot1"]
    assert bool(agent.get("is_alive", True)) is True
    assert int(agent.get("thirst") or 0) <= 70
    assert state.get("debt_ledger", {}).get("debts")
    assert int(state["traders"]["trader_1"].get("accounts_receivable") or 0) > 0

    # Focused regression: avoid repeated impossible no_items_sold sell loops.
    no_items_sold_count = 0
    for _ in range(10):
        state, events = tick_zone_map(state)
        no_items_sold_count += sum(
            1
            for ev in events
            if (ev.get("event_type") == "trade_sell_failed")
            and str((ev.get("payload") or {}).get("reason") or "") == "no_items_sold"
        )
    assert no_items_sold_count == 0

