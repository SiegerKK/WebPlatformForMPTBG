from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.constants import (
    ARMOR_CLASS_RANK,
    CRITICAL_REST_THRESHOLD,
    EMISSION_DANGEROUS_TERRAIN,
    HUNT_MIN_AMMO_ROUNDS,
    HUNT_MIN_ARMOR_CLASS_FOR_STRONG_TARGET,
    HUNT_MIN_CASH_RESERVE,
    HUNT_MIN_MED_ITEMS,
    HUNT_REQUIRED_ADVANTAGE_SCORE,
    HUNT_REQUIRED_ADVANTAGE_SCORE_CO_LOCATED,
    HUNT_REQUIRED_ADVANTAGE_SCORE_STRONG_TARGET,
    SOFT_RESTORE_DRINK_THRESHOLD,
    SOFT_RESTORE_FOOD_THRESHOLD,
    SOFT_REST_THRESHOLD,
    WEAPON_CLASS_RANK,
)
from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveGenerationContext
from app.games.zone_stalkers.economy.debts import (
    DEBT_ESCAPE_THRESHOLD,
    DEBT_REPAYMENT_KEEP_SURVIVAL_RESERVE,
    DEBT_REPAYMENT_KEEP_TRAVEL_RESERVE,
)


OBJECTIVE_RESTORE_WATER = "RESTORE_WATER"
OBJECTIVE_RESTORE_FOOD = "RESTORE_FOOD"
OBJECTIVE_HEAL_SELF = "HEAL_SELF"
OBJECTIVE_REST = "REST"

OBJECTIVE_RESUPPLY_FOOD = "RESUPPLY_FOOD"
OBJECTIVE_RESUPPLY_DRINK = "RESUPPLY_DRINK"
OBJECTIVE_RESUPPLY_MEDICINE = "RESUPPLY_MEDICINE"
OBJECTIVE_RESUPPLY_WEAPON = "RESUPPLY_WEAPON"
OBJECTIVE_RESUPPLY_ARMOR = "RESUPPLY_ARMOR"
OBJECTIVE_RESUPPLY_AMMO = "RESUPPLY_AMMO"
OBJECTIVE_GET_MONEY_FOR_RESUPPLY = "GET_MONEY_FOR_RESUPPLY"

OBJECTIVE_REACH_SAFE_SHELTER = "REACH_SAFE_SHELTER"
OBJECTIVE_WAIT_IN_SHELTER = "WAIT_IN_SHELTER"
OBJECTIVE_ESCAPE_DANGER = "ESCAPE_DANGER"

OBJECTIVE_FIND_ARTIFACTS = "FIND_ARTIFACTS"
OBJECTIVE_SELL_ARTIFACTS = "SELL_ARTIFACTS"
OBJECTIVE_HUNT_TARGET = "HUNT_TARGET"
OBJECTIVE_SEARCH_INFORMATION = "SEARCH_INFORMATION"
OBJECTIVE_LEAVE_ZONE = "LEAVE_ZONE"
OBJECTIVE_REPAY_DEBT = "REPAY_DEBT"
OBJECTIVE_IDLE = "IDLE"

OBJECTIVE_CONTINUE_CURRENT_PLAN = "CONTINUE_CURRENT_PLAN"

# Reserved hunt decomposition keys (pre-PR5 prerequisite).
OBJECTIVE_GATHER_INTEL = "GATHER_INTEL"
OBJECTIVE_LOCATE_TARGET = "LOCATE_TARGET"
OBJECTIVE_PREPARE_FOR_HUNT = "PREPARE_FOR_HUNT"
OBJECTIVE_VERIFY_LEAD = "VERIFY_LEAD"
OBJECTIVE_TRACK_TARGET = "TRACK_TARGET"
OBJECTIVE_INTERCEPT_TARGET = "INTERCEPT_TARGET"
OBJECTIVE_AMBUSH_TARGET = "AMBUSH_TARGET"
OBJECTIVE_ENGAGE_TARGET = "ENGAGE_TARGET"
OBJECTIVE_CONFIRM_KILL = "CONFIRM_KILL"
OBJECTIVE_RETREAT_FROM_TARGET = "RETREAT_FROM_TARGET"
OBJECTIVE_RECOVER_AFTER_COMBAT = "RECOVER_AFTER_COMBAT"

HUNT_OBJECTIVE_KEYS: tuple[str, ...] = (
    OBJECTIVE_GATHER_INTEL,
    OBJECTIVE_LOCATE_TARGET,
    OBJECTIVE_PREPARE_FOR_HUNT,
    OBJECTIVE_VERIFY_LEAD,
    OBJECTIVE_TRACK_TARGET,
    OBJECTIVE_INTERCEPT_TARGET,
    OBJECTIVE_AMBUSH_TARGET,
    OBJECTIVE_ENGAGE_TARGET,
    OBJECTIVE_CONFIRM_KILL,
    OBJECTIVE_RETREAT_FROM_TARGET,
    OBJECTIVE_RECOVER_AFTER_COMBAT,
)

BLOCKING_OBJECTIVE_KEYS: tuple[str, ...] = (
    OBJECTIVE_RESTORE_WATER,
    OBJECTIVE_RESTORE_FOOD,
    OBJECTIVE_HEAL_SELF,
    OBJECTIVE_REACH_SAFE_SHELTER,
    OBJECTIVE_ESCAPE_DANGER,
)

ITEM_NEED_TO_OBJECTIVE = {
    "weapon": OBJECTIVE_RESUPPLY_WEAPON,
    "armor": OBJECTIVE_RESUPPLY_ARMOR,
    "ammo": OBJECTIVE_RESUPPLY_AMMO,
    "food": OBJECTIVE_RESUPPLY_FOOD,
    "drink": OBJECTIVE_RESUPPLY_DRINK,
    "medicine": OBJECTIVE_RESUPPLY_MEDICINE,
}

_MIN_HUNT_INTEL_CASH_RESERVE = 200


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _soft_need_value(value: int, threshold: int) -> float:
    if value < threshold:
        return 0.0
    return min(0.85, 0.35 + ((value - threshold) / max(1, 100 - threshold)) * 0.50)


def _memory_ref(memory_id: str) -> str:
    return f"memory:{memory_id}"


def _is_semantic_decision_memory(kind: str) -> bool:
    return kind in {"semantic_v2_decision", "v2_decision"}


def _objective_memory_refs_and_confidence(
    ctx: ObjectiveGenerationContext,
    objective_key: str,
    category: str | None = None,
) -> tuple[tuple[str, ...], float]:
    from app.games.zone_stalkers.balance.items import DRINK_ITEM_TYPES, FOOD_ITEM_TYPES

    refs: list[str] = []
    confidences: list[float] = []
    relevant = tuple(
        mem
        for mem in ctx.belief_state.relevant_memories
        if not _is_semantic_decision_memory(str(mem.get("kind") or ""))
    )

    def _append(memory_id: str | None, confidence: float | None) -> None:
        if not memory_id:
            return
        ref = _memory_ref(str(memory_id))
        if ref in refs:
            return
        refs.append(ref)
        if confidence is not None:
            confidences.append(_clamp01(float(confidence)))

    def _relevant_summary_tokens(mem: dict[str, Any], tokens: tuple[str, ...]) -> bool:
        summary = str(mem.get("summary") or "").lower()
        return any(token in summary for token in tokens)

    if category in {"drink", "food"}:
        match_types = set(DRINK_ITEM_TYPES if category == "drink" else FOOD_ITEM_TYPES)
        for known in ctx.belief_state.known_items:
            item_types = set(known.get("item_types") or [])
            if item_types & match_types:
                _append(known.get("memory_id"), known.get("confidence"))

    if objective_key == OBJECTIVE_RESTORE_WATER:
        for mem in relevant:
            kind = str(mem.get("kind") or "")
            if kind in {"water_source_known", "item_bought", "trader_location_known"} or _relevant_summary_tokens(
                mem,
                ("вода", "water", "drink", "жажд"),
            ):
                _append(mem.get("id"), mem.get("confidence"))
    elif objective_key == OBJECTIVE_RESTORE_FOOD:
        for mem in relevant:
            kind = str(mem.get("kind") or "")
            if kind in {"food_source_known", "item_bought", "trader_location_known"} or _relevant_summary_tokens(
                mem,
                ("еда", "food", "bread", "голод"),
            ):
                _append(mem.get("id"), mem.get("confidence"))
    elif objective_key in {OBJECTIVE_GET_MONEY_FOR_RESUPPLY, OBJECTIVE_FIND_ARTIFACTS}:
        for mem in relevant:
            kind = str(mem.get("kind") or "")
            if kind in {"artifact_source_known", "trader_location_known", "trader_buys_artifacts", "artifact_sale"} or _relevant_summary_tokens(
                mem,
                ("artifact", "артеф", "sell", "продаж", "trader", "торгов"),
            ):
                _append(mem.get("id"), mem.get("confidence"))
        for trader in ctx.belief_state.known_traders:
            _append(trader.get("memory_id"), trader.get("confidence"))
    elif objective_key == OBJECTIVE_SELL_ARTIFACTS:
        for mem in relevant:
            kind = str(mem.get("kind") or "")
            if kind in {"trader_location_known", "trader_buys_artifacts", "artifact_sale"} or _relevant_summary_tokens(
                mem,
                ("sell", "продаж", "artifact", "артеф", "trader", "торгов"),
            ):
                _append(mem.get("id"), mem.get("confidence"))
        for trader in ctx.belief_state.known_traders:
            _append(trader.get("memory_id"), trader.get("confidence"))
    elif objective_key in {
        OBJECTIVE_VERIFY_LEAD, OBJECTIVE_TRACK_TARGET, OBJECTIVE_GATHER_INTEL,
        OBJECTIVE_LOCATE_TARGET, OBJECTIVE_ENGAGE_TARGET, OBJECTIVE_CONFIRM_KILL,
        OBJECTIVE_PREPARE_FOR_HUNT, OBJECTIVE_INTERCEPT_TARGET,
    }:
        # Fix 10: Only use hunt-relevant memory kinds, exclude active_plan_* events
        _hunt_relevant_kinds = {
            "target_intel", "target_seen", "target_last_known_location",
            "target_moved", "target_route_observed", "target_not_found",
        }
        for mem in relevant:
            kind = str(mem.get("kind") or "")
            if kind in _hunt_relevant_kinds:
                _append(mem.get("id"), mem.get("confidence"))
    else:
        for mem in relevant:
            _append(mem.get("id"), mem.get("confidence"))

    refs = refs[:3]
    if not confidences:
        return tuple(refs), 0.5
    return tuple(refs), _clamp01(sum(confidences) / len(confidences))


def _global_goal_objective(goal: str) -> str:
    if goal == "get_rich":
        return OBJECTIVE_FIND_ARTIFACTS
    if goal == "kill_stalker":
        return OBJECTIVE_HUNT_TARGET
    if goal == "unravel_zone_mystery":
        return OBJECTIVE_SEARCH_INFORMATION
    if goal == "leave_zone":
        return OBJECTIVE_LEAVE_ZONE
    return OBJECTIVE_IDLE


def _is_critical_need(key: str, urgency: float) -> bool:
    if key in {"drink_now", "eat_now", "heal_now"} and urgency >= 0.8:
        return True
    return False


def _append_unique(result: list[Objective], objective: Objective) -> None:
    if any(existing.key == objective.key for existing in result):
        return
    result.append(objective)


def _replace_or_append(result: list[Objective], objective: Objective) -> None:
    for index, existing in enumerate(result):
        if existing.key == objective.key:
            result[index] = objective
            return
    result.append(objective)



def _get_weapon_class(agent: dict[str, Any]) -> str:
    """Return weapon class string from agent's equipped weapon or inventory."""
    equipment = agent.get("equipment") if isinstance(agent.get("equipment"), dict) else {}
    weapon = equipment.get("weapon")
    if isinstance(weapon, dict):
        wtype = str(weapon.get("type") or weapon.get("weapon_class") or "")
        for wc in WEAPON_CLASS_RANK:
            if wc != "none" and wc in wtype:
                return wc
        if wtype:
            return "pistol"  # default non-melee if something equipped
    return "none"


def _get_armor_class(agent: dict[str, Any]) -> str:
    """Return armor class string from agent's equipped armor."""
    equipment = agent.get("equipment") if isinstance(agent.get("equipment"), dict) else {}
    armor = equipment.get("armor")
    if isinstance(armor, dict):
        atype = str(armor.get("type") or armor.get("armor_class") or armor.get("protection_class") or "")
        for ac in ARMOR_CLASS_RANK:
            if ac not in {"none", "unknown"} and ac in atype:
                return ac
        if atype:
            return "light"  # default if something equipped
    return "none"


def _count_ammo_items(agent: dict[str, Any]) -> int:
    """Return total count of ammo items in agent inventory."""
    inventory = agent.get("inventory")
    if not isinstance(inventory, list):
        return 0
    return sum(
        1
        for item in inventory
        if isinstance(item, dict) and str(item.get("type") or "").startswith("ammo")
    )


def _count_med_items(agent: dict[str, Any]) -> int:
    """Return total count of medical items in agent inventory."""
    inventory = agent.get("inventory")
    if not isinstance(inventory, list):
        return 0
    med_types = {"bandage", "medkit", "stimpack", "antidote", "medicine"}
    return sum(
        1
        for item in inventory
        if isinstance(item, dict)
        and any(str(item.get("type") or "").startswith(mt) for mt in med_types)
    )


def evaluate_hunter_equipment_advantage(
    *,
    agent: dict[str, Any],
    target_belief: Any,
    need_result: Any,
    world_turn: int,
) -> dict[str, Any]:
    """Compute hunter equipment advantage vs known target equipment.

    Returns a dict with:
    - target_equipment_known (bool)
    - target_weapon_class, target_armor_class, target_combat_strength
    - own_weapon_class, own_armor_class, own_ammo_count, own_med_count, own_hp
    - advantage_score, required_advantage_score
    - is_advantaged (bool)
    - missing_requirements (list[str])
    - estimated_money_needed (int)
    - recommended_support_objective (str | None)
    """
    from app.games.zone_stalkers.knowledge.knowledge_hunt_builder import (  # noqa: PLC0415
        build_equipment_belief_from_knowledge,
    )
    target_id = str(agent.get("kill_target_id") or "") or None

    # Own equipment
    own_weapon_class = _get_weapon_class(agent)
    own_armor_class = _get_armor_class(agent)
    own_ammo_count = _count_ammo_items(agent)
    own_med_count = _count_med_items(agent)
    own_hp = int(agent.get("hp") or 100)

    # Target equipment from knowledge
    target_weapon_class = "none"
    target_armor_class = "none"
    target_combat_strength: float | None = None
    target_equipment_known = False

    if target_id:
        eq_belief = build_equipment_belief_from_knowledge(
            agent=agent, target_id=target_id, world_turn=world_turn
        )
        target_equipment_known = bool(eq_belief.get("equipment_known"))
        target_combat_strength = eq_belief.get("combat_strength")
        # Also read from target_belief if available (may be more up-to-date from direct obs)
        if target_belief is not None:
            if not target_equipment_known and bool(target_belief.equipment_known):
                target_equipment_known = True
            if target_combat_strength is None and target_belief.combat_strength is not None:
                target_combat_strength = float(target_belief.combat_strength)

    # Try to read weapon/armor class from known_npcs equipment_summary
    if target_id:
        knowledge = agent.get("knowledge_v1")
        known_npcs = knowledge.get("known_npcs") if isinstance(knowledge, dict) else None
        npc_entry = known_npcs.get(target_id) if isinstance(known_npcs, dict) else None
        if isinstance(npc_entry, dict):
            eq_summary = npc_entry.get("equipment_summary")
            if isinstance(eq_summary, dict):
                wc = str(eq_summary.get("weapon_class") or "")
                if wc and wc not in {"", "unknown"}:
                    target_weapon_class = wc
                ac = str(eq_summary.get("armor_class") or "")
                if ac and ac not in {"", "unknown"}:
                    target_armor_class = ac

    if target_combat_strength is None:
        target_combat_strength = 0.5  # conservative assumption

    # Advantage scoring
    own_weapon_score = WEAPON_CLASS_RANK.get(own_weapon_class, 0)
    target_weapon_score = WEAPON_CLASS_RANK.get(target_weapon_class, 0)
    own_armor_score = ARMOR_CLASS_RANK.get(own_armor_class, 0)
    target_armor_score = ARMOR_CLASS_RANK.get(target_armor_class, 0)

    weapon_delta = own_weapon_score - target_weapon_score
    armor_delta = own_armor_score - target_armor_score
    ammo_bonus = min(0.25, own_ammo_count / max(1, HUNT_MIN_AMMO_ROUNDS * 2))
    med_bonus = min(0.20, own_med_count / max(1, HUNT_MIN_MED_ITEMS + 1))
    hp_bonus = (own_hp - 60) / 200.0
    target_strength_penalty = float(target_combat_strength) * 0.5

    advantage_score = (
        weapon_delta * 0.35
        + armor_delta * 0.25
        + ammo_bonus
        + med_bonus
        + hp_bonus
        - target_strength_penalty
    )

    # Required advantage threshold
    required_advantage_score = HUNT_REQUIRED_ADVANTAGE_SCORE
    if float(target_combat_strength) >= 0.8:
        required_advantage_score = HUNT_REQUIRED_ADVANTAGE_SCORE_STRONG_TARGET
    target_co_located = bool(target_belief.co_located) if target_belief else False
    if target_co_located:
        required_advantage_score = HUNT_REQUIRED_ADVANTAGE_SCORE_CO_LOCATED

    is_advantaged = advantage_score >= required_advantage_score

    # Missing requirements
    missing_requirements: list[str] = []
    if target_equipment_known and target_weapon_class not in {"none", "melee"}:
        if own_weapon_score < target_weapon_score:
            missing_requirements.append("weapon_upgrade")
    if own_ammo_count < HUNT_MIN_AMMO_ROUNDS:
        missing_requirements.append("ammo_resupply")
    if own_med_count < HUNT_MIN_MED_ITEMS:
        missing_requirements.append("medicine_resupply")
    if float(target_combat_strength) >= 0.8 and ARMOR_CLASS_RANK.get(own_armor_class, 0) < 1:
        missing_requirements.append("armor_upgrade")

    # Estimated money needed (rough)
    estimated_money_needed = 0
    if "weapon_upgrade" in missing_requirements:
        estimated_money_needed += 1500
    if "ammo_resupply" in missing_requirements:
        estimated_money_needed += 400
    if "medicine_resupply" in missing_requirements:
        estimated_money_needed += 300
    if "armor_upgrade" in missing_requirements:
        estimated_money_needed += 1200

    money = int(agent.get("money") or 0)
    recommended_support_objective: str | None = None
    if not is_advantaged:
        recommended_support_objective = (
            OBJECTIVE_GET_MONEY_FOR_RESUPPLY if money < estimated_money_needed
            else OBJECTIVE_PREPARE_FOR_HUNT
        )

    # Determine minimum required classes based on target knowledge
    _target_weapon_score_for_req = WEAPON_CLASS_RANK.get(target_weapon_class, 0)
    _weapon_min_class = "none"
    for wc, rank in WEAPON_CLASS_RANK.items():
        if rank >= _target_weapon_score_for_req and wc not in {"none"}:
            _weapon_min_class = wc
            break
    _armor_min_class = HUNT_MIN_ARMOR_CLASS_FOR_STRONG_TARGET if float(target_combat_strength) >= 0.8 else "light"

    required_hunt_equipment: dict[str, Any] = {
        "weapon_min_class": _weapon_min_class,
        "armor_min_class": _armor_min_class,
        "ammo_min_count": HUNT_MIN_AMMO_ROUNDS,
        "medical_min_count": HUNT_MIN_MED_ITEMS,
        "missing_requirements": list(missing_requirements),
    }

    return {
        "target_equipment_known": target_equipment_known,
        "target_weapon_class": target_weapon_class,
        "target_armor_class": target_armor_class,
        "target_combat_strength": float(target_combat_strength),
        "own_weapon_class": own_weapon_class,
        "own_armor_class": own_armor_class,
        "own_ammo_count": own_ammo_count,
        "own_med_count": own_med_count,
        "own_hp": own_hp,
        "advantage_score": round(advantage_score, 3),
        "required_advantage_score": round(required_advantage_score, 3),
        "is_advantaged": is_advantaged,
        "missing_requirements": missing_requirements,
        "estimated_money_needed": estimated_money_needed,
        "recommended_support_objective": recommended_support_objective,
        "required_hunt_equipment": required_hunt_equipment,
    }


def evaluate_kill_target_combat_readiness(
    *,
    agent: dict[str, Any],
    target_belief: Any,
    need_result: Any,
    context: Any,
    world_turn: int = 0,
) -> dict[str, Any]:
    del context
    readiness = (need_result.combat_readiness or {}) if need_result is not None else {}
    equipment = agent.get("equipment") if isinstance(agent.get("equipment"), dict) else {}
    liquidity = (need_result.liquidity_summary or {}) if need_result is not None else {}
    hp = int(agent.get("hp") or 100)
    weapon_ready = bool(equipment.get("weapon")) and int(readiness.get("weapon_missing") or 0) <= 0
    ammo_ready = int(readiness.get("ammo_missing") or 0) <= 0
    armor_ready = bool(equipment.get("armor"))
    hp_ready = hp > 35
    target_visible_now = bool(target_belief.visible_now) if target_belief else False
    target_co_located = bool(target_belief.co_located) if target_belief else False
    target_strength = (
        float(target_belief.combat_strength)
        if target_belief and target_belief.combat_strength is not None
        else None
    )
    money_missing = int(liquidity.get("money_missing") or 0)
    medicine_missing = int(readiness.get("medicine_missing") or 0)

    reasons: list[str] = []
    if not weapon_ready:
        reasons.append("no_weapon")
    if not ammo_ready:
        reasons.append("low_ammo")
    if not hp_ready:
        reasons.append("hp_low")
    if medicine_missing > 0 and hp < 70:
        reasons.append("no_medicine")
    if target_strength is not None and target_strength >= 0.8 and not armor_ready:
        reasons.append("no_armor")
        reasons.append("target_too_strong")
    elif not armor_ready and target_co_located:
        reasons.append("no_armor")
    elif target_strength is not None and target_strength >= 0.95 and hp < 60:
        reasons.append("target_too_strong")

    # Equipment-based advantage evaluation
    advantage_result = evaluate_hunter_equipment_advantage(
        agent=agent,
        target_belief=target_belief,
        need_result=need_result,
        world_turn=world_turn,
    )
    equipment_advantaged = bool(advantage_result["is_advantaged"])
    if advantage_result["target_equipment_known"] and not equipment_advantaged:
        reasons.append("equipment_disadvantage")
        if "weapon_upgrade" in advantage_result["missing_requirements"]:
            reasons.append("weapon_inferior")
        if "armor_upgrade" in advantage_result["missing_requirements"]:
            reasons.append("armor_inferior")
        if "medicine_resupply" in advantage_result["missing_requirements"]:
            if "no_medicine" not in reasons:
                reasons.append("no_medicine")
        if "ammo_resupply" in advantage_result["missing_requirements"]:
            if "low_ammo" not in reasons:
                reasons.append("low_ammo")

    recommended_support_objective: str | None = None
    if "hp_low" in reasons:
        recommended_support_objective = OBJECTIVE_HEAL_SELF
    elif advantage_result["recommended_support_objective"]:
        recommended_support_objective = advantage_result["recommended_support_objective"]
    elif any(reason in reasons for reason in ("no_weapon", "low_ammo", "no_armor", "target_too_strong")):
        recommended_support_objective = (
            OBJECTIVE_GET_MONEY_FOR_RESUPPLY if money_missing > 0 else OBJECTIVE_PREPARE_FOR_HUNT
        )

    if recommended_support_objective == OBJECTIVE_GET_MONEY_FOR_RESUPPLY and money_missing > 0:
        reasons.append("money_missing_for_resupply")

    reasons = list(dict.fromkeys(reasons))
    combat_ready = not reasons
    should_engage_now = combat_ready and target_visible_now and target_co_located
    return {
        "combat_ready": combat_ready,
        "should_engage_now": should_engage_now,
        "reasons": reasons,
        "target_visible_now": target_visible_now,
        "target_co_located": target_co_located,
        "target_strength": target_strength,
        "weapon_ready": weapon_ready,
        "ammo_ready": ammo_ready,
        "armor_ready": armor_ready,
        "hp_ready": hp_ready,
        "recommended_support_objective": recommended_support_objective,
        "equipment_advantage": advantage_result,
        "equipment_advantaged": equipment_advantaged,
        "estimated_money_needed_for_advantage": advantage_result["estimated_money_needed"],
    }


def has_recent_trade_sell_failure(
    ctx: "ObjectiveGenerationContext",
    *,
    trader_id: str | None,
    location_id: str | None,
    item_types: set[str],
    world_turn: int,
) -> bool:
    """Return True if active trade_sell_failed cooldown matches trader/location/item overlap."""
    from app.games.zone_stalkers.decision.trade_sell_failures import has_recent_trade_sell_failure_for_agent

    return has_recent_trade_sell_failure_for_agent(
        getattr(ctx, "personality", None),
        trader_id=trader_id,
        location_id=location_id,
        item_types=item_types,
        world_turn=world_turn,
    )


def generate_objectives(ctx: ObjectiveGenerationContext) -> list[Objective]:
    """Generate objective candidates from need_result, environment, goals and active plan."""
    result: list[Objective] = []
    agent = ctx.personality
    need_result = ctx.need_result
    hunger = int(agent.get("hunger") or 0)
    thirst = int(agent.get("thirst") or 0)
    liquidity = need_result.liquidity_summary or {}
    money_missing = int(liquidity.get("money_missing") or 0)

    for immediate in need_result.immediate_needs:
        key = None
        if immediate.key == "drink_now":
            key = OBJECTIVE_RESTORE_WATER
        elif immediate.key == "eat_now":
            key = OBJECTIVE_RESTORE_FOOD
        elif immediate.key == "heal_now":
            key = OBJECTIVE_HEAL_SELF
        if key is None or immediate.urgency <= 0:
            continue

        immediate_refs, immediate_mem_conf = _objective_memory_refs_and_confidence(ctx, key)
        reasons = [immediate.reason] if immediate.reason else []
        metadata: dict[str, Any] = {
            "is_blocking": _is_critical_need(immediate.key, float(immediate.urgency)),
            "critical": float(immediate.urgency) >= 0.8,
        }
        if immediate.selected_item_type:
            metadata["selected_item_type"] = immediate.selected_item_type
            metadata["has_inventory_solution"] = True

        _append_unique(
            result,
            Objective(
                key=key,
                source="immediate_need",
                urgency=_clamp01(immediate.urgency),
                expected_value=1.0,
                risk=0.05,
                time_cost=0.05 if immediate.selected_item_type else 0.25,
                resource_cost=0.0,
                confidence=1.0,
                goal_alignment=0.9,
                memory_confidence=immediate_mem_conf,
                reasons=tuple(reasons),
                source_refs=(f"immediate:{immediate.key}",) + immediate_refs,
                metadata=metadata,
            ),
        )


    can_generate_soft_water_restore = thirst >= SOFT_RESTORE_DRINK_THRESHOLD
    if not any(o.key == OBJECTIVE_RESTORE_WATER for o in result) and can_generate_soft_water_restore:
        drink_memory_refs, drink_memory_conf = _objective_memory_refs_and_confidence(
            ctx,
            OBJECTIVE_RESTORE_WATER,
            category="drink",
        )
        drink_reasons = ["Жажда растёт"]
        if drink_memory_refs:
            drink_reasons.append("По памяти известен источник воды")
        soft_drink_value = _soft_need_value(thirst, SOFT_RESTORE_DRINK_THRESHOLD)
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_RESTORE_WATER,
                source="soft_need",
                urgency=_clamp01(need_result.scores.drink),
                expected_value=soft_drink_value,
                risk=0.1,
                time_cost=0.3,
                resource_cost=0.1,
                confidence=0.9,
                goal_alignment=0.8,
                memory_confidence=drink_memory_conf,
                reasons=tuple(drink_reasons),
                source_refs=("need:drink",) + drink_memory_refs,
                metadata={
                    "is_blocking": float(need_result.scores.drink) >= 0.8,
                    "critical": float(need_result.scores.drink) >= 0.8,
                    "soft_threshold": SOFT_RESTORE_DRINK_THRESHOLD,
                },
            ),
        )

    can_generate_soft_food_restore = hunger >= SOFT_RESTORE_FOOD_THRESHOLD
    if not any(o.key == OBJECTIVE_RESTORE_FOOD for o in result) and can_generate_soft_food_restore:
        food_memory_refs, food_memory_conf = _objective_memory_refs_and_confidence(
            ctx,
            OBJECTIVE_RESTORE_FOOD,
            category="food",
        )
        food_reasons = ["Голод растёт"]
        if food_memory_refs:
            food_reasons.append("По памяти известен источник еды")
        soft_food_value = _soft_need_value(hunger, SOFT_RESTORE_FOOD_THRESHOLD)
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_RESTORE_FOOD,
                source="soft_need",
                urgency=_clamp01(need_result.scores.eat),
                expected_value=soft_food_value,
                risk=0.1,
                time_cost=0.3,
                resource_cost=0.1,
                confidence=0.9,
                goal_alignment=0.75,
                memory_confidence=food_memory_conf,
                reasons=tuple(food_reasons),
                source_refs=("need:eat",) + food_memory_refs,
                metadata={
                    "is_blocking": float(need_result.scores.eat) >= 0.8,
                    "critical": float(need_result.scores.eat) >= 0.8,
                    "soft_threshold": SOFT_RESTORE_FOOD_THRESHOLD,
                },
            ),
        )

    sleep_score = float(need_result.scores.sleep)
    sleepiness = int(agent.get("sleepiness") or 0)
    hp = int(agent.get("hp") or 100)
    radiation = int(agent.get("radiation") or 0)
    current_terrain = str(ctx.belief_state.current_location.get("terrain_type") or "")
    is_safe_for_recovery = current_terrain not in EMISSION_DANGEROUS_TERRAIN
    is_critical_rest = sleepiness >= CRITICAL_REST_THRESHOLD
    is_soft_rest = SOFT_REST_THRESHOLD <= sleepiness < CRITICAL_REST_THRESHOLD
    is_recovery_rest = is_safe_for_recovery and not is_critical_rest and (hp <= 45 or radiation >= 35)

    if is_critical_rest or is_soft_rest or is_recovery_rest:
        rest_refs, rest_memory_conf = _objective_memory_refs_and_confidence(ctx, OBJECTIVE_REST)
        if is_critical_rest:
            rest_source = "immediate_need"
            rest_reasons = ("Критическое истощение — нужен срочный отдых",)
            rest_urgency = max(_clamp01(sleep_score), 0.8)
            rest_expected_value = min(1.0, 0.55 + (_clamp01(rest_urgency) * 0.45))
            rest_metadata: dict[str, Any] = {
                "is_blocking": True,
                "critical": True,
                "soft_threshold": SOFT_REST_THRESHOLD,
                "critical_threshold": CRITICAL_REST_THRESHOLD,
            }
        elif is_recovery_rest:
            rest_source = "recovery_need"
            rest_reasons = ("Восстановление после ранений / снятие радиации",)
            recovery_pressure = max(
                _clamp01((50 - hp) / 50.0),
                _clamp01((radiation - 20) / 80.0),
            )
            rest_urgency = max(_clamp01(sleep_score), recovery_pressure)
            rest_expected_value = min(0.9, 0.35 + (rest_urgency * 0.45))
            rest_metadata = {
                "is_blocking": False,
                "critical": False,
                "recovery_need": True,
                "recovery_hp": hp,
                "recovery_radiation": radiation,
            }
        else:
            rest_source = "soft_need"
            rest_reasons = ("Усталость растёт",)
            rest_urgency = _clamp01(sleep_score)
            rest_expected_value = min(0.85, 0.25 + (rest_urgency * 0.60))
            rest_metadata = {
                "is_blocking": False,
                "critical": False,
                "soft_threshold": SOFT_REST_THRESHOLD,
                "critical_threshold": CRITICAL_REST_THRESHOLD,
            }
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_REST,
                source=rest_source,
                urgency=rest_urgency,
                expected_value=rest_expected_value,
                risk=0.05,
                time_cost=0.4,
                resource_cost=0.0,
                confidence=0.9,
                goal_alignment=0.7,
                memory_confidence=rest_memory_conf,
                reasons=rest_reasons,
                source_refs=("need:sleep",) + rest_refs,
                metadata=rest_metadata,
            ),
        )

    dominant_item_urgency = 0.0
    for item_need in need_result.item_needs:
        if not getattr(item_need, "actionable", True):
            continue
        objective_key = ITEM_NEED_TO_OBJECTIVE.get(item_need.key)
        if objective_key is None or item_need.urgency <= 0:
            continue
        dominant_item_urgency = max(dominant_item_urgency, float(item_need.urgency))

        _metadata = {
            "is_blocking": False,
            "item_need_key": item_need.key,
            "missing_count": item_need.missing_count,
            "blockers": [],
        }
        _reasons = [item_need.reason] if item_need.reason else []
        if money_missing > 0:
            _penalty = 0.6 if objective_key == OBJECTIVE_RESUPPLY_WEAPON else 0.4
            _metadata["blockers"].append({
                "key": "insufficient_money",
                "reason": "Недостаточно денег для покупки",
                "blocked": True,
                "penalty": _penalty,
            })
            _reasons.append("Покупка недоступна без дополнительных денег")

        _append_unique(
            result,
            Objective(
                key=objective_key,
                source="item_need",
                urgency=_clamp01(item_need.urgency),
                expected_value=0.85,
                risk=0.2,
                time_cost=0.35,
                resource_cost=0.4,
                confidence=0.85,
                goal_alignment=0.8,
                memory_confidence=_objective_memory_refs_and_confidence(ctx, objective_key)[1],
                reasons=tuple(_reasons),
                source_refs=(f"item_need:{item_need.key}",),
                metadata=_metadata,
            ),
        )

    if dominant_item_urgency > 0 and money_missing > 0:
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
                source="item_need",
                urgency=_clamp01(dominant_item_urgency),
                expected_value=0.8,
                risk=0.3,
                time_cost=0.55,
                resource_cost=0.1,
                confidence=0.8,
                goal_alignment=0.75,
                memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_GET_MONEY_FOR_RESUPPLY)[1],
                reasons=("Не хватает денег для обязательного пополнения",),
                source_refs=("liquidity:money_missing",) + _objective_memory_refs_and_confidence(
                    ctx,
                    OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
                )[0],
                metadata={"is_blocking": False, "money_missing": money_missing},
            ),
        )

    emission_pressure = float(need_result.scores.avoid_emission)
    if emission_pressure > 0.05:
        is_safe_terrain = ctx.belief_state.current_location.get("terrain_type") not in {
            "plain", "hills", "swamp", "field_camp", "slag_heaps", "bridge",
        }
        if is_safe_terrain:
            _append_unique(
                result,
                Objective(
                    key=OBJECTIVE_WAIT_IN_SHELTER,
                    source="environment",
                    urgency=_clamp01(emission_pressure),
                    expected_value=0.9,
                    risk=0.02,
                    time_cost=0.15,
                    resource_cost=0.0,
                    confidence=0.95,
                    goal_alignment=0.85,
                    memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_WAIT_IN_SHELTER)[1],
                    reasons=("Нахожусь в безопасном укрытии во время выброса",),
                    source_refs=("need:avoid_emission",),
                    metadata={"is_blocking": True},
                ),
            )
        else:
            _append_unique(
                result,
                Objective(
                    key=OBJECTIVE_REACH_SAFE_SHELTER,
                    source="environment",
                    urgency=_clamp01(emission_pressure),
                    expected_value=1.0,
                    risk=0.1,
                    time_cost=0.2,
                    resource_cost=0.0,
                    confidence=0.95,
                    goal_alignment=0.9,
                    memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_REACH_SAFE_SHELTER)[1],
                    reasons=("Выброс в опасной зоне — нужно укрытие",),
                    source_refs=("need:avoid_emission",),
                    metadata={"is_blocking": True},
                ),
            )

    if float(need_result.scores.survive_now) >= 0.7:
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_ESCAPE_DANGER,
                source="environment",
                urgency=_clamp01(need_result.scores.survive_now),
                expected_value=1.0,
                risk=0.15,
                time_cost=0.15,
                resource_cost=0.0,
                confidence=0.95,
                goal_alignment=0.9,
                memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_ESCAPE_DANGER)[1],
                reasons=("Критически низкий HP",),
                source_refs=("need:survive_now",),
                metadata={"is_blocking": True},
            ),
        )

    if agent.get("global_goal_achieved") and not agent.get("has_left_zone"):
        leave_refs, leave_mem_conf = _objective_memory_refs_and_confidence(ctx, OBJECTIVE_LEAVE_ZONE)
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_LEAVE_ZONE,
                source="global_goal_completed",
                urgency=max(0.9, float(need_result.scores.leave_zone)),
                expected_value=1.0,
                risk=0.2,
                time_cost=0.5,
                resource_cost=0.1,
                confidence=0.95,
                goal_alignment=1.0,
                memory_confidence=leave_mem_conf,
                reasons=("Глобальная цель выполнена — пора покинуть Зону",),
                source_refs=(f"global_goal_completed:{agent.get('global_goal')}",) + leave_refs,
                metadata={"is_blocking": False, "completed_global_goal": agent.get("global_goal")},
            ),
        )

    global_goal = str(agent.get("global_goal") or "get_rich")
    global_key = OBJECTIVE_LEAVE_ZONE if agent.get("global_goal_achieved") else _global_goal_objective(global_goal)
    global_refs, global_memory_conf = _objective_memory_refs_and_confidence(ctx, global_key)
    _raw_global_urgency = max(0.1, float(getattr(need_result.scores, {
        "get_rich": "get_rich",
        "kill_stalker": "hunt_target",
        "unravel_zone_mystery": "unravel_zone_mystery",
        "leave_zone": "leave_zone",
    }.get(global_goal, "get_rich"), 0.1)))
    # Boost FIND_ARTIFACTS when the agent is already at a location with artifacts —
    # picking up a present artifact is cheap and highly profitable.
    _loc_has_artifact = bool(ctx.location_state.get("artifacts"))
    _loc_has_anomaly = int(ctx.location_state.get("anomaly_activity", 0)) > 0
    _artifact_boost = (
        global_goal == "get_rich"
        and global_key == OBJECTIVE_FIND_ARTIFACTS
        and _loc_has_artifact
        and _loc_has_anomaly
    )
    if _artifact_boost:
        _global_urgency = max(0.5, _raw_global_urgency)
        _global_ev, _global_risk, _global_tc, _global_conf = 0.92, 0.20, 0.15, 0.90
    else:
        _global_urgency = _raw_global_urgency
        _global_ev, _global_risk, _global_tc, _global_conf = 0.7, 0.35, 0.6, 0.7
    _append_unique(
        result,
        Objective(
            key=global_key,
            source="global_goal",
            urgency=_global_urgency,
            expected_value=_global_ev,
            risk=_global_risk,
            time_cost=_global_tc,
            resource_cost=0.2,
            confidence=_global_conf,
            goal_alignment=1.0,
            memory_confidence=global_memory_conf,
            reasons=(f"Глобальная цель: {global_goal}",),
            source_refs=(f"global_goal:{global_goal}",) + global_refs,
            metadata={"is_blocking": False},
        ),
    )

    if global_goal == "get_rich":
        from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
        artifact_types = frozenset(ARTIFACT_TYPES.keys())
        artifact_items_in_inventory = {
            str(i.get("type") or "")
            for i in agent.get("inventory", [])
            if i.get("type") in artifact_types
        }
        has_artifact = bool(artifact_items_in_inventory)
        current_location_id = str(agent.get("location_id") or "")
        known_traders = tuple(ctx.belief_state.known_traders or ())
        current_location_traders = tuple(
            trader for trader in known_traders
            if str(trader.get("location_id") or "") == current_location_id
        )

        current_location_cooldown = has_recent_trade_sell_failure(
            ctx,
            trader_id=None,
            location_id=current_location_id,
            item_types=artifact_items_in_inventory,
            world_turn=ctx.world_turn,
        )
        current_trader_cooldown = any(
            has_recent_trade_sell_failure(
                ctx,
                trader_id=str(trader.get("agent_id") or ""),
                location_id=current_location_id,
                item_types=artifact_items_in_inventory,
                world_turn=ctx.world_turn,
            )
            for trader in current_location_traders
        )
        local_sell_blocked = current_location_cooldown or current_trader_cooldown
        alternative_trader_available = any(
            not has_recent_trade_sell_failure(
                ctx,
                trader_id=str(trader.get("agent_id") or ""),
                location_id=str(trader.get("location_id") or ""),
                item_types=artifact_items_in_inventory,
                world_turn=ctx.world_turn,
            )
            for trader in known_traders
        )

        if has_artifact and (not local_sell_blocked or alternative_trader_available):
            sell_refs, sell_memory_conf = _objective_memory_refs_and_confidence(ctx, OBJECTIVE_SELL_ARTIFACTS)
            _sell_urgency = max(0.2, float(need_result.scores.trade))
            _append_unique(
                result,
                Objective(
                    key=OBJECTIVE_SELL_ARTIFACTS,
                    source="global_goal",
                    urgency=_sell_urgency,
                    expected_value=0.9,
                    risk=0.15,
                    time_cost=0.25,
                    resource_cost=0.0,
                    confidence=0.85,
                    goal_alignment=0.98,
                    memory_confidence=sell_memory_conf,
                    reasons=("В инвентаре есть артефакт для продажи",),
                    source_refs=("global_goal:get_rich", "inventory:artifact") + sell_refs,
                    metadata={"is_blocking": False},
                ),
            )

    if global_goal == "kill_stalker" and not agent.get("global_goal_achieved"):
        target_belief = ctx.target_belief
        world_turn = int(ctx.world_turn) if hasattr(ctx, "world_turn") and ctx.world_turn is not None else 0
        combat_eval = evaluate_kill_target_combat_readiness(
            agent=agent,
            target_belief=target_belief,
            need_result=need_result,
            context=ctx,
            world_turn=world_turn,
        )
        blockers: list[dict[str, Any]] = []
        prepare_reasons: list[str] = ["Охота требует предварительной готовности"]
        if "no_weapon" in combat_eval["reasons"]:
            blockers.append({"key": "no_weapon", "reason": "Нет оружия", "blocked": True, "penalty": 0.55})
            prepare_reasons.append("Нет оружия")
        if "low_ammo" in combat_eval["reasons"]:
            blockers.append({"key": "low_ammo", "reason": "Недостаточно патронов", "blocked": True, "penalty": 0.45})
            prepare_reasons.append("Недостаточно патронов")
        if "hp_low" in combat_eval["reasons"]:
            blockers.append({"key": "hp_low", "reason": "HP слишком низкий", "blocked": True, "penalty": 0.5})
            prepare_reasons.append("Нужно восстановить HP")
        if "no_armor" in combat_eval["reasons"]:
            blockers.append({"key": "no_armor", "reason": "Нет брони", "blocked": True, "penalty": 0.35})
            prepare_reasons.append("Нет брони")
        if "target_too_strong" in combat_eval["reasons"]:
            blockers.append({"key": "target_too_strong", "reason": "Цель слишком сильная сейчас", "blocked": True, "penalty": 0.5})
            prepare_reasons.append("Цель слишком сильная для текущего состояния")
        if "money_missing_for_resupply" in combat_eval["reasons"]:
            blockers.append({"key": "money_missing_for_resupply", "reason": "Не хватает денег на пополнение", "blocked": False, "penalty": 0.25})
            prepare_reasons.append("Нужно добыть деньги на пополнение")
        if "equipment_disadvantage" in combat_eval["reasons"]:
            blockers.append({"key": "equipment_disadvantage", "reason": "Снаряжение хуже цели", "blocked": True, "penalty": 0.45})
            prepare_reasons.append("Нужно улучшить снаряжение для охоты")
        if "weapon_inferior" in combat_eval["reasons"]:
            blockers.append({"key": "weapon_upgrade", "reason": "Оружие слабее цели", "blocked": True, "penalty": 0.40})
            prepare_reasons.append("Оружие слабее цели")
        if "armor_inferior" in combat_eval["reasons"]:
            blockers.append({"key": "armor_upgrade", "reason": "Броня хуже цели", "blocked": True, "penalty": 0.30})
            prepare_reasons.append("Броня хуже цели")
        if "ammo_resupply" in combat_eval.get("equipment_advantage", {}).get("missing_requirements", []):
            if not any(b["key"] == "low_ammo" for b in blockers):
                blockers.append({"key": "ammo_resupply", "reason": "Недостаточно патронов для охоты", "blocked": True, "penalty": 0.35})
        if "medicine_resupply" in combat_eval.get("equipment_advantage", {}).get("missing_requirements", []):
            if not any(b["key"] in ("no_medicine", "medicine_resupply") for b in blockers):
                blockers.append({"key": "medicine_resupply", "reason": "Недостаточно медикаментов для охоты", "blocked": True, "penalty": 0.25})

        target_id = str(agent.get("kill_target_id") or "")
        best_target_loc = target_belief.best_location_id if target_belief else None
        target_loc = best_target_loc or (target_belief.last_known_location_id if target_belief else None)
        target_visible_now = bool(combat_eval["target_visible_now"])
        target_co_located = bool(combat_eval["target_co_located"])
        target_alive = target_belief.is_alive if target_belief else None
        exhausted_locations = set(target_belief.exhausted_locations) if target_belief else set()
        route_hypothesis = target_belief.likely_routes[0] if target_belief and target_belief.likely_routes else None

        # Fix 3: Gather recently_seen metadata for current agent location
        _current_agent_loc = str(agent.get("location_id") or "")
        _target_recently_here = (
            target_belief is not None
            and bool(target_belief.recently_seen)
            and target_belief.recent_contact_location_id == _current_agent_loc
            and not target_co_located
            and not target_visible_now
        )
        _has_survival_emergency = float(need_result.scores.survive_now) >= 0.7 or any(
            n.key in {"drink_now", "eat_now", "heal_now"} and float(n.urgency) >= 0.8
            for n in need_result.immediate_needs
        )
        _combat_readiness_sufficient = bool(combat_eval["combat_ready"])
        _combat_metadata = {
            "is_blocking": False,
            "combat_ready": combat_eval["combat_ready"],
            "target_visible_now": target_visible_now,
            "target_co_located": target_co_located,
            "target_strength": combat_eval["target_strength"],
            "not_attacking_reasons": list(combat_eval["reasons"]),
            "recommended_support_objective": combat_eval["recommended_support_objective"],
            "equipment_advantaged": combat_eval["equipment_advantaged"],
            "equipment_advantage": combat_eval["equipment_advantage"],
            "required_hunt_equipment": combat_eval.get("equipment_advantage", {}).get("required_hunt_equipment"),
            "estimated_money_needed_for_advantage": combat_eval.get("equipment_advantage", {}).get("estimated_money_needed", 0),
            "preparation_basis": "target_equipment_advantage" if not combat_eval.get("equipment_advantaged", True) else None,
        }
        no_actionable_hunt_lead = bool(
            target_belief is not None
            and target_alive is not False
            and not target_visible_now
            and not target_co_located
            and not bool(target_belief.possible_locations)
            and not bool(target_belief.likely_routes)
        )
        hunt_cash_low = int(agent.get("money", 0) or 0) < _MIN_HUNT_INTEL_CASH_RESERVE
        needs_get_money_first = bool(no_actionable_hunt_lead and (hunt_cash_low or money_missing > 0))
        # Fix C: additional economy pressure when hunter is undergeared even with a weak lead
        _hunter_in_debt = int((agent.get("economic_state") or {}).get("debt_total") or 0) > 0
        _hunter_money_low = int(agent.get("money", 0) or 0) < HUNT_MIN_CASH_RESERVE
        _lead_confidence = float(target_belief.best_location_confidence or 0.0) if target_belief else 0.0
        _last_seen_turn = target_belief.last_seen_turn if target_belief else None
        _lead_age = (world_turn - int(_last_seen_turn)) if _last_seen_turn is not None else 999999
        _lead_is_weak_or_stale = (
            target_belief is None
            or not target_belief.possible_locations
            or _lead_confidence < 0.65
            or _lead_age > 240
        )
        _hunt_not_ready = (
            bool(blockers)
            or not combat_eval["combat_ready"]
            or not combat_eval.get("equipment_advantaged", True)
        )
        needs_hunt_preparation = (
            not target_visible_now
            and not target_co_located
            and _hunt_not_ready
            and (_hunter_money_low or _hunter_in_debt or _lead_is_weak_or_stale)
        )

        if target_alive is False:
            if target_loc:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_CONFIRM_KILL,
                        source="global_goal",
                        urgency=0.85,
                        expected_value=1.0,
                        risk=0.1,
                        time_cost=0.2,
                        resource_cost=0.0,
                        confidence=0.9,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_CONFIRM_KILL)[1],
                        reasons=("Есть данные, что цель мертва — нужно подтвердить устранение на месте",),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_CONFIRM_KILL)[0],
                        metadata={
                            "is_blocking": False,
                            "hunt_stage": "confirm",
                            "target_id": target_id,
                            "target_location_id": target_loc,
                        },
                        target={"target_id": target_id, "location_id": target_loc} if target_id else None,
                    ),
                )
            else:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_GATHER_INTEL,
                        source="global_goal",
                        urgency=0.78,
                        expected_value=0.78,
                        risk=0.15,
                        time_cost=0.35,
                        resource_cost=0.05,
                        confidence=0.72,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_GATHER_INTEL)[1],
                        reasons=("Цель вероятно мертва, но место тела неизвестно — собираю свидетельства",),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_GATHER_INTEL)[0],
                        metadata={"is_blocking": False, "hunt_stage": "gather_intel", "target_id": target_id},
                    ),
                )
        elif target_co_located or target_visible_now:
            if combat_eval["recommended_support_objective"] == OBJECTIVE_GET_MONEY_FOR_RESUPPLY:
                _replace_or_append(
                    result,
                    Objective(
                        key=OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
                        source="global_goal",
                        urgency=0.91,
                        expected_value=0.86,
                        risk=0.26,
                        time_cost=0.42,
                        resource_cost=0.18,
                        confidence=0.82,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_GET_MONEY_FOR_RESUPPLY)[1],
                        reasons=("Нужна подготовка к бою перед атакой видимой цели",) + tuple(prepare_reasons),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_GET_MONEY_FOR_RESUPPLY)[0],
                        metadata={
                            **_combat_metadata,
                            "support_objective_for": "kill_stalker",
                            "hunt_stage": "prepare",
                            "blockers": blockers,
                        },
                    ),
                )
            if blockers:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_PREPARE_FOR_HUNT,
                        source="global_goal",
                        urgency=0.9,
                        expected_value=0.85,
                        risk=0.25,
                        time_cost=0.45,
                        resource_cost=0.25,
                        confidence=0.8,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_PREPARE_FOR_HUNT)[1],
                        reasons=tuple(prepare_reasons) + ("Цель рядом, но вступать в бой рано",),
                        source_refs=("global_goal:kill_stalker",),
                        metadata={
                            **_combat_metadata,
                            "blockers": blockers,
                            "hunt_stage": "prepare",
                            "support_objective_for": "kill_stalker",
                        },
                    ),
                )
            else:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_ENGAGE_TARGET,
                        source="global_goal",
                        urgency=0.95,
                        expected_value=1.0,
                        risk=0.75,
                        time_cost=0.2,
                        resource_cost=0.15,
                        confidence=0.8,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_ENGAGE_TARGET)[1],
                        reasons=("Цель обнаружена рядом, боеготовность достаточна — атакую",),
                        source_refs=("global_goal:kill_stalker",),
                        metadata={**_combat_metadata, "blockers": blockers, "hunt_stage": "engage"},
                        target={"target_id": target_id, "location_id": target_loc} if target_id else None,
                    ),
                )
        elif _target_recently_here and _combat_readiness_sufficient and not _has_survival_emergency:
            # Fix 3: Target was recently seen at current location — generate high-priority ENGAGE_TARGET
            # Score is high enough to override soft needs (thirst ~0.4-0.5) but below true emergencies
            _append_unique(
                result,
                Objective(
                    key=OBJECTIVE_ENGAGE_TARGET,
                    source="global_goal",
                    urgency=0.87,
                    expected_value=0.95,
                    risk=0.2,
                    time_cost=0.1,
                    resource_cost=0.1,
                    confidence=0.85,
                    goal_alignment=1.0,
                    memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_ENGAGE_TARGET)[1],
                    reasons=("Цель недавно замечена в текущей локации — атакую пока след не остыл",),
                    source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_ENGAGE_TARGET)[0],
                    metadata={
                        "is_blocking": False,
                        "hunt_stage": "engage",
                        "target_id": target_id,
                        "target_location_id": _current_agent_loc,
                        "recently_seen": True,
                    },
                    target={"target_id": target_id, "location_id": _current_agent_loc} if target_id else None,
                ),
            )
        elif target_loc:
            best_is_exhausted = target_loc in exhausted_locations
            route_track_location = (
                route_hypothesis.to_location_id
                if route_hypothesis
                and route_hypothesis.to_location_id
                and (
                    best_is_exhausted
                    or (float(target_belief.best_location_confidence) if target_belief else 0.0) <= 0.3
                )
                else target_loc
            )
            if not best_is_exhausted and route_track_location == target_loc:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_VERIFY_LEAD,
                        source="global_goal",
                        urgency=0.8,
                        expected_value=0.88,
                        risk=0.28,
                        time_cost=0.45,
                        resource_cost=0.08,
                        confidence=max(0.5, float(target_belief.best_location_confidence) if target_belief else 0.65),
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_VERIFY_LEAD)[1],
                        reasons=("Есть рабочая версия местоположения цели — нужно проверить зацепку",),
                        source_refs=(
                            ("global_goal:kill_stalker",)
                            + (tuple(target_belief.possible_locations[0].source_refs[:2]) if target_belief and target_belief.possible_locations else ())
                            + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_VERIFY_LEAD)[0][:1]
                        ),
                        metadata={
                            "is_blocking": False,
                            "hunt_stage": "verify",
                            "target_id": target_id,
                            "target_location_id": target_loc,
                            "target_location_confidence": float(target_belief.best_location_confidence) if target_belief else 0.65,
                        },
                        target={"target_id": target_id, "location_id": target_loc} if target_id else None,
                    ),
                )
            _append_unique(
                result,
                Objective(
                    key=OBJECTIVE_TRACK_TARGET,
                    source="global_goal",
                    urgency=0.75,
                    expected_value=0.85,
                    risk=0.35,
                    time_cost=0.5,
                    resource_cost=0.1,
                    confidence=max(0.45, float(target_belief.best_location_confidence) if target_belief else 0.6),
                    goal_alignment=1.0,
                    memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_TRACK_TARGET)[1],
                    reasons=(
                        "Есть лучшее непустое предположение о местоположении цели — начинаю преследование"
                        if not best_is_exhausted
                        else "Основная локация уже исчерпана — переключаюсь на следующую зацепку",
                    ),
                    source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_TRACK_TARGET)[0],
                    metadata={
                        "is_blocking": False,
                        "hunt_stage": "track",
                        "target_id": target_id,
                        "target_location_id": target_loc,
                        "target_location_confidence": float(target_belief.best_location_confidence) if target_belief else 0.6,
                        "exhausted_locations": sorted(exhausted_locations),
                    },
                    target={"target_id": target_id, "location_id": target_loc} if target_id else None,
                ),
            )
            if route_hypothesis and route_hypothesis.to_location_id:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_INTERCEPT_TARGET,
                        source="global_goal",
                        urgency=0.62,
                        expected_value=0.72,
                        risk=0.38,
                        time_cost=0.52,
                        resource_cost=0.1,
                        confidence=max(0.4, float(route_hypothesis.confidence)),
                        goal_alignment=0.9,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_INTERCEPT_TARGET)[1],
                        reasons=("Есть вероятный маршрут цели — можно попробовать перехват",),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_INTERCEPT_TARGET)[0],
                        metadata={
                            "is_blocking": False,
                            "hunt_stage": "intercept",
                            "target_id": target_id,
                            "target_location_id": route_hypothesis.to_location_id,
                            "route_from_id": route_hypothesis.from_location_id,
                        },
                        target={"target_id": target_id, "location_id": route_hypothesis.to_location_id} if target_id else None,
                    ),
                )
            if blockers:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_PREPARE_FOR_HUNT,
                        source="global_goal",
                        urgency=0.72,
                        expected_value=0.82,
                        risk=0.22,
                        time_cost=0.45,
                        resource_cost=0.25,
                        confidence=0.8,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_PREPARE_FOR_HUNT)[1],
                        reasons=tuple(prepare_reasons),
                        source_refs=("global_goal:kill_stalker",),
                        metadata={
                            **_combat_metadata,
                            "blockers": blockers,
                            "hunt_stage": "prepare",
                            "support_objective_for": "kill_stalker",
                        },
                    ),
                )
            # Fix C: inject economy pressure even when a weak/stale lead exists
            if needs_hunt_preparation:
                _prep_money_reasons = list(prepare_reasons)
                if _hunter_money_low:
                    _prep_money_reasons.append("Нужны деньги на подготовку к охоте")
                if _hunter_in_debt:
                    _prep_money_reasons.append("Нужно погасить долг")
                _estimated_money = combat_eval.get("estimated_money_needed_for_advantage", 0)
                _replace_or_append(
                    result,
                    Objective(
                        key=OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
                        source="global_goal",
                        urgency=0.87,
                        expected_value=0.86,
                        risk=0.24,
                        time_cost=0.42,
                        resource_cost=0.12,
                        confidence=0.82,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_GET_MONEY_FOR_RESUPPLY)[1],
                        reasons=tuple(_prep_money_reasons),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_GET_MONEY_FOR_RESUPPLY)[0],
                        metadata={
                            **_combat_metadata,
                            "support_objective_for": "kill_stalker",
                            "hunt_stage": "prepare",
                            "hunt_preparation_pressure": True,
                            "preparation_basis": "target_equipment_advantage",
                            "money": int(agent.get("money") or 0),
                            "debt_total": int((agent.get("economic_state") or {}).get("debt_total") or 0),
                            "combat_ready": combat_eval["combat_ready"],
                            "equipment_advantaged": combat_eval["equipment_advantaged"],
                            "estimated_money_needed": _estimated_money,
                            "not_attacking_reasons": list(combat_eval["reasons"]),
                            "target_location_confidence": _lead_confidence,
                            "target_last_seen_age": _lead_age,
                        },
                    ),
                )
        else:
            if no_actionable_hunt_lead and (blockers or needs_get_money_first):
                money_reasons = []
                if blockers:
                    money_reasons.extend(prepare_reasons)
                if hunt_cash_low:
                    money_reasons.append("Нужны деньги на покупку сведений и подготовку к охоте")
                _replace_or_append(
                    result,
                    Objective(
                        key=OBJECTIVE_GET_MONEY_FOR_RESUPPLY,
                        source="global_goal",
                        urgency=0.9 if blockers else 0.86,
                        expected_value=0.86,
                        risk=0.24,
                        time_cost=0.42,
                        resource_cost=0.12,
                        confidence=0.82,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_GET_MONEY_FOR_RESUPPLY)[1],
                        reasons=tuple(money_reasons),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_GET_MONEY_FOR_RESUPPLY)[0],
                        metadata={
                            **_combat_metadata,
                            "blockers": blockers,
                            "hunt_stage": "prepare",
                            "support_objective_for": "kill_stalker",
                            "money_missing": money_missing,
                            "hunt_cash_low": hunt_cash_low,
                        },
                    ),
                )
                if needs_get_money_first:
                    result[:] = [objective for objective in result if objective.key != OBJECTIVE_HUNT_TARGET]
            if not needs_get_money_first:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_GATHER_INTEL,
                        source="global_goal",
                        urgency=0.84,
                        expected_value=0.82,
                        risk=0.18,
                        time_cost=0.48,
                        resource_cost=0.08,
                        confidence=0.76,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_GATHER_INTEL)[1],
                        reasons=("Полезных зацепок нет — расширяю поиск и собираю новые разведданные",),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_GATHER_INTEL)[0],
                        metadata={"is_blocking": False, "hunt_stage": "gather_intel", "target_id": target_id},
                        target={"target_id": target_id} if target_id else None,
                    ),
                )
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_LOCATE_TARGET,
                        source="global_goal",
                        urgency=0.82,
                        expected_value=0.8,
                        risk=0.2,
                        time_cost=0.55,
                        resource_cost=0.1,
                        confidence=0.75,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_LOCATE_TARGET)[1],
                        reasons=("Местоположение цели неизвестно — расширяю поиск",),
                        source_refs=("global_goal:kill_stalker",) + _objective_memory_refs_and_confidence(ctx, OBJECTIVE_LOCATE_TARGET)[0],
                        metadata={"is_blocking": False, "hunt_stage": "locate", "target_id": target_id},
                        target={"target_id": target_id} if target_id else None,
                    ),
                )
            if blockers:
                _append_unique(
                    result,
                    Objective(
                        key=OBJECTIVE_PREPARE_FOR_HUNT,
                        source="global_goal",
                        urgency=0.78 if no_actionable_hunt_lead else 0.70,
                        expected_value=0.80,
                        risk=0.20,
                        time_cost=0.45,
                        resource_cost=0.25,
                        confidence=0.8,
                        goal_alignment=1.0,
                        memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_PREPARE_FOR_HUNT)[1],
                        reasons=tuple(prepare_reasons),
                        source_refs=("global_goal:kill_stalker",),
                        metadata={
                            **_combat_metadata,
                            "blockers": blockers,
                            "hunt_stage": "prepare",
                            "support_objective_for": "kill_stalker",
                        },
                    ),
                )

        # Fix E: Anti-loop policy — suppress soft restore/rest for kill_stalker hunters
        # when they need to earn/prepare and survival is not critical.
        # Critical immediate needs (urgency >= 0.8) are always kept.
        if needs_hunt_preparation and not _has_survival_emergency:
            _soft_suppress_keys = {OBJECTIVE_RESTORE_FOOD, OBJECTIVE_RESTORE_WATER, OBJECTIVE_REST}
            result[:] = [
                obj for obj in result
                if obj.key not in _soft_suppress_keys or float(obj.urgency) >= 0.8
            ]

    economic_state = agent.get("economic_state") if isinstance(agent.get("economic_state"), dict) else {}
    debt_total = int(economic_state.get("debt_total") or 0)
    debt_creditors = [str(c) for c in (economic_state.get("creditors") or []) if str(c)]
    next_due_turn_min = economic_state.get("next_due_turn_min")
    world_turn_int = int(ctx.world_turn)

    if debt_total >= DEBT_ESCAPE_THRESHOLD:
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_LEAVE_ZONE,
                source="debt_escape",
                urgency=0.95,
                expected_value=0.9,
                risk=0.15,
                time_cost=0.35,
                resource_cost=0.05,
                confidence=0.95,
                goal_alignment=0.95,
                memory_confidence=0.8,
                reasons=("Долг превысил порог побега",),
                source_refs=("debt_escape_threshold",),
                metadata={"is_blocking": True, "reason": "debt_escape_threshold", "debt_total": debt_total},
            ),
        )

    if debt_total > 0 and debt_creditors:
        reserve = DEBT_REPAYMENT_KEEP_SURVIVAL_RESERVE if any(
            _is_critical_need(immediate.key, float(immediate.urgency))
            for immediate in need_result.immediate_needs
        ) else DEBT_REPAYMENT_KEEP_TRAVEL_RESERVE
        money = int(agent.get("money") or 0)
        surplus = max(0, money - reserve)
        near_due = isinstance(next_due_turn_min, (int, float)) and int(next_due_turn_min) - world_turn_int <= 180
        if surplus >= 10 or near_due:
            urgency = 0.58
            if near_due:
                urgency = max(urgency, 0.72)
            if debt_total >= 2000:
                urgency = max(urgency, 0.8)
            _append_unique(
                result,
                Objective(
                    key=OBJECTIVE_REPAY_DEBT,
                    source="debt_account",
                    urgency=_clamp01(urgency),
                    expected_value=_clamp01(0.45 + min(0.35, debt_total / 4000.0)),
                    risk=0.05,
                    time_cost=0.2 if debt_creditors else 0.45,
                    resource_cost=0.1,
                    confidence=0.85,
                    goal_alignment=0.85,
                    memory_confidence=0.8,
                    reasons=("Нужно сократить долг до следующего роста",),
                    source_refs=("debt_repayment",),
                    metadata={"is_blocking": False, "debt_total": debt_total, "creditors": debt_creditors},
                ),
            )

    if ctx.active_plan_summary:
        continue_refs, continue_memory_conf = _objective_memory_refs_and_confidence(ctx, OBJECTIVE_CONTINUE_CURRENT_PLAN)
        remaining_value = _clamp01(float(ctx.active_plan_summary.get("remaining_value", 0.6)))
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_CONTINUE_CURRENT_PLAN,
                source="active_plan",
                urgency=_clamp01(float(ctx.active_plan_summary.get("urgency", 0.5))),
                expected_value=remaining_value,
                risk=_clamp01(float(ctx.active_plan_summary.get("risk", 0.2))),
                time_cost=_clamp01(float(ctx.active_plan_summary.get("remaining_time", 0.25))),
                resource_cost=_clamp01(float(ctx.active_plan_summary.get("resource_cost", 0.0))),
                confidence=_clamp01(float(ctx.active_plan_summary.get("confidence", 0.7))),
                goal_alignment=_clamp01(float(ctx.active_plan_summary.get("goal_alignment", 0.8))),
                memory_confidence=continue_memory_conf,
                reasons=("Текущий план ещё актуален",),
                source_refs=("active_plan",) + continue_refs,
                metadata={"is_blocking": False},
            ),
        )

    if not result:
        idle_refs, idle_memory_conf = _objective_memory_refs_and_confidence(ctx, OBJECTIVE_IDLE)
        result.append(
            Objective(
                key=OBJECTIVE_IDLE,
                source="global_goal",
                urgency=0.1,
                expected_value=0.2,
                risk=0.0,
                time_cost=0.0,
                resource_cost=0.0,
                confidence=1.0,
                goal_alignment=0.2,
                memory_confidence=idle_memory_conf,
                reasons=("Нет более приоритетной цели",),
                source_refs=("fallback",) + idle_refs,
                metadata={"is_blocking": False},
            )
        )

    return result
