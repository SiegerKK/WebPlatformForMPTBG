from __future__ import annotations

from collections import defaultdict

from app.games.zone_stalkers.economy.debts import advance_survival_credit
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map
from tests.decision.conftest import make_agent


def _base_state() -> dict:
    return {
        "seed": 7,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {},
        "traders": {
            "trader_1": {
                "id": "trader_1",
                "name": "Trader",
                "is_alive": True,
                "location_id": "loc_a",
                "money": 100000,
                "accounts_receivable": 0,
                "inventory": [
                    {"id": "w1", "type": "water", "name": "Water", "value": 30},
                    {"id": "f1", "type": "bread", "name": "Bread", "value": 30},
                    {"id": "m1", "type": "bandage", "name": "Bandage", "value": 50},
                ],
            }
        },
        "locations": {
            "loc_a": {
                "id": "loc_a",
                "name": "A",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "dominant_anomaly_type": None,
                "connections": [{"to": "loc_b", "type": "road", "travel_time": 12, "closed": False}],
                "items": [],
                "artifacts": [],
                "agents": ["trader_1"],
                "exit_zone": False,
            },
            "loc_b": {
                "id": "loc_b",
                "name": "B",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "dominant_anomaly_type": None,
                "connections": [{"to": "loc_a", "type": "road", "travel_time": 12, "closed": False}],
                "items": [],
                "artifacts": [],
                "agents": [],
                "exit_zone": True,
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }


def test_long_run_lifecycle_smoke_1500_turns() -> None:
    state = _base_state()

    goal_agent = make_agent(
        agent_id="goal_bot",
        location_id="loc_a",
        global_goal="get_rich",
        global_goal_achieved=True,
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
        money=500,
    )
    goal_agent["id"] = "goal_bot"

    survival_agent = make_agent(
        agent_id="survival_bot",
        location_id="loc_a",
        global_goal="get_rich",
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
        money=0,
        thirst=100,
        hunger=85,
        hp=45,
    )
    survival_agent["id"] = "survival_bot"

    dead_agent = make_agent(
        agent_id="dead_bot",
        location_id="loc_a",
        inventory=[],
        has_weapon=False,
        has_armor=False,
        has_ammo=False,
        money=0,
    )
    dead_agent["id"] = "dead_bot"
    dead_agent["is_alive"] = False

    state["agents"] = {
        "goal_bot": goal_agent,
        "survival_bot": survival_agent,
        "dead_bot": dead_agent,
    }
    state["locations"]["loc_a"]["agents"].extend(["goal_bot", "survival_bot", "dead_bot"])

    advance_survival_credit(
        state=state,
        debtor_id="dead_bot",
        creditor_id="trader_1",
        creditor_type="trader",
        amount=150,
        purpose="survival_food",
        location_id="loc_a",
        world_turn=state["world_turn"],
    )

    episode_events: dict[str, set[str]] = defaultdict(set)
    episode_loan_counts: dict[str, int] = defaultdict(int)
    episode_last_turn: dict[str, int] = {}

    for _ in range(1500):
        state, events = tick_zone_map(state)
        current_turn = int(state.get("world_turn") or 0)
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            episode_id = str(payload.get("survival_episode_id") or "")
            if not episode_id:
                continue
            episode_last_turn[episode_id] = current_turn
            if event_type == "survival_purchase_episode_loaned":
                episode_loan_counts[episode_id] += 1
            if event_type.startswith("survival_purchase_episode_"):
                episode_events[episode_id].add(event_type)

    accounts = ((state.get("debt_ledger") or {}).get("accounts") or {})
    agents = state.get("agents") or {}

    for account in accounts.values():
        if not isinstance(account, dict):
            continue
        if str(account.get("status") or "") != "active":
            continue
        debtor_id = str(account.get("debtor_id") or "")
        debtor = agents.get(debtor_id) if isinstance(agents, dict) else None
        assert isinstance(debtor, dict)
        assert bool(debtor.get("is_alive", True)) is True
        assert bool(debtor.get("has_left_zone")) is False
        assert bool(debtor.get("escaped_due_to_debt")) is False
        assert bool(debtor.get("debt_escape_completed")) is False

    for agent in agents.values():
        if not isinstance(agent, dict):
            continue
        if not bool(agent.get("is_alive", True)):
            continue
        if not bool(agent.get("global_goal_achieved")):
            continue
        assert bool(agent.get("has_left_zone")) or bool((agent.get("exit_zone_mode") or {}).get("active"))

    final_turn = int(state.get("world_turn") or 0)
    for episode_id, loan_count in episode_loan_counts.items():
        assert loan_count <= 1, episode_id
        kinds = episode_events.get(episode_id, set())
        if "survival_purchase_episode_loaned" not in kinds:
            continue
        if (
            "survival_purchase_episode_bought" in kinds
            or "survival_purchase_episode_consumed" in kinds
            or "survival_purchase_episode_failed" in kinds
            or "survival_purchase_episode_reloan_blocked" in kinds
        ):
            continue
        # Allow in-progress episode only near the end of the smoke window.
        assert final_turn - int(episode_last_turn.get(episode_id, final_turn)) <= 30
