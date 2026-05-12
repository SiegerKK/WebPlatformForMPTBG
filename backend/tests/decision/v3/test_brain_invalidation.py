from __future__ import annotations

from app.games.zone_stalkers.decision.brain_runtime import (
    ensure_brain_runtime,
    highest_invalidator_priority,
    invalidate_brain,
    should_run_brain,
)
from app.games.zone_stalkers.rules.tick_rules import _add_memory, tick_zone_map


def _base_state() -> dict:
    return {
        "seed": 1,
        "world_turn": 100,
        "world_day": 1,
        "world_hour": 12,
        "world_minute": 0,
        "emission_active": False,
        "emission_scheduled_turn": None,
        "emission_warning_written_turn": None,
        "emission_warning_offset": None,
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


def _bot(agent_id: str) -> dict:
    return {
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
        "inventory": [{"id": "ammo1", "type": "ammo_9mm", "value": 0}],
        "action_queue": [],
        "scheduled_action": None,
        "active_plan_v3": None,
        "brain_v3_context": {"objective_key": "IDLE", "intent_kind": "idle"},
    }


def test_brain_skips_when_valid_plan_not_invalidated() -> None:
    agent = _bot("bot1")
    agent["active_plan_v3"] = {"status": "active"}
    runtime = ensure_brain_runtime(agent, 100)
    runtime["valid_until_turn"] = 130
    runtime["invalidated"] = False

    should_run, reason = should_run_brain(agent, 100)

    assert should_run is False
    assert reason == "cached_until_valid"


def test_plan_completed_invalidates_brain() -> None:
    state = _base_state()
    agent = _bot("bot1")
    state["agents"]["bot1"] = agent
    state["locations"]["loc_a"]["agents"] = ["bot1"]
    ensure_brain_runtime(agent, 100)

    _add_memory(
        agent,
        100,
        state,
        "decision",
        "done",
        {"action_kind": "active_plan_completed"},
    )

    br = agent["brain_runtime"]
    assert br["invalidated"] is True
    assert br["invalidators"][-1]["reason"] == "plan_completed"
    assert br["invalidators"][-1]["priority"] == "high"


def test_target_seen_invalidates_and_runs_immediately() -> None:
    state = _base_state()
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 0,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }
    bot = _bot("bot1")
    ensure_brain_runtime(bot, 100)["valid_until_turn"] = 999
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    _add_memory(
        bot,
        100,
        state,
        "observation",
        "seen",
        {"action_kind": "target_seen", "target_id": "t1", "location_id": "loc_a"},
    )

    new_state, _ = tick_zone_map(state)
    br = new_state["agents"]["bot1"]["brain_runtime"]
    assert br["last_decision_turn"] == 100
    assert br["invalidated"] is False


def test_emission_warning_invalidates_all_exposed_agents() -> None:
    """Emission warning urgently invalidates agents via _add_memory."""
    state = _base_state()
    agent = _bot("bot1")
    state["agents"]["bot1"] = agent
    ensure_brain_runtime(agent, 100)

    # Simulate emission-imminent memory (same action_kind the emission section writes).
    _add_memory(
        agent,
        100,
        state,
        "observation",
        "⚠️ Скоро выброс!",
        {"action_kind": "emission_imminent", "turns_until": 10},
    )

    br = agent["brain_runtime"]
    assert br["invalidated"] is True
    assert any(inv.get("reason") == "emission_warning_started" for inv in br.get("invalidators", []))
    assert highest_invalidator_priority(agent) == "urgent"


def test_emission_warning_triggers_brain_run_bypassing_active_plan() -> None:
    """After emission warning, agents with cached active plan still run brain (urgent bypass)."""
    state = _base_state()
    state["emission_scheduled_turn"] = 110
    state["emission_warning_offset"] = 10
    bots = {"bot1": _bot("bot1"), "bot2": _bot("bot2")}
    for bot in bots.values():
        # Pre-set a valid brain cache so the cache wouldn't expire this tick.
        br = ensure_brain_runtime(bot, 100)
        br["valid_until_turn"] = 200
        br["last_decision_turn"] = 50
        # Active plan would normally suppress brain re-evaluation.
        bot["active_plan_v3"] = {
            "status": "active",
            "plan_key": "IDLE",
            "current_step": 0,
            "steps": [],
        }
    state["agents"] = bots
    state["locations"]["loc_a"]["agents"] = ["bot1", "bot2"]

    new_state, events = tick_zone_map(state)

    assert any(ev.get("event_type") == "emission_warning" for ev in events)
    for agent_id in ("bot1", "bot2"):
        br = new_state["agents"][agent_id]["brain_runtime"]
        # Urgent bypass means brain runs this tick despite valid cache + active plan.
        assert br["last_decision_turn"] == 100, (
            f"{agent_id}: brain should have run due to emission urgency (got {br})"
        )


def test_urgent_invalidation_bypasses_scheduled_action() -> None:
    """Urgently invalidated agents run the brain even when a scheduled_action is set."""
    state = _base_state()
    bot = _bot("bot1")
    bot["scheduled_action"] = {
        "type": "sleep",
        "turns_remaining": 50,
        "turns_total": 50,
        "revision": 0,
        "interruptible": True,
        "started_turn": 50,
        "ends_turn": 150,
    }
    ensure_brain_runtime(bot, 100)["valid_until_turn"] = 200
    invalidate_brain(bot, None, reason="critical_thirst", priority="urgent", world_turn=100)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    br = new_state["agents"]["bot1"]["brain_runtime"]
    assert br["last_decision_turn"] == 100, (
        f"Brain should have run despite scheduled_action (got {br})"
    )


def test_urgent_invalidation_bypasses_active_plan() -> None:
    """Urgently invalidated agents run the brain even with an active plan in progress."""
    state = _base_state()
    bot = _bot("bot1")
    bot["active_plan_v3"] = {
        "status": "active",
        "plan_key": "GET_RICH",
        "current_step": 0,
        "steps": [],
    }
    br = ensure_brain_runtime(bot, 100)
    br["valid_until_turn"] = 500
    br["last_decision_turn"] = 50
    invalidate_brain(bot, None, reason="target_seen", priority="urgent", world_turn=100)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    br_after = new_state["agents"]["bot1"]["brain_runtime"]
    assert br_after["last_decision_turn"] == 100, (
        f"Brain should have run despite active plan (got {br_after})"
    )


def test_non_urgent_invalidation_does_not_set_urgent_bypass() -> None:
    """Normal-priority invalidation does not mark the agent as urgently invalidated."""
    agent = _bot("bot1")
    ensure_brain_runtime(agent, 100)["valid_until_turn"] = 500
    invalidate_brain(agent, None, reason="trade_completed", priority="normal", world_turn=100)

    # should_run is True because the agent is invalidated...
    should_run, reason = should_run_brain(agent, 100)
    assert should_run is True
    assert reason == "invalidated"
    # ...but the highest priority is "normal", not "urgent", so no bypass happens.
    assert highest_invalidator_priority(agent) == "normal"


def test_valid_cache_suppresses_brain_run() -> None:
    """should_run_brain returns False when cache is valid and an active plan is present."""
    agent = _bot("bot1")
    br = ensure_brain_runtime(agent, 100)
    br["valid_until_turn"] = 200
    br["last_decision_turn"] = 50
    # Active plan prevents the "no_plan_or_action" fallback.
    agent["active_plan_v3"] = {"status": "active"}

    should_run, reason = should_run_brain(agent, 100)
    assert should_run is False
    assert reason == "cached_until_valid"


def test_stable_agents_with_valid_cache_dont_retrigger_brain(monkeypatch) -> None:
    """Agents with valid brain cache should not retrigger brain each tick (E2E)."""
    from app.games.zone_stalkers.rules import tick_rules as _tr

    # Simulate active plan handling returning handled=True so the plan loop skips brain.
    monkeypatch.setattr(_tr, "_process_active_plan_v3", lambda *a, **kw: (True, []))
    monkeypatch.setattr(_tr, "_run_npc_brain_v3_decision", lambda aid, a, s, t: [])

    state = _base_state()
    agents_out: dict = {}
    for i in range(5):
        bot_id = f"stable_bot{i + 1}"
        bot = _bot(bot_id)
        br = ensure_brain_runtime(bot, 100)
        br["valid_until_turn"] = 200
        br["last_decision_turn"] = 90
        bot["active_plan_v3"] = {"status": "active", "plan_key": "GET_RICH"}
        agents_out[bot_id] = bot

    state["agents"] = agents_out
    state["locations"]["loc_a"]["agents"] = list(agents_out.keys())

    new_state, _ = tick_zone_map(state)

    ran = [
        aid for aid, a in new_state["agents"].items()
        if a.get("brain_runtime", {}).get("last_decision_turn") == 100
    ]
    # Stable cached agents should NOT have triggered a new brain decision this tick.
    assert len(ran) == 0, f"Expected 0 brain runs from cached agents, got {len(ran)}: {ran}"


def test_hunter_reacts_to_new_target_intel_even_with_cache() -> None:
    state = _base_state()
    hunter = _bot("hunter")
    hunter["global_goal"] = "kill_stalker"
    hunter["kill_target_id"] = "target_1"
    hunter["active_plan_v3"] = {"status": "active"}
    ensure_brain_runtime(hunter, 100)["valid_until_turn"] = 500
    state["agents"]["hunter"] = hunter
    state["locations"]["loc_a"]["agents"] = ["hunter"]

    _add_memory(
        hunter,
        100,
        state,
        "observation",
        "intel",
        {
            "action_kind": "intel_from_stalker",
            "target_id": "target_1",
            "location_id": "loc_a",
            "observed": "agent_location",
        },
    )

    should_run, reason = should_run_brain(hunter, 100)
    assert should_run is True
    assert reason == "invalidated"
    assert hunter["brain_runtime"]["invalidators"][-1]["reason"] == "target_intel_received"


def test_high_invalidation_bypasses_active_plan_and_is_queued_or_run(monkeypatch) -> None:
    """High-priority invalidation with active_plan is queued (or run), not lost."""
    from app.games.zone_stalkers.rules import tick_rules as _tr

    monkeypatch.setattr(_tr, "_process_active_plan_v3", lambda *a, **kw: (True, []))
    monkeypatch.setattr(_tr, "_run_npc_brain_v3_decision", lambda aid, a, s, t: [])

    state = _base_state()
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 0,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }
    bot = _bot("bot1")
    bot["active_plan_v3"] = {
        "status": "active",
        "plan_key": "GET_RICH",
        "current_step": 0,
        "steps": [],
    }
    br = ensure_brain_runtime(bot, 100)
    br["valid_until_turn"] = 500
    br["last_decision_turn"] = 50
    # High-priority invalidation — should NOT be silently suppressed by active_plan handler.
    invalidate_brain(bot, None, reason="target_intel_received", priority="high", world_turn=100)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    br_after = new_state["agents"]["bot1"]["brain_runtime"]
    assert br_after.get("last_skip_reason") in {"active_plan_runtime", "budget_deferred"}
    queued = new_state.get("decision_queue", [])
    assert any(q.get("agent_id") == "bot1" for q in queued)


def test_normal_invalidation_with_active_plan_is_budget_deferred_not_lost(monkeypatch) -> None:
    """Normal-priority invalidation with active_plan is budget-deferred, not silently lost."""
    from app.games.zone_stalkers.rules import tick_rules as _tr

    monkeypatch.setattr(_tr, "_process_active_plan_v3", lambda *a, **kw: (True, []))
    monkeypatch.setattr(_tr, "_run_npc_brain_v3_decision", lambda aid, a, s, t: [])

    state = _base_state()
    state["ai_budget"] = {
        "enabled": True,
        "max_normal_decisions_per_tick": 0,
        "max_background_decisions_per_tick": 0,
        "urgent_decisions_ignore_budget": True,
        "max_decision_delay_turns": 10,
    }
    bot = _bot("bot1")
    bot["active_plan_v3"] = {
        "status": "active",
        "plan_key": "GET_RICH",
        "current_step": 0,
        "steps": [],
    }
    br = ensure_brain_runtime(bot, 100)
    br["valid_until_turn"] = 500
    br["last_decision_turn"] = 50
    # Normal-priority invalidation — should enter budget queue, not be silently dropped.
    invalidate_brain(bot, None, reason="trade_completed", priority="normal", world_turn=100)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    new_state, _ = tick_zone_map(state)

    br_after = new_state["agents"]["bot1"]["brain_runtime"]
    assert br_after.get("last_skip_reason") in {"active_plan_runtime", "budget_deferred"}
    queued = new_state.get("decision_queue", [])
    assert any(q.get("agent_id") == "bot1" for q in queued)


def test_goal_completion_invalidation_bypasses_active_plan_before_leave_zone(monkeypatch) -> None:
    """Goal-completion invalidation bypasses active-plan runtime so leave-zone step can't pre-empt brain."""
    from app.games.zone_stalkers.rules import tick_rules as _tr

    state = _base_state()
    bot = _bot("bot1")
    bot["active_plan_v3"] = {
        "status": "active",
        "plan_key": "GET_RICH",
        "current_step": 0,
        "steps": [],
    }
    br = ensure_brain_runtime(bot, 100)
    br["valid_until_turn"] = 500
    br["last_decision_turn"] = 50
    invalidate_brain(bot, None, reason="goal_completed", priority="high", world_turn=100)
    state["agents"]["bot1"] = bot
    state["locations"]["loc_a"]["agents"] = ["bot1"]

    called = {"active_plan": False}

    def _active_plan_runtime(*args, **kwargs):
        called["active_plan"] = True
        # If this executes for goal completion, it could consume the tick and advance to leave-zone path.
        args[1]["has_left_zone"] = True
        return True, []

    monkeypatch.setattr(_tr, "_process_active_plan_v3", _active_plan_runtime)
    monkeypatch.setattr(_tr, "_run_npc_brain_v3_decision", lambda aid, a, s, t: [])

    new_state, _ = tick_zone_map(state)
    br_after = new_state["agents"]["bot1"]["brain_runtime"]

    assert called["active_plan"] is False
    assert new_state["agents"]["bot1"].get("has_left_zone") is False
    queued = any(q.get("agent_id") == "bot1" for q in new_state.get("decision_queue", []))
    ran_now = br_after.get("last_decision_turn") == 100
    assert queued or ran_now
