from __future__ import annotations

from app.games.zone_stalkers.decision.brain_runtime import invalidate_brain
from app.games.zone_stalkers.rules import tick_rules
from app.games.zone_stalkers.rules.tick_rules import tick_zone_map


def _base_state(bot_count: int = 3) -> dict:
    state = {
        "seed": 1,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "agents": {},
        "traders": {},
        "locations": {
            "loc_a": {
                "name": "A",
                "terrain_type": "buildings",
                "anomaly_activity": 0,
                "connections": [],
                "items": [],
                "agents": [],
            },
        },
        "combat_interactions": {},
        "relations": {},
        "groups": {},
    }
    for i in range(bot_count):
        agent_id = f"bot{i + 1}"
        state["agents"][agent_id] = {
            "id": agent_id,
            "archetype": "stalker_agent",
            "controller": {"kind": "bot"},
            "name": agent_id,
            "is_alive": True,
            "has_left_zone": False,
            "action_used": False,
            "location_id": "loc_a",
            "hp": 90,
            "max_hp": 100,
            "radiation": 0,
            "hunger": 20,
            "thirst": 20,
            "sleepiness": 10,
            "money": 100,
            "global_goal": "get_rich",
            "equipment": {"weapon": {"type": "pistol"}, "armor": {"type": "leather_jacket"}},
            "inventory": [{"id": f"ammo{i}", "type": "ammo_9mm", "value": 0}],
            "memory": [],
            "action_queue": [],
            "scheduled_action": None,
            "active_plan_v3": None,
            "brain_v3_context": {"objective_key": "IDLE", "intent_kind": "idle"},
        }
        state["locations"]["loc_a"]["agents"].append(agent_id)
    return state


def _stub_brain_decision(agent_id: str, agent: dict, state: dict, world_turn: int) -> list[dict]:
    agent["current_goal"] = "idle"
    return [{"event_type": "bot_stub_decision", "payload": {"agent_id": agent_id, "world_turn": world_turn}}]


def test_normal_decisions_are_budgeted(monkeypatch) -> None:
    monkeypatch.setattr(tick_rules, "_run_npc_brain_v3_decision", _stub_brain_decision)
    state = _base_state(bot_count=4)
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 1,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }

    new_state, _ = tick_zone_map(state)

    decided = [aid for aid, a in new_state["agents"].items() if a.get("brain_runtime", {}).get("last_decision_turn") == 100]
    deferred = [aid for aid, a in new_state["agents"].items() if a.get("brain_runtime", {}).get("last_skip_reason") == "budget_deferred"]
    assert len(decided) == 1
    assert len(deferred) == 3
    assert len(new_state.get("decision_queue", [])) == 3


def test_urgent_decisions_ignore_budget(monkeypatch) -> None:
    monkeypatch.setattr(tick_rules, "_run_npc_brain_v3_decision", _stub_brain_decision)
    state = _base_state(bot_count=3)
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 0,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }
    urgent_agent = state["agents"]["bot1"]
    invalidate_brain(urgent_agent, None, reason="target_seen", priority="urgent", world_turn=100)

    new_state, _ = tick_zone_map(state)

    assert new_state["agents"]["bot1"]["brain_runtime"]["last_decision_turn"] == 100
    assert len([q for q in new_state.get("decision_queue", []) if q.get("agent_id") != "bot1"]) == 2


def test_agent_is_not_starved_by_budget(monkeypatch) -> None:
    monkeypatch.setattr(tick_rules, "_run_npc_brain_v3_decision", _stub_brain_decision)
    monkeypatch.setattr(tick_rules, "should_run_brain", lambda _a, _t: (False, "cached_until_valid"))
    state = _base_state(bot_count=1)
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 0,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 5,
    }
    state["decision_queue"] = [{"agent_id": "bot1", "priority": "low", "reason": "idle_refresh", "queued_turn": 80}]

    new_state, _ = tick_zone_map(state)

    br = new_state["agents"]["bot1"]["brain_runtime"]
    assert br["last_decision_turn"] == 100
    assert br["queued"] is False


def test_critical_need_bypasses_budget(monkeypatch) -> None:
    monkeypatch.setattr(tick_rules, "_run_npc_brain_v3_decision", _stub_brain_decision)
    state = _base_state(bot_count=2)
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 0,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }
    critical = state["agents"]["bot1"]
    critical["thirst"] = 100

    new_state, _ = tick_zone_map(state)

    assert new_state["agents"]["bot1"]["brain_runtime"]["last_decision_turn"] == 100
    assert any(q.get("agent_id") == "bot2" for q in new_state.get("decision_queue", []))
