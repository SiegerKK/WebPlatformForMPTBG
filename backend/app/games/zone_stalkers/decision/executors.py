"""executors — execute the current PlanStep for an agent.

``execute_plan_step(ctx, plan, state, world_turn)`` performs the concrete
in-world action corresponding to the current step, applying side effects
to the mutable ``agent`` dict and ``state``.

Executor invariants (spec §4.5 / migration plan §9):
    - Executors apply game effects (consume items, travel, trade, heal).
    - Executors do NOT choose strategy, select motives, or pick social intents.
    - Executors write memory entries only for the action just performed.
    - Executors return a list of game events.

During Phase 1–4 migration, most steps delegate to existing tick_rules helpers
to avoid behaviour divergence.  The STEP_LEGACY_SCHEDULED_ACTION kind is a
special passthrough for steps that have no dedicated executor yet.
"""
from __future__ import annotations

from typing import Any

from .models.agent_context import AgentContext


# ── Standalone v3 memory helpers (no tick_rules dependency) ──────────────────
def _v3r_ex(agent: "dict[str, Any]") -> "list[dict[str, Any]]":
    """Return memory_v3 records sorted newest-first."""
    mv3 = agent.get("memory_v3")
    records = mv3.get("records", {}) if isinstance(mv3, dict) else {}
    return sorted(records.values(), key=lambda r: int(r.get("created_turn") or 0), reverse=True)


def _v3ak_ex(rec: "dict[str, Any]") -> str:
    """Return original action_kind (details.action_kind first, then rec.kind)."""
    d = rec.get("details") or {}
    return str(d.get("action_kind") or rec.get("kind") or "")


def _v3fx_ex(rec: "dict[str, Any]") -> "dict[str, Any]":
    """Return details dict safely."""
    return dict(rec.get("details") or {})


def _v3mt_ex(rec: "dict[str, Any]") -> str:
    """Return memory_type from details."""
    return str((rec.get("details") or {}).get("memory_type") or "")
from .models.plan import (
    Plan, PlanStep,
    STEP_TRAVEL_TO_LOCATION,
    STEP_SLEEP_FOR_HOURS,
    STEP_EXPLORE_LOCATION,
    STEP_TRADE_BUY_ITEM,
    STEP_TRADE_SELL_ITEM,
    STEP_CONSUME_ITEM,
    STEP_EQUIP_ITEM,
    STEP_PICKUP_ITEM,
    STEP_LOOT_CORPSE,
    STEP_ASK_FOR_INTEL,
    STEP_LOOK_FOR_TRACKS,
    STEP_QUESTION_WITNESSES,
    STEP_SEARCH_TARGET,
    STEP_START_COMBAT,
    STEP_MONITOR_COMBAT,
    STEP_CONFIRM_KILL,
    STEP_LEAVE_ZONE,
    STEP_WAIT,
    STEP_LEGACY_SCHEDULED_ACTION,
    STEP_HEAL_SELF,
    STEP_REQUEST_LOAN,
    STEP_REPAY_DEBT,
)
from app.games.zone_stalkers.economy.debts import (
    advance_survival_credit,
    can_request_survival_credit,
    choose_debt_repayment_amount,
    repay_debt_account,
    repay_debts_to_creditor_if_useful,
)

_SEARCH_EXHAUSTION_THRESHOLD = 3
_SEARCH_LOCATION_COOLDOWN_TURNS = 300
_SEARCH_LOCATION_HUB_COOLDOWN_TURNS = 90
# Fix 6 — cooldown turns after exhausting a witness source at a location
WITNESS_SOURCE_COOLDOWN_TURNS = 180
TRADE_FAIL_TRADER_NO_MONEY = "trader_no_money"
_SURVIVAL_EPISODE_WINDOW_TURNS = 30


def _begin_survival_episode(
    *,
    agent_id: str,
    agent: dict[str, Any],
    category: str,
    expected_item_type: str,
    required_price: int,
    world_turn: int,
) -> tuple[dict[str, Any], bool]:
    state = agent.get("survival_episode_state")
    if not isinstance(state, dict):
        state = {}
    same_category_active = (
        str(state.get("category") or "") == str(category)
        and str(state.get("status") or "") in {"started", "loaned"}
        and int(world_turn) - int(state.get("started_turn") or 0) < _SURVIVAL_EPISODE_WINDOW_TURNS
    )
    if same_category_active:
        return state, False

    episode_id = str(state.get("last_episode_id") or "")
    if not episode_id or str(state.get("category") or "") != str(category):
        episode_id = f"survival_{category}_{int(world_turn)}_{agent_id}"
    state = {
        "last_episode_id": episode_id,
        "category": str(category),
        "expected_item_type": str(expected_item_type or ""),
        "required_price": int(required_price or 0),
        "started_turn": int(world_turn),
        "loan_turn": None,
        "buy_turn": None,
        "consume_turn": None,
        "status": "started",
        "failure_reason": None,
    }
    agent["survival_episode_state"] = state
    return state, True


def _mark_survival_episode_failed(
    *,
    agent: dict[str, Any],
    reason: str,
    world_turn: int,
) -> dict[str, Any] | None:
    episode = agent.get("survival_episode_state")
    if not isinstance(episode, dict):
        return None
    episode["status"] = "failed"
    episode["failure_reason"] = str(reason)
    episode["failed_turn"] = int(world_turn)
    return episode


def _survival_episode_event(event_type: str, episode: dict[str, Any], world_turn: int) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "payload": {
            "survival_episode_id": str(episode.get("last_episode_id") or ""),
            "survival_episode_category": str(episode.get("category") or ""),
            "expected_item_type": str(episode.get("expected_item_type") or ""),
            "required_price": int(episode.get("required_price") or 0),
            "status": str(episode.get("status") or ""),
            "failure_reason": episode.get("failure_reason"),
            "world_turn": int(world_turn),
        },
    }


def execute_plan_step(
    ctx: AgentContext,
    plan: Plan,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Execute the current step of ``plan`` and return any emitted events.

    If the step completes this tick, ``plan.advance()`` is called so the
    planner knows to move to the next step next tick.

    Parameters
    ----------
    ctx
        AgentContext (read-only snapshot built this tick).
    plan
        The active Plan; ``plan.current_step`` is executed.
    state
        Mutable world state.
    world_turn
        Current world turn.

    Returns
    -------
    list[dict]
        Game events emitted by this step.
    """
    step = plan.current_step
    if step is None:
        return []

    agent_id = ctx.agent_id
    agent = state["agents"][agent_id]     # mutable reference
    if isinstance(agent, dict):
        agent.setdefault("id", agent_id)

    dispatch: dict[str, Any] = {
        STEP_TRAVEL_TO_LOCATION:   _exec_travel,
        STEP_SLEEP_FOR_HOURS:      _exec_sleep,
        STEP_EXPLORE_LOCATION:     _exec_explore,
        STEP_TRADE_BUY_ITEM:       _exec_trade_buy,
        STEP_TRADE_SELL_ITEM:      _exec_trade_sell,
        STEP_CONSUME_ITEM:         _exec_consume,
        STEP_EQUIP_ITEM:           _exec_equip,
        STEP_PICKUP_ITEM:          _exec_pickup,
        STEP_LOOT_CORPSE:          _exec_loot_corpse,
        STEP_HEAL_SELF:            _exec_consume,   # same as consume
        STEP_ASK_FOR_INTEL:        _exec_ask_for_intel,
        STEP_LOOK_FOR_TRACKS:      _exec_look_for_tracks,
        STEP_QUESTION_WITNESSES:   _exec_question_witnesses,
        STEP_SEARCH_TARGET:        _exec_search_target,
        STEP_START_COMBAT:         _exec_start_combat,
        STEP_MONITOR_COMBAT:       _exec_monitor_combat,
        STEP_CONFIRM_KILL:         _exec_confirm_kill,
        STEP_LEAVE_ZONE:           _exec_leave_zone,
        STEP_WAIT:                 _exec_wait,
        STEP_REPAY_DEBT:           _exec_repay_debt,
        STEP_LEGACY_SCHEDULED_ACTION: _exec_legacy_passthrough,
        STEP_REQUEST_LOAN:         _exec_request_loan,
    }

    executor = dispatch.get(step.kind, _exec_unknown)
    events = executor(agent_id, agent, step, ctx, state, world_turn)

    # If the step is a one-tick action (not a scheduled multi-tick action),
    # advance the plan index immediately.
    if step.kind in (
        STEP_CONSUME_ITEM, STEP_EQUIP_ITEM, STEP_PICKUP_ITEM, STEP_LOOT_CORPSE,
        STEP_HEAL_SELF, STEP_WAIT,
        STEP_ASK_FOR_INTEL, STEP_LOOK_FOR_TRACKS, STEP_QUESTION_WITNESSES,
        STEP_SEARCH_TARGET, STEP_START_COMBAT, STEP_CONFIRM_KILL,
    ):
        plan.advance()
    elif step.kind == STEP_TRADE_BUY_ITEM:
        if _trade_buy_succeeded(events):
            plan.advance()
        else:
            step.payload["_trade_buy_failed"] = True
            step.payload["_failure_reason"] = step.payload.get("_failure_reason") or "trade_buy_failed"
    elif step.kind == STEP_TRADE_SELL_ITEM:
        if _trade_sell_succeeded(events):
            plan.advance()
        else:
            step.payload["_trade_sell_failed"] = True
            step.payload["_failure_reason"] = step.payload.get("_failure_reason") or "no_items_sold"
    elif step.kind == STEP_MONITOR_COMBAT and bool(step.payload.get("_monitor_complete")):
        plan.advance()
    elif step.kind == STEP_REQUEST_LOAN:
        if _loan_request_succeeded(events):
            plan.advance()
        else:
            step.payload["_loan_failed"] = True
            step.payload["_failure_reason"] = step.payload.get("_failure_reason") or "loan_request_failed"
    elif step.kind == STEP_REPAY_DEBT:
        if any(isinstance(ev, dict) and str(ev.get("event_type") or "") in {"debt_payment", "debt_repaid"} for ev in events):
            plan.advance()
        else:
            step.payload["_failure_reason"] = step.payload.get("_failure_reason") or "no_safe_repayment_amount"

    return events


# ── Step executors ─────────────────────────────────────────────────────────────

def _exec_travel(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Delegate to the existing _bot_schedule_travel helper."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_schedule_travel, _add_memory
    target_id = step.payload.get("target_id")
    if not target_id:
        return []
    reason = step.payload.get("reason", "")
    emergency_flee = (reason == "flee_emission")

    # Write goal-specific decision memory entries (v1 compat)
    if reason == "flee_emission":
        _write_once_decision(agent, world_turn, state,
            "🚨 Бегу от выброса",
            {"action_kind": "flee_emission", "destination": target_id},
            f"Бегу в безопасное место: {target_id}")
    elif reason in ("heal_self", "emergency_heal", "seek_medical", "buy_heal"):
        _write_once_by_dest(agent, world_turn, state, "seek_item", "medical", target_id,
                            emergency=True)
    elif reason in ("seek_food", "emergency_food", "buy_food", "buy_food_survival", "buy_food_stock"):
        _write_once_by_dest(agent, world_turn, state, "seek_item", "food", target_id,
                            emergency=True)
    elif reason in ("seek_drink", "emergency_drink", "buy_drink", "buy_drink_survival", "buy_drink_stock"):
        _write_once_by_dest(agent, world_turn, state, "seek_item", "drink", target_id,
                            emergency=True)
    elif reason in ("sell_artifacts", "sell_artifacts_get_rich"):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART_TYPES
        _art_set = frozenset(_ART_TYPES.keys())
        _artifacts_count = sum(1 for i in agent.get("inventory", []) if i.get("type") in _art_set)
        _write_once_decision(agent, world_turn, state,
            "🎁 Иду продавать артефакты",
            {"action_kind": "sell_artifacts", "artifacts_count": _artifacts_count,
             "destination": target_id},
            f"Иду к торговцу в {target_id} продавать {_artifacts_count} артефакт(ов)")
    elif reason == "get_rich_travel_to_anomaly":
        # Write a decision record only when the target anomaly location changes.
        _prev_anomaly_target = next(
            (_v3fx_ex(r).get("target_location_id")
             for r in _v3r_ex(agent)
             if _v3mt_ex(r) == "decision"
             and _v3ak_ex(r) == "anomaly_search_target_chosen"),
            None,
        )
        if _prev_anomaly_target != target_id:
            _target_loc_name = state.get("locations", {}).get(target_id, {}).get("name", target_id)
            _add_memory(
                agent, world_turn, state, "decision",
                f"🗺️ Выбрал локацию для поиска: «{_target_loc_name}»",
                {"action_kind": "anomaly_search_target_chosen",
                 "target_location_id": target_id},
                summary=f"Отправляюсь искать артефакты в «{_target_loc_name}»",
            )

    # If agent has artifacts and destination is a trader location, record sell intent
    # (anti-spam: _write_once_decision skips if already written this turn)
    if reason not in ("sell_artifacts", "sell_artifacts_get_rich", "flee_emission"):
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART_TYPES_CHK
        from app.games.zone_stalkers.rules.tick_rules import _find_trader_at_location
        _art_set_chk = frozenset(_ART_TYPES_CHK.keys())
        _has_arts = any(i.get("type") in _art_set_chk for i in agent.get("inventory", []))
        if _has_arts and _find_trader_at_location(target_id, state) is not None:
            _arts_cnt = sum(1 for i in agent.get("inventory", []) if i.get("type") in _art_set_chk)
            _write_once_decision(agent, world_turn, state,
                "🎁 Иду продавать артефакты",
                {"action_kind": "sell_artifacts", "artifacts_count": _arts_cnt,
                 "destination": target_id},
                f"Иду к торговцу в {target_id} продавать {_arts_cnt} артефакт(ов)")

    return _bot_schedule_travel(agent_id, agent, target_id, state, world_turn,
                                emergency_flee=emergency_flee)


def _exec_sleep(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Schedule a sleep action."""
    from app.games.zone_stalkers.rules.tick_rules import DEFAULT_SLEEP_HOURS
    hours = max(1, int(step.payload.get("hours", DEFAULT_SLEEP_HOURS)))
    from app.games.zone_stalkers.rules.tick_rules import MINUTES_PER_TURN
    turns = hours * 60 // MINUTES_PER_TURN
    agent["scheduled_action"] = {
        "type": "sleep",
        "hours": hours,
        "turns_remaining": turns,
        "turns_total": turns,
        "started_turn": world_turn,
        "ends_turn": world_turn + turns,
        "revision": 1,
        "interruptible": True,
        "sleep_progress_turns": 0,
        "sleep_intervals_applied": 0,
        "sleep_turns_slept": 0,
    }
    agent["action_used"] = True
    return []


def _exec_explore(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Schedule an exploration action."""
    from app.games.zone_stalkers.rules.tick_rules import EXPLORE_DURATION_TURNS
    target_id = step.payload.get("target_id", agent.get("location_id", ""))
    loc_name = state.get("locations", {}).get(target_id, {}).get("name", target_id)
    _write_once_decision(agent, world_turn, state,
        "🔍 Исследую аномальную зону",
        {"action_kind": "explore_decision", "target_id": target_id},
        f"Решил исследовать локацию «{loc_name}» в поисках артефактов")
    agent["scheduled_action"] = {
        "type": "explore_anomaly_location",
        "target_id": target_id,
        "turns_remaining": EXPLORE_DURATION_TURNS,
        "turns_total": EXPLORE_DURATION_TURNS,
        "started_turn": world_turn,
        "ends_turn": world_turn + EXPLORE_DURATION_TURNS,
        "revision": 1,
        "interruptible": True,
    }
    agent["action_used"] = True
    return [{"event_type": "exploration_started",
             "payload": {"agent_id": agent_id, "location_id": target_id}}]


def _exec_trade_buy(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Attempt to buy from a co-located trader."""
    from app.games.zone_stalkers.balance.items import (
        HEAL_ITEM_TYPES, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES,
        WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES, AMMO_ITEM_TYPES,
        AMMO_FOR_WEAPON,
    )
    from app.games.zone_stalkers.rules.tick_rules import _bot_buy_from_trader

    category = step.payload.get("item_category", "medical")
    buy_mode = step.payload.get("buy_mode")
    agent_loc = str(agent.get("location_id") or "")

    # Upgrade categories: buy the best upgrade target and immediately equip it.
    if category in ("weapon_upgrade", "armor_upgrade"):
        slot = "weapon" if category == "weapon_upgrade" else "armor"
        from app.games.zone_stalkers.balance.items import (
            WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES, ITEM_TYPES as _ITEM_TYPES,
            weapon_class_for_item_type, armor_class_for_item_type,
        )
        from app.games.zone_stalkers.rules.tick_rules import (
            _find_upgrade_target, _bot_equip_from_inventory,
        )
        from app.games.zone_stalkers.decision.constants import WEAPON_CLASS_RANK, ARMOR_CLASS_RANK
        item_types_for_slot = WEAPON_ITEM_TYPES if slot == "weapon" else ARMOR_ITEM_TYPES
        agent_risk = float(agent.get("risk_tolerance", 0.5))
        agent_money = agent.get("money", 0)
        current = agent.get("equipment", {}).get(slot)
        current_type = current.get("type") if isinstance(current, dict) else None

        # Honor min class requirement from hunt preparation payload
        min_weapon_class = step.payload.get("min_weapon_class")
        min_armor_class = step.payload.get("min_armor_class")
        min_required_rank = None
        if slot == "weapon" and min_weapon_class:
            min_required_rank = WEAPON_CLASS_RANK.get(str(min_weapon_class), 0)
        elif slot == "armor" and min_armor_class:
            min_required_rank = ARMOR_CLASS_RANK.get(str(min_armor_class), 0)

        if min_required_rank is not None and min_required_rank > 0:
            # Filter to items that meet the minimum class requirement AND are affordable.
            # Use item-class helpers because item type keys like "ak74" differ from
            # class names like "rifle" in WEAPON_CLASS_RANK.
            class_rank_map = WEAPON_CLASS_RANK if slot == "weapon" else ARMOR_CLASS_RANK
            get_item_class = weapon_class_for_item_type if slot == "weapon" else armor_class_for_item_type
            candidates = [
                t for t in item_types_for_slot
                if class_rank_map.get(get_item_class(t), 0) >= min_required_rank
                and t in _ITEM_TYPES
                and agent_money >= int(_ITEM_TYPES[t].get("value", 0) * 1.5)
            ]
            if not candidates:
                return [_make_trade_buy_failed_event(
                    agent_id=agent_id,
                    reason="no_matching_upgrade",
                    item_category=category,
                    buy_mode=buy_mode,
                    location_id=agent_loc,
                    required_price=None,
                )]
            # Sort by class rank first, then by price (buy cheapest that meets requirement)
            candidates.sort(key=lambda t: (class_rank_map.get(get_item_class(t), 0), int(_ITEM_TYPES[t].get("value", 0) * 1.5)))
            upgrade_key: str | None = candidates[0]
        else:
            upgrade_key = _find_upgrade_target(item_types_for_slot, current_type, agent_risk, agent_money)

        if upgrade_key is None:
            return [_make_trade_buy_failed_event(
                agent_id=agent_id,
                reason="no_upgrade_target",
                item_category=category,
                buy_mode=buy_mode,
                location_id=agent_loc,
            )]
        bought = _bot_buy_from_trader(agent_id, agent, frozenset([upgrade_key]), state, world_turn,
                                      purchase_reason=f"апгрейд {slot}") or []
        if bought:
            equip_evs = _bot_equip_from_inventory(
                agent_id, agent, frozenset([upgrade_key]), slot, state, world_turn,
            )
            return bought + equip_evs
        return [_make_trade_buy_failed_event(
            agent_id=agent_id,
            reason="not_enough_money",
            item_category=category,
            buy_mode=buy_mode,
            location_id=agent_loc,
        )]

    # Defensive guard: never buy equipment the agent already has equipped.
    # Emit an explicit skipped-success event so active-plan runtime can advance.
    eq = agent.get("equipment", {})
    if category in ("weapon", "equipment") and eq.get("weapon") is not None:
        return [_make_trade_buy_skipped_event(
            agent_id=agent_id,
            item_category=category,
            buy_mode=buy_mode,
            location_id=agent_loc,
            reason="already_equipped",
        )]
    if category == "armor" and eq.get("armor") is not None:
        return [_make_trade_buy_skipped_event(
            agent_id=agent_id,
            item_category=category,
            buy_mode=buy_mode,
            location_id=agent_loc,
            reason="already_equipped",
        )]

    if category == "ammo":
        # Only buy ammo that is compatible with the agent's equipped weapon.
        weapon = eq.get("weapon")
        weapon_type = weapon.get("type") if isinstance(weapon, dict) else None
        required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
        if not required_ammo:
            return [_make_trade_buy_failed_event(
                agent_id=agent_id,
                reason="no_weapon_for_ammo",
                item_category=category,
                buy_mode=buy_mode,
                location_id=agent_loc,
            )]
        item_types: "frozenset[str]" = frozenset([required_ammo])
    else:
        type_map = {
            "medical": HEAL_ITEM_TYPES,
            "food": FOOD_ITEM_TYPES,
            "drink": DRINK_ITEM_TYPES,
            "weapon": WEAPON_ITEM_TYPES,
            "armor": ARMOR_ITEM_TYPES,
            "equipment": WEAPON_ITEM_TYPES,  # legacy fallback; prefer explicit "weapon"/"armor"
        }
        item_types = type_map.get(category, HEAL_ITEM_TYPES)

    # PR2 survival mode: force cheapest affordable viable item.
    compatible_item_types = step.payload.get("compatible_item_types")
    compatible_item_types_list: list[str] = []
    if isinstance(compatible_item_types, list) and compatible_item_types:
        compatible_item_types_list = [str(t) for t in compatible_item_types]
        item_types = frozenset(compatible_item_types_list)

    required_price_from_payload = step.payload.get("required_price")
    expected_item_type = str(step.payload.get("expected_item_type") or "")
    previous_step_was_survival_credit = bool(step.payload.get("previous_step_was_survival_credit"))
    agent_money = int(agent.get("money") or 0)

    if buy_mode == "survival_cheapest":
        from app.games.zone_stalkers.balance.items import ITEM_TYPES

        affordable = sorted(
            (
                t for t in item_types
                if t in ITEM_TYPES and agent.get("money", 0) >= int(ITEM_TYPES[t].get("value", 0) * 1.5)
            ),
            key=lambda t: (int(ITEM_TYPES[t].get("value", 0) * 1.5), t),
        )
        if not affordable:
            return [_make_trade_buy_failed_event(
                agent_id=agent_id,
                reason="not_enough_money",
                item_category=category,
                buy_mode=buy_mode,
                location_id=agent_loc,
                required_price=required_price_from_payload,
                agent_money=agent_money,
                compatible_item_types=compatible_item_types_list,
                expected_item_type=expected_item_type,
                previous_step_was_survival_credit=previous_step_was_survival_credit,
            )]
        item_types = frozenset([affordable[0]])
    elif buy_mode == "reserve_basic":
        from app.games.zone_stalkers.balance.items import ITEM_TYPES

        preferred = step.payload.get("preferred_item_types") or []
        preferred_set = frozenset(str(t) for t in preferred if str(t) in item_types)
        candidate_pool = preferred_set or item_types
        affordable = sorted(
            (
                t for t in candidate_pool
                if t in ITEM_TYPES and agent.get("money", 0) >= int(ITEM_TYPES[t].get("value", 0) * 1.5)
            ),
            key=lambda t: (int(ITEM_TYPES[t].get("value", 0) * 1.5), t),
        )
        if not affordable:
            return [_make_trade_buy_failed_event(
                agent_id=agent_id,
                reason="not_enough_money",
                item_category=category,
                buy_mode=buy_mode,
                location_id=agent_loc,
                required_price=required_price_from_payload,
                agent_money=agent_money,
                compatible_item_types=compatible_item_types_list,
                expected_item_type=expected_item_type,
                previous_step_was_survival_credit=previous_step_was_survival_credit,
            )]
        item_types = frozenset([affordable[0]])

    reason = step.payload.get("reason", f"buy_{category}")
    reason_lower = str(reason).lower()
    critical_needs = bool(
        category in {"drink", "food", "medical"}
        and ("survival" in reason_lower or "emergency" in reason_lower)
    )
    pending_purchase = agent.get("pending_survival_purchase")
    if isinstance(pending_purchase, dict) and int(pending_purchase.get("expires_turn") or 0) < int(world_turn):
        agent["pending_survival_purchase"] = None
        pending_purchase = None

    trader = None
    for trader_id, trader_obj in (state.get("traders") or {}).items():
        if not isinstance(trader_obj, dict):
            continue
        if str(trader_obj.get("location_id") or "") != str(agent.get("location_id") or ""):
            continue
        trader = trader_obj
        trader.setdefault("id", str(trader_id))
        break
    events: list[dict[str, Any]] = []
    if isinstance(trader, dict):
        events.extend(repay_debts_to_creditor_if_useful(
            state=state,
            debtor=agent,
            creditor=trader,
            world_turn=world_turn,
            critical_needs=critical_needs,
        ))
    buy_events = _bot_buy_from_trader(agent_id, agent, item_types, state, world_turn,
                                      purchase_reason=reason) or []
    events.extend(buy_events)

    if buy_events:
        episode = agent.get("survival_episode_state")
        if isinstance(episode, dict) and str(episode.get("status") or "") in {"started", "loaned"}:
            if str(episode.get("category") or "") == str(category):
                episode["buy_turn"] = int(world_turn)
                episode["status"] = "bought"
                episode["failure_reason"] = None
                events.append(_survival_episode_event("survival_purchase_episode_bought", episode, world_turn))
        return events

    # If no purchase happened, emit a failure event so the plan step is not silently skipped.
    events.append(_make_trade_buy_failed_event(
        agent_id=agent_id,
        reason="not_enough_money",
        item_category=category,
        buy_mode=buy_mode,
        location_id=agent_loc,
        required_price=required_price_from_payload,
        agent_money=agent_money,
        compatible_item_types=compatible_item_types_list,
        expected_item_type=expected_item_type,
        previous_step_was_survival_credit=previous_step_was_survival_credit,
    ))
    if previous_step_was_survival_credit or isinstance(pending_purchase, dict):
        failed = _mark_survival_episode_failed(
            agent=agent,
            reason="trade_buy_failed",
            world_turn=world_turn,
        )
        agent["pending_survival_purchase"] = None
        if failed is not None:
            events.append(_survival_episode_event("survival_purchase_episode_failed", failed, world_turn))
    return events



def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or "")


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else event


def _is_trade_sell_success_event(event: dict[str, Any]) -> bool:
    event_type = _event_type(event)
    payload = _event_payload(event)
    # trade_sell — canonical success event from legacy sell paths.
    # bot_sold_artifact / bot_sold_item are the raw events emitted by
    # _bot_sell_to_trader and _bot_sell_items_for_cash respectively.
    # All three represent a successful sale and must advance the plan step.
    if event_type in ("trade_sell", "bot_sold_artifact", "bot_sold_item"):
        return True
    if str(payload.get("action_kind") or "") == "trade_sell":
        return True
    if payload.get("items_sold"):
        return True
    if int(payload.get("money_gained") or payload.get("money_delta") or 0) > 0:
        return True
    return False


def _trade_sell_succeeded(events: list[dict[str, Any]]) -> bool:
    return any(_is_trade_sell_success_event(ev) for ev in events if isinstance(ev, dict))


def _loan_request_succeeded(events: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(ev, dict) and str(ev.get("event_type") or "") in {"debt_created", "debt_credit_advanced"}
        for ev in events
    )


def _trade_buy_succeeded(events: list[dict[str, Any]]) -> bool:
    """Return True if at least one event indicates a successful item purchase or equip."""
    for ev in events:
        if not isinstance(ev, dict):
            continue
        et = str(ev.get("event_type") or "")
        if et in {"bot_bought_item", "item_bought", "trade_buy", "item_equipped", "trade_buy_skipped"}:
            return True
        # Legacy payload-based check
        payload = ev.get("payload") or ev
        if isinstance(payload, dict) and payload.get("items_bought"):
            return True
        if isinstance(payload, dict) and int(payload.get("money_spent") or 0) > 0:
            return True
    return False


def _make_trade_buy_skipped_event(
    *,
    agent_id: str,
    item_category: str,
    buy_mode: str | None,
    location_id: str,
    reason: str = "already_equipped",
) -> dict[str, Any]:
    return {
        "event_type": "trade_buy_skipped",
        "payload": {
            "agent_id": agent_id,
            "reason": reason,
            "item_category": item_category,
            "buy_mode": buy_mode,
            "location_id": location_id,
        },
    }


def _make_trade_buy_failed_event(
    *,
    agent_id: str,
    reason: str,
    item_category: str,
    buy_mode: str | None,
    location_id: str,
    required_price: int | None = None,
    agent_money: int | None = None,
    compatible_item_types: list[str] | None = None,
    expected_item_type: str | None = None,
    previous_step_was_survival_credit: bool | None = None,
) -> dict[str, Any]:
    return {
        "event_type": "trade_buy_failed",
        "payload": {
            "agent_id": agent_id,
            "reason": reason,
            "item_category": item_category,
            "buy_mode": buy_mode,
            "location_id": location_id,
            "required_price": required_price,
            "agent_money": agent_money,
            "compatible_item_types": list(compatible_item_types or []),
            "expected_item_type": expected_item_type,
            "previous_step_was_survival_credit": previous_step_was_survival_credit,
        },
    }


def _trade_sell_failure_reason(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if not isinstance(event, dict):
            continue
        if _event_type(event) != "trade_sell_failed":
            continue
        payload = _event_payload(event)
        reason = str(payload.get("reason") or "").strip()
        if reason:
            return reason
    return None


def _make_trade_sell_failed_event(
    *,
    agent_id: str,
    reason: str,
    location_id: str,
    trader_id: str | None,
    item_types: list[str],
    objective_key: str,
) -> dict[str, Any]:
    return {
        "event_type": "trade_sell_failed",
        "payload": {
            "agent_id": agent_id,
            "reason": reason,
            "location_id": location_id,
            "trader_id": trader_id,
            "item_types": list(item_types),
            "objective_key": objective_key,
        },
    }


def _write_trade_sell_failed_memory(
    *,
    agent_id: str,
    agent: dict[str, Any],
    world_turn: int,
    reason: str,
    location_id: str,
    trader_id: str | None,
    item_types: list[str],
    objective_key: str,
) -> None:
    from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
    write_memory_event_to_v3(
        agent_id=agent_id,
        agent=agent,
        legacy_entry={
            "world_turn": world_turn,
            "type": "action",
            "title": "trade_sell_failed",
            "effects": {
                "action_kind": "trade_sell_failed",
                "reason": reason,
                "location_id": location_id,
                "trader_id": trader_id,
                "item_types": list(item_types),
                "objective_key": objective_key,
            },
        },
        world_turn=world_turn,
    )


def _artifact_sell_price(
    item: dict[str, Any],
    artifact_types: dict[str, dict[str, Any]],
) -> int:
    """Return artifact sell price (60% of value) with balance fallback by type."""
    item_type = str(item.get("type") or "")
    fallback_cfg = artifact_types.get(item_type) or {}
    item_value_raw = item.get("value")
    item_value = int(item_value_raw if item_value_raw is not None else (fallback_cfg.get("value") or 0))
    if item_value <= 0:
        return 0
    return int(item_value * 0.6)


def _any_sellable_item_price(
    item: dict[str, Any],
    item_types_cfg: dict[str, dict[str, Any]],
    artifact_types: dict[str, dict[str, Any]],
) -> int:
    """Return sell price (60%) for sellable inventory items, skipping protected categories."""
    item_type = str(item.get("type") or "")
    base_type = str((item_types_cfg.get(item_type) or {}).get("type") or item_type)
    if base_type in {"medical", "consumable", "ammo", "secret_document"}:
        return 0
    item_value_raw = item.get("value")
    fallback_value = (
        (item_types_cfg.get(item_type) or {}).get("value")
        or (artifact_types.get(item_type) or {}).get("value")
        or 0
    )
    item_value = int(item_value_raw if item_value_raw is not None else fallback_value)
    if item_value <= 0:
        return 0
    return int(item_value * 0.6)


def _exec_trade_sell(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Sell inventory items at a co-located trader.

    Returns a trade_sell_failed event when no sale is possible.
    """
    from app.games.zone_stalkers.rules.tick_rules import (
        _bot_sell_to_trader,
        _bot_sell_items_for_cash,
        _find_trader_at_location,
    )
    from app.games.zone_stalkers.balance.items import ITEM_TYPES as _ITEM_TYPES
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART_TYPES

    _art_set = frozenset(_ART_TYPES.keys())
    objective_key = str(step.payload.get("objective_key") or "SELL_ARTIFACTS")
    loc_id = agent.get("location_id", "")
    inventory_before = list(agent.get("inventory") or [])
    artifact_types_before = sorted({
        str(item.get("type") or "")
        for item in inventory_before
        if str(item.get("type") or "") in _art_set
    })
    sellable_count_before = (
        len(inventory_before)
        if step.payload.get("item_category", "artifact") == "any_sellable"
        else sum(1 for item in inventory_before if str(item.get("type") or "") in _art_set)
    )
    trader = _find_trader_at_location(loc_id, state)
    trader_id = str(trader.get("id") or trader.get("agent_id") or "") if isinstance(trader, dict) else ""

    def _fail(reason: str, *, item_types: list[str] | None = None) -> list[dict[str, Any]]:
        selected_item_types = list(item_types if item_types is not None else artifact_types_before)
        failure_event = _make_trade_sell_failed_event(
            agent_id=agent_id,
            reason=reason,
            location_id=str(loc_id),
            trader_id=trader_id or None,
            item_types=selected_item_types,
            objective_key=objective_key,
        )
        step.payload["_failure_reason"] = reason
        agent["action_used"] = True
        _write_trade_sell_failed_memory(
            agent_id=agent_id,
            agent=agent,
            world_turn=world_turn,
            reason=reason,
            location_id=str(loc_id),
            trader_id=trader_id or None,
            item_types=selected_item_types,
            objective_key=objective_key,
        )
        return [failure_event]

    if trader is None:
        return _fail("no_trader_at_location")

    item_category = step.payload.get("item_category", "artifact")
    if item_category == "artifact" and not artifact_types_before:
        return _fail("no_sellable_items", item_types=[])
    if item_category == "any_sellable" and not inventory_before:
        return _fail("no_sellable_items", item_types=[])

    trader_money = int((trader or {}).get("money") or 0)
    if item_category == "artifact":
        sellable_items_before = [
            item
            for item in inventory_before
            if str(item.get("type") or "") in _art_set
        ]
        sellable_prices_before = [
            _artifact_sell_price(item, _ART_TYPES)
            for item in sellable_items_before
        ]
        sellable_item_types_before = sorted(
            {
                str(item.get("type") or "")
                for item, sell_price in zip(sellable_items_before, sellable_prices_before)
                if sell_price > 0
            }
        )
    else:
        sellable_items_before = list(inventory_before)
        sellable_prices_before = [
            _any_sellable_item_price(item, _ITEM_TYPES, _ART_TYPES)
            for item in sellable_items_before
        ]
        sellable_item_types_before = sorted(
            {
                str(item.get("type") or "")
                for item, sell_price in zip(sellable_items_before, sellable_prices_before)
                if sell_price > 0
            }
        )
    trader_can_afford_any_item = any(
        sell_price > 0 and trader_money >= sell_price
        for sell_price in sellable_prices_before
    )
    if sellable_item_types_before and not trader_can_afford_any_item:
        return _fail(TRADE_FAIL_TRADER_NO_MONEY, item_types=sellable_item_types_before)

    money_before = int(agent.get("money") or 0)
    inventory_before_len = len(inventory_before)

    if item_category == "any_sellable":
        events = _bot_sell_items_for_cash(agent_id, agent, trader, state, world_turn) or []
    else:
        events = _bot_sell_to_trader(agent_id, agent, trader, state, world_turn) or []

    money_after = int(agent.get("money") or 0)
    inventory_after = list(agent.get("inventory") or [])
    inventory_after_len = len(inventory_after)
    sellable_count_after = (
        len(inventory_after)
        if item_category == "any_sellable"
        else sum(1 for item in inventory_after if str(item.get("type") or "") in _art_set)
    )
    sold_by_state = money_after > money_before and sellable_count_after < sellable_count_before
    sold_by_event = _trade_sell_succeeded(events)

    if sold_by_event or sold_by_state:
        # ── Debt repayment on successful sale ─────────────────────────────
        _apply_debt_repayment_after_sell(agent_id, agent, trader, state, world_turn, events)
        return events

    reason = _trade_sell_failure_reason(events)
    if not reason:
        reason = "no_sellable_items" if sellable_count_before <= 0 else "no_items_sold"
    return _fail(reason)


def _exec_consume(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Consume an item from inventory."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_consume
    item_type = step.payload.get("item_type")
    inventory = agent.get("inventory", [])
    item = next((i for i in inventory if i.get("type") == item_type), None)
    if not item:
        return []
    reason_key = step.payload.get("reason", "consume")
    action_kind_map = {
        "emergency_heal": "consume_heal",
        "emergency_food": "consume_food",
        "emergency_drink": "consume_drink",
        "prepare_sleep_food": "consume_food",
        "prepare_sleep_drink": "consume_drink",
        "opportunistic_food": "consume_food",
        "opportunistic_drink": "consume_drink",
        # PR2 explicit reason keys
        "need_food": "consume_food",
        "need_drink": "consume_drink",
        "need_medical": "consume_heal",
    }
    if reason_key in action_kind_map:
        action_kind = action_kind_map[reason_key]
    else:
        # Fallback: derive action_kind from item type category.
        from app.games.zone_stalkers.balance.items import FOOD_ITEM_TYPES, DRINK_ITEM_TYPES
        if item.get("type") in FOOD_ITEM_TYPES:
            action_kind = "consume_food"
        elif item.get("type") in DRINK_ITEM_TYPES:
            action_kind = "consume_drink"
        else:
            action_kind = "consume_heal"
    consume_events = _bot_consume(agent_id, agent, item, world_turn, state, action_kind) or []
    if consume_events:
        episode = agent.get("survival_episode_state")
        if isinstance(episode, dict) and str(episode.get("status") or "") in {"started", "loaned", "bought"}:
            expected_item_type = str(episode.get("expected_item_type") or "")
            if not expected_item_type or expected_item_type == str(item_type or ""):
                episode["consume_turn"] = int(world_turn)
                episode["status"] = "completed"
                episode["failure_reason"] = None
                agent["pending_survival_purchase"] = None
                consume_events.append(_survival_episode_event("survival_purchase_episode_consumed", episode, world_turn))
    return consume_events


def _exec_equip(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Equip an item from inventory."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_equip_from_inventory
    from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES
    slot = step.payload.get("slot", "weapon")
    item_types = WEAPON_ITEM_TYPES if slot == "weapon" else ARMOR_ITEM_TYPES
    return _bot_equip_from_inventory(agent_id, agent, item_types, slot, state, world_turn) or []


def _exec_pickup(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Pick up an item from the ground."""
    from app.games.zone_stalkers.rules.tick_rules import _bot_pickup_item_from_ground
    from app.games.zone_stalkers.balance.items import WEAPON_ITEM_TYPES
    item_types = step.payload.get("item_types", WEAPON_ITEM_TYPES)
    return _bot_pickup_item_from_ground(agent_id, agent, item_types, state, world_turn) or []


def _exec_loot_corpse(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    from app.games.zone_stalkers.balance.items import (
        AMMO_ITEM_TYPES,
        FOOD_ITEM_TYPES,
        DRINK_ITEM_TYPES,
        HEAL_ITEM_TYPES,
        WEAPON_ITEM_TYPES,
        ARMOR_ITEM_TYPES,
        AMMO_FOR_WEAPON,
    )
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    _ = ctx
    location_id = str(agent.get("location_id") or "")
    corpse_id = str(step.payload.get("corpse_id") or "")
    take_money = bool(step.payload.get("take_money", True))
    take_all = bool(step.payload.get("take_all", False))
    max_items = int(step.payload.get("max_items") or 0)
    def _record_loot_failure(reason: str) -> list[dict[str, Any]]:
        _add_memory(
            agent,
            world_turn,
            state,
            "action",
            "❌ Не удалось обыскать труп",
            {
                "action_kind": "loot_corpse_failed",
                "reason": reason,
                "corpse_id": corpse_id or None,
                "location_id": location_id or None,
            },
            summary=f"Обыск трупа сорвался: {reason}.",
        )
        step.payload["_loot_step_outcome"] = "failed"
        step.payload["_loot_failed_reason"] = reason
        agent["action_used"] = True
        return []

    if not location_id:
        return _record_loot_failure("no_valid_corpse")

    loc = state.get("locations", {}).get(location_id, {})
    corpses = loc.get("corpses") if isinstance(loc, dict) else None
    if not isinstance(corpses, list):
        return _record_loot_failure("no_valid_corpse")

    def _is_important(item: dict[str, Any]) -> bool:
        item_type = str(item.get("type") or "")
        if item_type in ARTIFACT_TYPES:
            return True
        if item_type in FOOD_ITEM_TYPES or item_type in DRINK_ITEM_TYPES or item_type in HEAL_ITEM_TYPES:
            return True
        if item_type in WEAPON_ITEM_TYPES and not isinstance((agent.get("equipment") or {}).get("weapon"), dict):
            return True
        if item_type in ARMOR_ITEM_TYPES and not isinstance((agent.get("equipment") or {}).get("armor"), dict):
            return True
        if item_type in AMMO_ITEM_TYPES:
            weapon = (agent.get("equipment") or {}).get("weapon") or {}
            weapon_type = str(weapon.get("type") or "")
            compatible = AMMO_FOR_WEAPON.get(weapon_type)
            return bool(compatible and compatible == item_type)
        return False

    target_corpse: dict[str, Any] | None = None
    for corpse in corpses:
        if not isinstance(corpse, dict):
            continue
        if not bool(corpse.get("visible", True)):
            continue
        if not bool(corpse.get("lootable", True)):
            continue
        if corpse_id and str(corpse.get("corpse_id") or "") != corpse_id:
            continue
        target_corpse = corpse
        break
    if target_corpse is None:
        return _record_loot_failure("no_valid_corpse")

    corpse_inventory = target_corpse.get("inventory")
    if not isinstance(corpse_inventory, list):
        corpse_inventory = []
        target_corpse["inventory"] = corpse_inventory
    selected_pairs = [
        (idx, item)
        for idx, item in enumerate(corpse_inventory)
        if isinstance(item, dict) and (take_all or _is_important(item))
    ]
    if max_items > 0:
        selected_pairs = selected_pairs[:max_items]

    selected_indices = {idx for idx, _ in selected_pairs}
    selected_items = [item for _, item in selected_pairs]
    items_taken = [dict(item) for item in selected_items]
    if items_taken:
        agent_inventory = agent.get("inventory")
        if not isinstance(agent_inventory, list):
            agent_inventory = []
            agent["inventory"] = agent_inventory
        agent_inventory.extend(items_taken)
    target_corpse["inventory"] = [
        item for idx, item in enumerate(corpse_inventory)
        if idx not in selected_indices
    ]

    money_taken = 0
    corpse_money = int(target_corpse.get("money") or 0)
    if take_money and corpse_money > 0:
        money_taken = corpse_money
        target_corpse["money"] = 0
        agent["money"] = int(agent.get("money") or 0) + money_taken

    looted_by = target_corpse.get("looted_by")
    if not isinstance(looted_by, list):
        looted_by = []
        target_corpse["looted_by"] = looted_by
    if agent_id not in looted_by and (items_taken or money_taken > 0):
        looted_by.append(agent_id)

    target_corpse["fully_looted"] = (
        len(target_corpse.get("inventory") or []) == 0 and int(target_corpse.get("money") or 0) <= 0
    )
    if target_corpse["fully_looted"]:
        target_corpse["lootable"] = False

    important_items_taken = [str(item.get("type") or "") for item in items_taken]
    if items_taken or money_taken > 0:
        _add_memory(
            agent,
            world_turn,
            state,
            "action",
            "💼 Обыскал труп",
            {
                "action_kind": "corpse_looted",
                "corpse_id": str(target_corpse.get("corpse_id") or ""),
                "dead_agent_id": str(target_corpse.get("agent_id") or ""),
                "location_id": location_id,
                "items_taken_count": len(items_taken),
                "money_taken": money_taken,
                "important_items_taken": important_items_taken,
            },
            summary=f"Обыскал труп и забрал {len(items_taken)} предмет(ов) и {money_taken} денег.",
        )

    agent["action_used"] = True
    return [
        {
            "event_type": "corpse_looted",
            "payload": {
                "agent_id": agent_id,
                "corpse_id": str(target_corpse.get("corpse_id") or ""),
                "location_id": location_id,
                "items_taken_count": len(items_taken),
                "money_taken": money_taken,
            },
        }
    ]


def _exec_ask_for_intel(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Ask co-located stalkers for intelligence."""
    from app.games.zone_stalkers.rules.tick_rules import (
        _bot_buy_hunt_intel_from_trader,
        _find_hunt_intel_location,
    )

    target_id = step.payload.get("target_id")
    if target_id:
        from app.games.zone_stalkers.rules.tick_rules import _bot_ask_colocated_stalkers_about_agent
        # _bot_ask_colocated_stalkers_about_agent(agent_id, agent, target_agent_id,
        #   target_agent_name, state, world_turn)
        target = state.get("agents", {}).get(target_id, {})
        target_name = target.get("name", target_id) if target else target_id
        _bot_ask_colocated_stalkers_about_agent(
            agent_id, agent, target_id, target_name, state, world_turn
        )
        # If stalkers had no intel, try trader intel at current location.
        intel_loc = _find_hunt_intel_location(agent, str(target_id), state)
        if not intel_loc:
            _bot_buy_hunt_intel_from_trader(
                agent_id,
                agent,
                str(target_id),
                target_name,
                state,
                world_turn,
            )
        agent["action_used"] = True
    return []


def _exec_question_witnesses(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import (
        _add_memory,
        _bot_ask_colocated_stalkers_about_agent,
        _bot_buy_hunt_intel_from_trader,
    )

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id, {})
    target_name = target.get("name", target_id) if isinstance(target, dict) else target_id
    intel_loc = _bot_ask_colocated_stalkers_about_agent(
        agent_id, agent, target_id, target_name, state, world_turn
    )
    if not intel_loc:
        _current_loc_id = str(agent.get("location_id") or "")
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🕵️ Свидетелей не найдено",
            {
                "action_kind": "no_witnesses",
                "target_id": target_id,
                "location_id": _current_loc_id,
            },
            summary="В этой локации нет свидетелей, которые могут указать след цели.",
        )
        # Fix 6: Mark witness source as exhausted with cooldown
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🚫 Источник свидетелей исчерпан",
            {
                "action_kind": "witness_source_exhausted",
                "target_id": target_id,
                "location_id": _current_loc_id,
                "source_kind": "location_witnesses",
                "cooldown_until_turn": world_turn + WITNESS_SOURCE_COOLDOWN_TURNS,
            },
            summary=f"Свидетели в локации {_current_loc_id} уже расспрошены, охлаждение до хода {world_turn + WITNESS_SOURCE_COOLDOWN_TURNS}.",
        )
        _bot_buy_hunt_intel_from_trader(
            agent_id,
            agent,
            target_id,
            target_name,
            state,
            world_turn,
        )
    agent["action_used"] = True
    return []


def _is_hunt_hub_location(state: dict[str, Any], location_id: str | None) -> bool:
    if not location_id:
        return False
    traders = state.get("traders")
    if not isinstance(traders, dict):
        return False
    return any(
        isinstance(raw_trader, dict)
        and raw_trader.get("is_alive", True)
        and str(raw_trader.get("location_id") or "") == str(location_id)
        for raw_trader in traders.values()
    )


def _search_location_cooldown_turns(state: dict[str, Any], location_id: str | None) -> int:
    return _SEARCH_LOCATION_HUB_COOLDOWN_TURNS if _is_hunt_hub_location(state, location_id) else _SEARCH_LOCATION_COOLDOWN_TURNS


def _count_target_not_found_failures(
    agent: dict[str, Any],
    *,
    target_id: str,
    location_id: str,
) -> int:
    count = 0
    for rec in _v3r_ex(agent):
        if _v3ak_ex(rec) != "target_not_found":
            continue
        fx = _v3fx_ex(rec)
        if str(fx.get("target_id") or "") != target_id:
            continue
        if str(fx.get("location_id") or rec.get("location_id") or "") != location_id:
            continue
        count += 1
    return count


def _resolve_track_destination_from_known_leads(
    *,
    agent: dict[str, Any],
    state: dict[str, Any],
    target_id: str,
    current_loc: str,
) -> str | None:
    target = state.get("agents", {}).get(target_id)
    if (
        bool(state.get("debug_omniscient_targets"))
        and isinstance(target, dict)
        and target.get("is_alive", True)
    ):
        target_loc = str(target.get("location_id") or "")
        if target_loc and target_loc != current_loc:
            return target_loc

    memory_v3 = agent.get("memory_v3")
    records = memory_v3.get("records", {}) if isinstance(memory_v3, dict) else {}
    rec_items = [rec for rec in records.values() if isinstance(rec, dict)]
    rec_items.sort(key=lambda rec: int(rec.get("created_turn") or 0), reverse=True)
    for rec in rec_items:
        details = rec.get("details")
        if not isinstance(details, dict):
            details = {}
        rec_target_id = str(details.get("target_id") or details.get("target_agent_id") or "")
        if rec_target_id != target_id and target_id not in {str(v) for v in rec.get("entity_ids", [])}:
            continue
        kind = str(rec.get("kind") or "")
        loc: str | None = None
        if kind in {"target_moved", "target_route_observed"}:
            loc = str(details.get("to_location_id") or rec.get("location_id") or "")
        elif kind in {"target_intel", "target_last_known_location"}:
            loc = str(rec.get("location_id") or details.get("location_id") or "")
        if loc and loc != current_loc:
            return loc
    return None


def _exec_look_for_tracks(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []

    current_loc = str(agent.get("location_id") or "")
    target_loc = _resolve_track_destination_from_known_leads(
        agent=agent,
        state=state,
        target_id=target_id,
        current_loc=current_loc,
    )

    if target_loc:
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "👣 Обнаружены следы цели",
            {
                "action_kind": "target_route_observed",
                "target_id": target_id,
                "location_id": target_loc,
                "from_location_id": current_loc,
                "to_location_id": target_loc,
            },
            summary="Нашёл свежие следы и предполагаемый маршрут цели.",
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🧭 Цель сместилась по следам",
            {
                "action_kind": "target_moved",
                "target_id": target_id,
                "location_id": target_loc,
                "from_location_id": current_loc,
                "to_location_id": target_loc,
            },
            summary="Следы указывают, куда ушла цель.",
        )
    else:
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "👣 Следы цели не найдены",
            {
                "action_kind": "no_tracks_found",
                "target_id": target_id,
                "location_id": current_loc,
            },
            summary="Следов цели в текущей локации не найдено.",
        )
    agent["action_used"] = True
    return []


def _exec_search_target(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id)
    target_loc = target.get("location_id") if isinstance(target, dict) else None
    current_loc = agent.get("location_id")
    if target and target.get("is_alive", True) and target_loc == current_loc:
        target_name = target.get("name", target_id)
        # Fix 1: Signal hunt outcome to active plan runtime
        step.payload["_target_found"] = True
        step.payload["_target_id"] = target_id
        step.payload["_target_location_id"] = current_loc
        step.payload["_hunt_step_outcome"] = "target_found"
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            f"🎯 Цель обнаружена: «{target_name}»",
            {
                "action_kind": "target_seen",
                "target_id": target_id,
                "target_name": target_name,
                "location_id": current_loc,
                "hp": target.get("hp"),
            },
            summary=f"Вижу цель «{target_name}» в текущей локации.",
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "📍 Последнее известное местоположение цели обновлено",
            {
                "action_kind": "target_last_known_location",
                "target_id": target_id,
                "location_id": current_loc,
            },
            summary="Обновил последнее известное местоположение цели.",
        )
        weapon_type = (target.get("equipment", {}) or {}).get("weapon", {}).get("type") if isinstance(target, dict) else None
        armor_type = (target.get("equipment", {}) or {}).get("armor", {}).get("type") if isinstance(target, dict) else None
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🧪 Оценка боевой силы цели",
            {
                "action_kind": "target_combat_strength_observed",
                "target_id": target_id,
                "location_id": current_loc,
                "combat_strength": max(0.1, min(1.0, float(target.get("hp", 100)) / 100.0)),
                "weapon_type": weapon_type,
                "armor_type": armor_type,
            },
            summary="Оценил боевую силу цели по текущему состоянию.",
        )
        if weapon_type or armor_type:
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                "🔎 Снаряжение цели замечено",
                {
                    "action_kind": "target_equipment_seen",
                    "target_id": target_id,
                    "location_id": current_loc,
                    "weapon_type": weapon_type,
                    "armor_type": armor_type,
                },
                summary="Зафиксировал видимое снаряжение цели.",
            )
    elif target and not target.get("is_alive", True) and target_loc == current_loc:
        corpse = next(
            (
                raw_corpse
                for raw_corpse in (state.get("locations", {}).get(current_loc, {}).get("corpses") or [])
                if isinstance(raw_corpse, dict)
                and bool(raw_corpse.get("visible", True))
                and str(raw_corpse.get("agent_id") or "") == target_id
            ),
            None,
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            f"☠️ Обнаружено тело цели: «{target.get('name', target_id)}»",
            {
                "action_kind": "target_corpse_seen",
                "target_id": target_id,
                "target_name": target.get("name", target_id),
                "corpse_id": (corpse or {}).get("corpse_id"),
                "corpse_location_id": current_loc,
                "location_id": current_loc,
                "death_cause": (corpse or {}).get("death_cause") or target.get("death_cause"),
                "killer_id": (corpse or {}).get("killer_id"),
                "directly_observed": True,
                "confidence": 0.95,
            },
            summary="Нашёл тело цели в текущей локации.",
        )
    else:
        expected_loc = step.payload.get("target_location_id") or current_loc
        failed_search_count = _count_target_not_found_failures(
            agent,
            target_id=target_id,
            location_id=str(expected_loc),
        ) + 1
        cooldown_turns = _search_location_cooldown_turns(state, str(expected_loc))
        cooldown_until_turn = (
            world_turn + cooldown_turns
            if failed_search_count >= _SEARCH_EXHAUSTION_THRESHOLD
            else None
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "❓ Цель не найдена в ожидаемой локации",
            {
                "action_kind": "target_not_found",
                "target_id": target_id,
                "location_id": expected_loc,
                "confirmed_empty": True,
                "failed_search_count": failed_search_count,
                "cooldown_until_turn": cooldown_until_turn,
                "cooldown_turns": cooldown_turns,
                "location_kind": "trader_hub" if _is_hunt_hub_location(state, str(expected_loc)) else "search_location",
                "is_hub_location": _is_hunt_hub_location(state, str(expected_loc)),
            },
            summary="Цель отсутствует в проверенной локации.",
        )
    agent["action_used"] = True
    return []


def _exec_start_combat(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _compat_initiate_combat, _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []
    target = state.get("agents", {}).get(target_id)
    if not isinstance(target, dict) or not target.get("is_alive", True):
        agent["action_used"] = True
        return []

    current_loc = agent.get("location_id")
    if target.get("location_id") != current_loc:
        target_loc = target.get("location_id")
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "🎯 Цель сместилась перед боем",
            {
                "action_kind": "target_moved",
                "target_id": target_id,
                "location_id": current_loc,
                "from_location_id": current_loc,
                "to_location_id": target_loc,
                "confirmed_empty": True,
            },
            summary="Не удалось начать бой: цель сместилась в другую локацию.",
        )
        agent["action_used"] = True
        return []

    target_name = target.get("name", target_id)
    _add_memory(
        agent,
        world_turn,
        state,
        "observation",
        f"🎯 Цель обнаружена перед боем: «{target_name}»",
        {
            "action_kind": "target_seen",
            "target_id": target_id,
            "target_name": target_name,
            "location_id": current_loc,
            "hp": target.get("hp"),
        },
        summary=f"Подтвердил визуальный контакт с целью «{target_name}».",
    )
    _add_memory(
        agent,
        world_turn,
        state,
        "observation",
        "📍 Последнее известное местоположение цели обновлено",
        {
            "action_kind": "target_last_known_location",
            "target_id": target_id,
            "location_id": current_loc,
        },
        summary="Перед боем обновил последнее известное местоположение цели.",
    )

    weapon = (agent.get("equipment", {}) or {}).get("weapon")
    if not isinstance(weapon, dict):
        agent["action_used"] = True
        return []

    events = _compat_initiate_combat(
        agent_id,
        agent,
        target_id,
        target,
        current_loc,
        state,
        world_turn,
    )
    return events or []


def _exec_confirm_kill(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import (
        _add_memory,
        _mark_kill_stalker_goal_achieved,
        _recent_combat_with_target,
    )

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id)
    target_name = target.get("name", target_id) if isinstance(target, dict) else target_id

    def _has_personal_kill_evidence() -> bool:
        for rec in _v3r_ex(agent):
            fx = _v3fx_ex(rec)
            if str(fx.get("target_id") or fx.get("target") or "") != target_id:
                continue
            if _v3ak_ex(rec) in {"target_death_confirmed", "hunt_target_killed"}:
                return True
        return False

    if isinstance(target, dict) and not target.get("is_alive", True):
        current_loc = str(agent.get("location_id") or "")

        corpse: dict[str, Any] | None = None
        corpse_loc_id: str | None = None
        for raw_corpse in (state.get("locations", {}).get(current_loc, {}).get("corpses") or []):
            if (
                isinstance(raw_corpse, dict)
                and bool(raw_corpse.get("visible", True))
                and str(raw_corpse.get("agent_id") or "") == target_id
            ):
                corpse = raw_corpse
                corpse_loc_id = current_loc
                break

        if corpse is None:
            for loc_id, loc in (state.get("locations", {}) or {}).items():
                if not isinstance(loc, dict):
                    continue
                for raw_corpse in (loc.get("corpses") or []):
                    if (
                        isinstance(raw_corpse, dict)
                        and bool(raw_corpse.get("visible", True))
                        and str(raw_corpse.get("agent_id") or "") == target_id
                    ):
                        corpse = raw_corpse
                        corpse_loc_id = str(loc_id)
                        break
                if corpse is not None:
                    break

        direct_confirmation = bool(corpse) and str(corpse_loc_id or "") == current_loc
        has_personal_evidence = _has_personal_kill_evidence()
        recently_engaged = _recent_combat_with_target(
            agent_id,
            agent,
            target_id,
            state,
            world_turn,
        )

        if direct_confirmation:
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                f"✅ Подтверждена ликвидация цели «{target_name}»",
                {
                    "action_kind": "target_death_confirmed",
                    "target_id": target_id,
                    "target_name": target_name,
                    "confirmation_source": "self_observed_body",
                    "directly_observed": True,
                    "corpse_id": (corpse or {}).get("corpse_id"),
                    "corpse_location_id": current_loc,
                    "target_death_cause": (corpse or {}).get("death_cause") or target.get("death_cause"),
                    "killer_id": (corpse or {}).get("killer_id"),
                    "location_id": current_loc,
                },
                summary=f"Лично подтвердил смерть цели «{target_name}» по телу.",
            )
            _mark_kill_stalker_goal_achieved(
                agent_id,
                agent,
                state,
                world_turn,
                target_id,
                confirmation_source="self_observed_body",
            )
        elif corpse is not None and (has_personal_evidence or recently_engaged):
            observed_here = str(corpse_loc_id or "") == current_loc
            confirmation_source = "combat_result_state" if observed_here else "state_confirmed_after_personal_combat"
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                f"✅ Подтверждена ликвидация цели «{target_name}»",
                {
                    "action_kind": "target_death_confirmed",
                    "target_id": target_id,
                    "target_name": target_name,
                    "confirmation_source": confirmation_source,
                    "directly_observed": observed_here,
                    "corpse_id": (corpse or {}).get("corpse_id"),
                    "corpse_location_id": str(corpse_loc_id or ""),
                    "target_death_cause": (corpse or {}).get("death_cause") or target.get("death_cause"),
                    "killer_id": (corpse or {}).get("killer_id"),
                    "location_id": current_loc,
                },
                summary=(
                    f"Подтвердил смерть цели «{target_name}» по результатам боя."
                    if observed_here
                    else f"Подтвердил смерть цели «{target_name}» по итогам боя и обнаружению тела в другой локации."
                ),
            )
            _mark_kill_stalker_goal_achieved(
                agent_id,
                agent,
                state,
                world_turn,
                target_id,
                confirmation_source=confirmation_source,
            )
        else:
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                "❌ Ликвидация цели не подтверждена",
                {
                    "action_kind": "hunt_failed",
                    "target_id": target_id,
                    "reason": "no_direct_confirmation",
                },
                summary="Цель мертва, но без подтверждения личным боевым контекстом.",
            )
    else:
        _add_memory(
            agent,
            world_turn,
            state,
            "observation",
            "❌ Ликвидация цели не подтверждена",
            {
                "action_kind": "hunt_failed",
                "target_id": target_id,
                "reason": "target_still_alive",
            },
            summary="Подтверждение не удалось: цель всё ещё жива.",
        )
    agent["action_used"] = True
    return []


def _exec_monitor_combat(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _add_memory

    target_id = str(step.payload.get("target_id") or agent.get("kill_target_id") or "")
    if not target_id:
        step.payload["_monitor_complete"] = True
        agent["action_used"] = True
        return []

    target = state.get("agents", {}).get(target_id)
    target_alive = bool(target.get("is_alive", True)) if isinstance(target, dict) else False

    combat_active = False
    for combat in (state.get("combat_interactions", {}) or {}).values():
        if not isinstance(combat, dict):
            continue
        if combat.get("ended") or combat.get("ended_turn") is not None:
            continue
        participants = combat.get("participants", {})
        if not isinstance(participants, dict):
            continue
        if agent_id in participants and target_id in participants:
            combat_active = True
            break

    monitor_complete = (not combat_active) or (not target_alive)
    step.payload["_monitor_complete"] = monitor_complete

    if monitor_complete and target_alive and not combat_active:
        recent_same = any(
            _v3ak_ex(rec) == "combat_ended_without_kill"
            and str(_v3fx_ex(rec).get("target_id") or "") == target_id
            and int(rec.get("created_turn") or 0) >= world_turn - 1
            for rec in _v3r_ex(agent)
        )
        if not recent_same:
            _add_memory(
                agent,
                world_turn,
                state,
                "observation",
                "⚠️ Бой завершён без ликвидации цели",
                {
                    "action_kind": "combat_ended_without_kill",
                    "target_id": target_id,
                },
                summary="Бой завершился, но цель осталась жива или ушла — требуется перепланирование.",
            )

    agent["action_used"] = True
    return []


def _exec_wait(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Mark action_used without doing anything."""
    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    agent["action_used"] = True
    reason = step.payload.get("reason", "")
    if reason == "wait_in_shelter":
        # Merge-in-place: if we already have a wait_in_shelter decision entry,
        # just update its world_turn instead of adding a new entry every tick.
        # This prevents the shelter/v2_decision pair from writing duplicate
        # entries on every emission tick.
        _has_shelter_entry = any(
            _v3ak_ex(r) == "wait_in_shelter"
            for r in _v3r_ex(agent)
        )
        if not _has_shelter_entry:
            _add_memory(agent, world_turn, state, "decision",
                "🏠 Укрываюсь от выброса",
                {"action_kind": "wait_in_shelter"},
                summary="Нахожусь в укрытии — жду окончания выброса")
    elif reason == "trapped_on_dangerous_terrain":
        _write_once_decision(agent, world_turn, state,
            "⚠️ Нет выхода: застрял на опасной местности",
            {"action_kind": "trapped_on_dangerous_terrain"},
            "Все соседние локации тоже опасны — укрыться негде",
            check_all=True)
    return []


def _exec_legacy_passthrough(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Pass through to legacy tick_rules logic (no-op at plan level)."""
    # The actual decision is handled by the legacy cascade in tick_rules.
    # This step simply signals "let the old code decide" — no side effects here.
    return []


def _exec_unknown(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    """Fallback executor for unrecognised step kinds."""
    agent["action_used"] = True
    return []


def _exec_leave_zone(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    from app.games.zone_stalkers.rules.tick_rules import _execute_leave_zone, _bot_route_to_exit

    loc_id = agent.get("location_id", "")
    loc = (state.get("locations", {}) or {}).get(loc_id, {})
    if not bool(loc.get("exit_zone")):
        return _bot_route_to_exit(agent_id, agent, state, world_turn)
    return _execute_leave_zone(agent_id, agent, state, world_turn)


# ── Memory-write helpers used by executors ────────────────────────────────────

def _write_once_decision(
    agent: dict[str, Any],
    world_turn: int,
    state: dict[str, Any],
    title: str,
    effects: dict[str, Any],
    summary: str,
    check_all: bool = False,
) -> None:
    """Write a decision memory entry but only if one with the same action_kind is not
    already present (anti-spam).

    When *check_all* is True every decision entry is scanned, not just the most recent one.
    Use check_all=True for decisions that should never repeat (e.g. trapped_on_dangerous_terrain).
    """
    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    action_kind = effects.get("action_kind")
    for rec in _v3r_ex(agent):
        if _v3mt_ex(rec) == "decision":
            if _v3ak_ex(rec) == action_kind:
                return  # already written this kind
            if not check_all:
                break  # last decision is different — safe to write
    _add_memory(agent, world_turn, state, "decision", title, effects, summary=summary)


def _write_once_by_dest(
    agent: dict[str, Any],
    world_turn: int,
    state: dict[str, Any],
    action_kind: str,
    item_category: str,
    destination: str,
    emergency: bool = False,
) -> None:
    """Write a seek_item decision memory (anti-spam by destination + category)."""
    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    for rec in _v3r_ex(agent):
        if _v3mt_ex(rec) != "decision":
            continue
        fx = _v3fx_ex(rec)
        if (fx.get("action_kind") == action_kind
                and fx.get("destination") == destination
                and fx.get("item_category") == item_category):
            return  # already recorded
    effects: dict[str, Any] = {
        "action_kind": action_kind,
        "item_category": item_category,
        "destination": destination,
    }
    if emergency:
        effects["emergency"] = True
    _add_memory(agent, world_turn, state, "decision",
                f"🔍 Ищу {item_category} в {destination}",
                effects,
                summary=f"Отправляюсь искать предметы категории {item_category}")


# ── Public sellable-inventory API (Part 1a) ────────────────────────────────────

def get_sellable_inventory_items(
    agent: "dict[str, Any]",
    *,
    item_category: str = "artifact",
) -> "list[dict[str, Any]]":
    """Return inventory items that are sellable under *item_category*.

    Parameters
    ----------
    agent:
        Agent dict (reads ``agent["inventory"]``).
    item_category:
        ``"artifact"`` — only artifact-type items with a positive sell price.
        ``"any_sellable"`` — any item with a positive sell price (excludes
        medical, consumable, ammo, secret_document categories).

    Returns
    -------
    list[dict]
        Items matching the sell criterion.  Empty when nothing is sellable.
    """
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES as _ART  # noqa: PLC0415
    from app.games.zone_stalkers.balance.items import ITEM_TYPES as _IT  # noqa: PLC0415

    _art_cfg = dict(_ART)
    _it_cfg = dict(_IT)
    inventory = agent.get("inventory") or []

    if item_category == "artifact":
        return [
            item for item in inventory
            if str(item.get("type") or "") in frozenset(_art_cfg.keys())
            and _artifact_sell_price(item, _art_cfg) > 0
        ]
    if item_category == "any_sellable":
        return [
            item for item in inventory
            if _any_sellable_item_price(item, _it_cfg, _art_cfg) > 0
        ]
    return []


def has_sellable_inventory(
    agent: "dict[str, Any]",
    *,
    item_category: str = "artifact",
) -> bool:
    """Return True when the agent has at least one sellable item of *item_category*."""
    return bool(get_sellable_inventory_items(agent, item_category=item_category))


def _apply_debt_repayment_after_sell(
    agent_id: str,
    agent: "dict[str, Any]",
    trader: "dict[str, Any]",
    state: "dict[str, Any]",
    world_turn: int,
    events: "list[dict[str, Any]]",
) -> None:
    """Apply debt auto-repayment to the trader after a successful sale."""
    trader_id = _resolve_trader_id(state, trader)
    if not trader_id:
        return
    repayment_events = repay_debts_to_creditor_if_useful(
        state=state,
        debtor=agent,
        creditor=trader,
        world_turn=world_turn,
        critical_needs=False,
    )
    if not repayment_events:
        return
    events.extend(repayment_events)
    if trader_id:
        trader["id"] = trader_id
    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    for event in repayment_events:
        payload = event.get("payload") if isinstance(event, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        event_type = str((event or {}).get("event_type") or "")
        if event_type not in {"debt_payment", "debt_repaid"}:
            continue
        title = "Погашение долга" if event_type == "debt_payment" else "Долг полностью погашен"
        summary = (
            f"Погасил долг: {payload.get('amount', 0)}"
            if event_type == "debt_payment"
            else f"Полностью закрыл долг {payload.get('account_id', '')}"
        )
        _add_memory(
            agent,
            world_turn,
            state,
            "action",
            title,
            {"action_kind": event_type, **payload},
            summary=summary,
            agent_id=agent_id,
        )


def _exec_repay_debt(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    del ctx
    creditor_id = str(step.payload.get("creditor_id") or "")
    creditor_type = str(step.payload.get("creditor_type") or "trader")
    account_id = str(step.payload.get("account_id") or "")
    if not creditor_id or not account_id:
        step.payload["_failure_reason"] = "missing_creditor_or_account"
        return []

    creditors = state.get("traders") if creditor_type == "trader" else state.get("agents")
    creditor = (creditors or {}).get(creditor_id) if isinstance(creditors, dict) else None
    if not isinstance(creditor, dict):
        step.payload["_failure_reason"] = "creditor_not_found"
        return []

    if str(agent.get("location_id") or "") != str(creditor.get("location_id") or ""):
        step.payload["_failure_reason"] = "creditor_not_colocated"
        return []

    # Use runtime chooser so partial repayment can adapt to current money/needs.
    account = None
    ledger = state.get("debt_ledger") if isinstance(state.get("debt_ledger"), dict) else {}
    accounts = ledger.get("accounts") if isinstance(ledger, dict) else {}
    if isinstance(accounts, dict):
        account = accounts.get(account_id)
    if not isinstance(account, dict):
        step.payload["_failure_reason"] = "account_not_found"
        return []

    amount = choose_debt_repayment_amount(
        debtor=agent,
        account=account,
        world_turn=world_turn,
        critical_needs=False,
    )
    if amount <= 0:
        step.payload["_failure_reason"] = "no_safe_repayment_amount"
        return []

    result = repay_debt_account(
        state=state,
        debtor=agent,
        creditor=creditor,
        account_id=account_id,
        amount=amount,
        world_turn=world_turn,
    )
    paid = int(result.get("paid") or 0)
    if paid <= 0:
        step.payload["_failure_reason"] = str(result.get("status") or "repayment_failed")
        return []

    events: list[dict[str, Any]] = [{
        "event_type": "debt_payment",
        "payload": {
            "account_id": account_id,
            "debtor_id": agent_id,
            "creditor_id": creditor_id,
            "amount": paid,
            "remaining_total": int(result.get("remaining_total") or 0),
        },
    }]
    if bool(result.get("fully_repaid")):
        events.append({
            "event_type": "debt_repaid",
            "payload": {
                "account_id": account_id,
                "debtor_id": agent_id,
                "creditor_id": creditor_id,
                "total_repaid": int((account or {}).get("repaid_total") or 0),
            },
        })
    return events


def _resolve_trader_id(state: dict[str, Any], trader: dict[str, Any]) -> str | None:
    trader_id = str(trader.get("id") or "")
    if trader_id:
        return trader_id
    for key, value in (state.get("traders") or {}).items():
        if value is trader:
            trader["id"] = str(key)
            return str(key)
    return None


def _exec_request_loan(
    agent_id: str,
    agent: dict[str, Any],
    step: PlanStep,
    ctx: AgentContext,
    state: dict[str, Any],
    world_turn: int,
) -> list[dict[str, Any]]:
    del ctx
    creditor_id = str(step.payload.get("creditor_id") or "")
    creditor_type = str(step.payload.get("creditor_type") or "trader")
    creditors = state.get("traders") if creditor_type == "trader" else state.get("agents")
    creditor = (creditors or {}).get(creditor_id) if isinstance(creditors, dict) else None
    if not isinstance(creditor, dict):
        step.payload["_failure_reason"] = "creditor_not_found"
        return []
    if creditor_type == "trader":
        creditor["id"] = creditor_id

    item_category = str(step.payload.get("item_category") or "")
    required_price = int(step.payload.get("required_price") or 0)
    current_money = int(agent.get("money") or 0)
    principal_needed = max(0, required_price - current_money)
    amount = int(step.payload.get("amount") or 0)
    step.payload["principal_needed"] = principal_needed

    expected_item_type = str(
        step.payload.get("survival_credit_quote_item_type")
        or step.payload.get("expected_item_type")
        or ""
    )
    episode_events: list[dict[str, Any]] = []
    episode_state: dict[str, Any] | None = None
    if item_category in {"drink", "food", "medical"}:
        episode_state, can_start_episode = _begin_survival_episode(
            agent_id=agent_id,
            agent=agent,
            category=item_category,
            expected_item_type=expected_item_type,
            required_price=required_price,
            world_turn=world_turn,
        )
        if not can_start_episode:
            step.payload["_failure_reason"] = "survival_episode_in_progress"
            failed = _mark_survival_episode_failed(
                agent=agent,
                reason="reloan_blocked_same_category_window",
                world_turn=world_turn,
            )
            if failed is not None:
                episode_events.append(_survival_episode_event("survival_purchase_episode_failed", failed, world_turn))
            return episode_events
        step.payload["survival_episode_id"] = str(episode_state.get("last_episode_id") or "")
        step.payload["survival_episode_category"] = item_category
        episode_events.append(_survival_episode_event("survival_purchase_episode_started", episode_state, world_turn))

    ok, reason = can_request_survival_credit(
        state=state,
        debtor=agent,
        creditor=creditor,
        creditor_type=creditor_type,
        item_category=item_category,
        required_price=required_price,
        world_turn=world_turn,
    )
    if not ok:
        step.payload["_failure_reason"] = reason
        failed = _mark_survival_episode_failed(agent=agent, reason=reason, world_turn=world_turn)
        if failed is not None:
            episode_events.append(_survival_episode_event("survival_purchase_episode_failed", failed, world_turn))
        return episode_events

    if amount < principal_needed:
        amount = principal_needed
        step.payload["amount_corrected_to_required_price"] = True
    amount = max(0, min(amount, principal_needed))
    step.payload["amount"] = amount
    if amount <= 0:
        step.payload["_failure_reason"] = "amount_not_needed"
        failed = _mark_survival_episode_failed(agent=agent, reason="amount_not_needed", world_turn=world_turn)
        if failed is not None:
            episode_events.append(_survival_episode_event("survival_purchase_episode_failed", failed, world_turn))
        return episode_events

    money_before = current_money
    account = advance_survival_credit(
        state=state,
        debtor_id=agent_id,
        creditor_id=creditor_id,
        creditor_type=creditor_type,
        amount=amount,
        purpose=str(step.payload.get("purpose") or "survival_credit"),
        location_id=str(agent.get("location_id") or ""),
        world_turn=world_turn,
    )

    agent["money"] = money_before + amount
    money_after = int(agent.get("money") or 0)
    if creditor_type == "trader":
        creditor["accounts_receivable"] = int(creditor.get("accounts_receivable") or 0) + amount

    purpose = str(step.payload.get("purpose") or "survival_credit")
    if not expected_item_type and item_category in {"food", "drink", "medical"} and required_price > 0:
        from .survival_credit import quote_survival_purchase

        recovered_quote = quote_survival_purchase(agent=agent, category=item_category)
        if recovered_quote is not None and int(recovered_quote.required_price) == required_price:
            expected_item_type = str(recovered_quote.item_type)
            step.payload["expected_item_type"] = expected_item_type
            step.payload["survival_credit_quote_item_type"] = expected_item_type
            step.payload["expected_item_type_recovered_from_quote"] = True

    if isinstance(episode_state, dict):
        episode_state["expected_item_type"] = expected_item_type
        episode_state["required_price"] = required_price
        episode_state["loan_turn"] = int(world_turn)
        episode_state["status"] = "loaned"
        episode_state["failure_reason"] = None
        agent["pending_survival_purchase"] = {
            "survival_episode_id": str(episode_state.get("last_episode_id") or ""),
            "category": item_category,
            "expected_item_type": expected_item_type,
            "required_price": int(required_price),
            "loan_turn": int(world_turn),
            "expires_turn": int(world_turn) + _SURVIVAL_EPISODE_WINDOW_TURNS,
        }
        episode_events.append(_survival_episode_event("survival_purchase_episode_loaned", episode_state, world_turn))

    event_payload = {
        "account_id": account["id"],
        "debtor_id": agent_id,
        "creditor_id": creditor_id,
        "creditor_type": creditor_type,
        "principal": amount,
        "amount": amount,
        "purpose": purpose,
        "item_category": item_category,
        "required_price": required_price,
        "principal_needed": principal_needed,
        "agent_money_before": money_before,
        "agent_money_after": money_after,
        "expected_item_type": expected_item_type,
        "credit_sized_to_purchase": True,
        "new_total": int(account.get("outstanding_total") or 0),
        "location_id": agent.get("location_id"),
    }

    from app.games.zone_stalkers.rules.tick_rules import _add_memory
    _add_memory(
        agent,
        world_turn,
        state,
        "action",
        "Получил кредит на выживание",
        {
            "action_kind": "debt_credit_advanced",
            **event_payload,
        },
        summary=f"Взял кредит {amount} у {creditor_id}",
        agent_id=agent_id,
    )

    return [
        {
            "event_type": "debt_credit_advanced",
            "payload": event_payload,
        },
        *episode_events,
    ]
