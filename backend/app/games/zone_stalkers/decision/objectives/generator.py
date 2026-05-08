from __future__ import annotations

from typing import Any

from app.games.zone_stalkers.decision.models.objective import Objective, ObjectiveGenerationContext


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
OBJECTIVE_IDLE = "IDLE"

OBJECTIVE_CONTINUE_CURRENT_PLAN = "CONTINUE_CURRENT_PLAN"

# Reserved hunt decomposition keys (pre-PR5 prerequisite).
OBJECTIVE_LOCATE_TARGET = "LOCATE_TARGET"
OBJECTIVE_PREPARE_FOR_HUNT = "PREPARE_FOR_HUNT"
OBJECTIVE_TRACK_TARGET = "TRACK_TARGET"
OBJECTIVE_INTERCEPT_TARGET = "INTERCEPT_TARGET"
OBJECTIVE_AMBUSH_TARGET = "AMBUSH_TARGET"
OBJECTIVE_ENGAGE_TARGET = "ENGAGE_TARGET"
OBJECTIVE_CONFIRM_KILL = "CONFIRM_KILL"
OBJECTIVE_RETREAT_FROM_TARGET = "RETREAT_FROM_TARGET"
OBJECTIVE_RECOVER_AFTER_COMBAT = "RECOVER_AFTER_COMBAT"

HUNT_OBJECTIVE_KEYS: tuple[str, ...] = (
    OBJECTIVE_LOCATE_TARGET,
    OBJECTIVE_PREPARE_FOR_HUNT,
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


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _memory_confidence(ctx: ObjectiveGenerationContext) -> float:
    relevant = ctx.belief_state.relevant_memories
    if not relevant:
        return 0.5
    total = sum(float(mem.get("confidence", 0.0)) for mem in relevant)
    return _clamp01(total / max(1, len(relevant)))


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


def generate_objectives(ctx: ObjectiveGenerationContext) -> list[Objective]:
    """Generate objective candidates from need_result, environment, goals and active plan."""
    result: list[Objective] = []
    agent = ctx.personality
    need_result = ctx.need_result
    memory_conf = _memory_confidence(ctx)

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
                memory_confidence=memory_conf,
                reasons=tuple(reasons),
                source_refs=(f"immediate:{immediate.key}",),
                metadata=metadata,
            ),
        )

    sleep_score = float(need_result.scores.sleep)
    if sleep_score > 0.05:
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_REST,
                source="immediate_need",
                urgency=_clamp01(sleep_score),
                expected_value=0.75,
                risk=0.05,
                time_cost=0.4,
                resource_cost=0.0,
                confidence=0.9,
                goal_alignment=0.7,
                memory_confidence=memory_conf,
                reasons=("Усталость растёт",),
                source_refs=("need:sleep",),
                metadata={"is_blocking": False},
            ),
        )

    dominant_item_urgency = 0.0
    for item_need in need_result.item_needs:
        objective_key = ITEM_NEED_TO_OBJECTIVE.get(item_need.key)
        if objective_key is None or item_need.urgency <= 0:
            continue
        dominant_item_urgency = max(dominant_item_urgency, float(item_need.urgency))

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
                memory_confidence=memory_conf,
                reasons=(item_need.reason,) if item_need.reason else (),
                source_refs=(f"item_need:{item_need.key}",),
                metadata={"is_blocking": False, "item_need_key": item_need.key, "missing_count": item_need.missing_count},
            ),
        )

    liquidity = need_result.liquidity_summary or {}
    if dominant_item_urgency > 0 and int(liquidity.get("money_missing") or 0) > 0:
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
                memory_confidence=memory_conf,
                reasons=("Не хватает денег для обязательного пополнения",),
                source_refs=("liquidity:money_missing",),
                metadata={"is_blocking": False, "money_missing": int(liquidity.get("money_missing") or 0)},
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
                    memory_confidence=memory_conf,
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
                    memory_confidence=memory_conf,
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
                memory_confidence=memory_conf,
                reasons=("Критически низкий HP",),
                source_refs=("need:survive_now",),
                metadata={"is_blocking": True},
            ),
        )

    global_goal = str(agent.get("global_goal") or "get_rich")
    global_key = _global_goal_objective(global_goal)
    _append_unique(
        result,
        Objective(
            key=global_key,
            source="global_goal",
            urgency=max(0.1, float(getattr(need_result.scores, {
                "get_rich": "get_rich",
                "kill_stalker": "hunt_target",
                "unravel_zone_mystery": "unravel_zone_mystery",
                "leave_zone": "leave_zone",
            }.get(global_goal, "get_rich"), 0.1))),
            expected_value=0.7,
            risk=0.35,
            time_cost=0.6,
            resource_cost=0.2,
            confidence=0.7,
            goal_alignment=1.0,
            memory_confidence=memory_conf,
            reasons=(f"Глобальная цель: {global_goal}",),
            source_refs=(f"global_goal:{global_goal}",),
            metadata={"is_blocking": False},
        ),
    )

    if global_goal == "get_rich":
        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_SELL_ARTIFACTS,
                source="global_goal",
                urgency=max(0.1, float(need_result.scores.trade)),
                expected_value=0.75,
                risk=0.2,
                time_cost=0.4,
                resource_cost=0.0,
                confidence=0.75,
                goal_alignment=0.95,
                memory_confidence=memory_conf,
                reasons=("Продажа артефактов ускоряет накопление денег",),
                source_refs=("global_goal:get_rich",),
                metadata={"is_blocking": False},
            ),
        )

    if global_goal == "kill_stalker":
        readiness = need_result.combat_readiness or {}
        weapon_missing = int(readiness.get("weapon_missing") or 0)
        ammo_missing = int(readiness.get("ammo_missing") or 0)
        hp = int(agent.get("hp") or 100)

        blockers: list[dict[str, Any]] = []
        reasons: list[str] = ["Охота требует предварительной готовности"]
        if weapon_missing > 0:
            blockers.append({"key": "no_weapon", "reason": "Нет оружия", "blocked": True, "penalty": 0.55})
            reasons.append("Нет оружия")
        if ammo_missing > 0:
            blockers.append({"key": "low_ammo", "reason": "Недостаточно патронов", "blocked": True, "penalty": 0.45})
            reasons.append("Недостаточно патронов")
        if hp <= 35:
            blockers.append({"key": "hp_low", "reason": "HP слишком низкий", "blocked": True, "penalty": 0.5})
            reasons.append("Нужно восстановить HP")

        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_PREPARE_FOR_HUNT,
                source="global_goal",
                urgency=0.65 + (0.2 if blockers else 0.0),
                expected_value=0.85,
                risk=0.25,
                time_cost=0.45,
                resource_cost=0.25,
                confidence=0.8,
                goal_alignment=1.0,
                memory_confidence=memory_conf,
                reasons=tuple(reasons),
                source_refs=("global_goal:kill_stalker",),
                metadata={"is_blocking": False, "blockers": blockers, "hunt_stage": "prepare"},
            ),
        )

        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_LOCATE_TARGET,
                source="global_goal",
                urgency=0.55,
                expected_value=0.75,
                risk=0.2,
                time_cost=0.5,
                resource_cost=0.1,
                confidence=0.7,
                goal_alignment=1.0,
                memory_confidence=memory_conf,
                reasons=("Нужно определить местоположение цели",),
                source_refs=("global_goal:kill_stalker",),
                metadata={"is_blocking": False, "hunt_stage": "locate"},
            ),
        )

        _append_unique(
            result,
            Objective(
                key=OBJECTIVE_ENGAGE_TARGET,
                source="global_goal",
                urgency=0.5,
                expected_value=0.9,
                risk=0.8,
                time_cost=0.35,
                resource_cost=0.2,
                confidence=0.6,
                goal_alignment=1.0,
                memory_confidence=memory_conf,
                reasons=("Прямая атака возможна только при боевой готовности",),
                source_refs=("global_goal:kill_stalker",),
                metadata={"is_blocking": False, "blockers": blockers, "hunt_stage": "engage"},
            ),
        )

    if ctx.active_plan_summary:
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
                memory_confidence=memory_conf,
                reasons=("Текущий план ещё актуален",),
                source_refs=("active_plan",),
                metadata={"is_blocking": False},
            ),
        )

    if not result:
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
                memory_confidence=memory_conf,
                reasons=("Нет более приоритетной цели",),
                source_refs=("fallback",),
                metadata={"is_blocking": False},
            )
        )

    return result
