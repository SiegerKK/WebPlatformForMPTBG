from __future__ import annotations

from app.games.zone_stalkers.decision.plan_monitor import assess_scheduled_action_v3, is_v3_monitored_bot


def _base_agent() -> dict:
    return {
        "id": "bot1",
        "archetype": "stalker_agent",
        "controller": {"kind": "bot"},
        "is_alive": True,
        "has_left_zone": False,
        "hp": 100,
        "hunger": 0,
        "thirst": 0,
        "global_goal": "get_rich",
    }


def _base_state() -> dict:
    return {"world_minute": 0}


def test_is_v3_monitored_bot_true_for_alive_stalker_bot() -> None:
    assert is_v3_monitored_bot(_base_agent()) is True


def test_is_v3_monitored_bot_false_for_human() -> None:
    agent = _base_agent()
    agent["controller"] = {"kind": "human"}
    assert is_v3_monitored_bot(agent) is False


def test_assess_aborts_on_critical_thirst() -> None:
    agent = _base_agent()
    agent["thirst"] = 96
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "travel", "turns_remaining": 5},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "abort"
    assert result.reason == "critical_thirst"
    assert result.dominant_pressure == "thirst"


def test_assess_continues_emergency_flee() -> None:
    agent = _base_agent()
    agent["thirst"] = 99
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "travel", "turns_remaining": 5, "emergency_flee": True},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "continue"
    assert result.interruptible is False


def test_assess_projects_hour_boundary_values() -> None:
    agent = _base_agent()
    agent["thirst"] = 86
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "explore_anomaly_location", "turns_remaining": 10},
        state={"world_minute": 59},
        world_turn=100,
    )
    assert result.decision == "abort"
    assert result.reason == "critical_thirst"


def test_assess_preserves_objective_and_intent_in_debug_context() -> None:
    agent = _base_agent()
    agent["brain_v3_context"] = {
        "objective_key": "GET_MONEY_FOR_RESUPPLY",
        "intent_kind": "get_rich",
        "support_objective_for": "kill_stalker",
        "combat_ready": False,
        "not_attacking_reasons": ["target_too_strong", "no_armor"],
    }
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "explore_anomaly_location", "turns_remaining": 5},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "continue"
    assert result.debug_context is not None
    assert result.debug_context["objective_key"] == "GET_MONEY_FOR_RESUPPLY"
    assert result.debug_context["intent_kind"] == "get_rich"
    assert result.debug_context["support_objective_for"] == "kill_stalker"
    assert result.debug_context["not_attacking_reasons"] == ["target_too_strong", "no_armor"]


def test_support_source_exhausted_interrupts_explore_action() -> None:
    agent = _base_agent()
    agent["location_id"] = "loc_a"
    agent["brain_v3_context"] = {"objective_key": "GET_MONEY_FOR_RESUPPLY", "intent_kind": "get_rich"}
    agent["memory_v3"] = {
        "records": {
            "m1": {
                "kind": "anomaly_search_exhausted",
                "created_turn": 95,
                "location_id": "loc_a",
                "details": {
                    "action_kind": "anomaly_search_exhausted",
                    "objective_key": "GET_MONEY_FOR_RESUPPLY",
                    "location_id": "loc_a",
                    "cooldown_until_turn": 130,
                },
            }
        }
    }
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "explore_anomaly_location", "turns_remaining": 5, "target_id": "loc_a"},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "abort"
    assert result.reason == "support_source_exhausted"


def test_combat_ready_visible_target_interrupts_support_action() -> None:
    agent = _base_agent()
    agent["global_goal"] = "kill_stalker"
    agent["brain_v3_context"] = {
        "objective_key": "GET_MONEY_FOR_RESUPPLY",
        "intent_kind": "get_rich",
        "combat_ready": True,
        "hunt_target_belief": {"visible_now": True, "co_located": True, "target_id": "enemy_1"},
    }
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "explore_anomaly_location", "turns_remaining": 5},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "abort"
    assert result.reason == "target_visible_and_combat_ready"


def test_soft_rest_need_allows_short_action_to_finish() -> None:
    agent = _base_agent()
    agent["sleepiness"] = 85
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "explore_anomaly_location", "turns_remaining": 2},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "continue"


def test_soft_rest_need_interrupts_long_action() -> None:
    agent = _base_agent()
    agent["sleepiness"] = 85
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "explore_anomaly_location", "turns_remaining": 8},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "abort"
    assert result.reason == "soft_restore_needs_interrupt"
    assert result.debug_context is not None
    assert result.debug_context["sleep_need"]["scale"] == "sleepiness_high_means_tired"


def test_critical_thirst_does_not_abort_restore_water_travel_to_trader() -> None:
    agent = _base_agent()
    agent["thirst"] = 95
    agent["brain_v3_context"] = {
        "objective_key": "RESTORE_WATER",
        "intent_kind": "seek_water",
    }
    agent["active_plan_v3"] = {
        "current_step_index": 0,
        "steps": [
            {
                "kind": "travel_to_location",
                "payload": {"reason": "buy_drink_survival"},
            }
        ],
    }
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "travel", "turns_remaining": 5},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "continue"
    assert result.reason == "critical_need_recovery_in_progress"


def test_critical_thirst_aborts_unrelated_explore_action() -> None:
    agent = _base_agent()
    agent["thirst"] = 95
    agent["brain_v3_context"] = {
        "objective_key": "GET_MONEY_FOR_RESUPPLY",
        "intent_kind": "get_rich",
    }
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "explore_anomaly_location", "turns_remaining": 10},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "abort"
    assert result.reason == "critical_thirst"


def test_critical_hunger_does_not_abort_restore_food_buy_consume_chain() -> None:
    agent = _base_agent()
    agent["hunger"] = 95
    agent["brain_v3_context"] = {
        "objective_key": "RESTORE_FOOD",
        "intent_kind": "seek_food",
    }
    agent["active_plan_v3"] = {
        "current_step_index": 1,
        "steps": [
            {"kind": "travel_to_location", "payload": {"reason": "buy_food_survival"}},
            {"kind": "trade_buy_item", "payload": {"item_category": "food", "reason": "buy_food_survival"}},
            {"kind": "consume_item", "payload": {"item_type": "bread", "reason": "emergency_food"}},
        ],
    }
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "travel", "turns_remaining": 4},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "continue"
    assert result.reason == "critical_need_recovery_in_progress"


def test_critical_hp_does_not_abort_heal_self_recovery_action() -> None:
    agent = _base_agent()
    agent["hp"] = 5
    agent["brain_v3_context"] = {
        "objective_key": "HEAL_SELF",
        "intent_kind": "heal_self",
    }
    agent["active_plan_v3"] = {
        "current_step_index": 1,
        "steps": [
            {"kind": "travel_to_location", "payload": {"reason": "buy_medical"}},
            {"kind": "trade_buy_item", "payload": {"item_category": "medical", "reason": "buy_medical"}},
            {"kind": "consume_item", "payload": {"item_type": "medkit", "reason": "heal_self"}},
        ],
    }
    result = assess_scheduled_action_v3(
        agent_id="bot1",
        agent=agent,
        scheduled_action={"type": "travel", "turns_remaining": 2},
        state=_base_state(),
        world_turn=100,
    )
    assert result.decision == "continue"
    assert result.reason == "critical_need_recovery_in_progress"
