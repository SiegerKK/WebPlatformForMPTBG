"""needs — evaluate NPC drives and return a NeedScores object.

``evaluate_needs(ctx, state)`` is the Phase 2 entry point.
All formulas are documented in ``docs/npc_decision_architecture_v2_refactor_spec_addendum.md``
section 4 and reproduced below for reference.

Wealth gate (preserved from original architecture):
    - while wealth < material_threshold  → material drives are amplified
    - while wealth >= material_threshold → goal-directed drives are amplified

The wealth gate is NOT an absolute blocker: even below the threshold, goal
drives get a floor value so that NPC behaviour never fully stalls.
"""
from __future__ import annotations

from typing import Any

from .constants import EMISSION_DANGEROUS_TERRAIN, DESIRED_AMMO_COUNT
from .models.agent_context import AgentContext
from .immediate_needs import evaluate_immediate_needs
from .item_needs import evaluate_item_needs
from .liquidity import find_liquidity_options
from .models.need_evaluation import NeedEvaluationResult
from .models.need_scores import NeedScores

# ── Constants ─────────────────────────────────────────────────────────────────
_HP_SURVIVE_NOW_THRESHOLD = 10       # hp at or below this → survive_now = 1.0
_HP_SURVIVE_NOW_UPPER = 30           # hp above this → survive_now = 0.0
_HP_HEAL_SELF_THRESHOLD = 20         # hp at or below this → heal_self = 1.0
_HP_HEAL_SELF_UPPER = 50             # hp above this → heal_self = 0.0

_SLEEP_HIGH = 75                     # sleepiness threshold from tick_rules

_DESIRED_AMMO_RESERVE = 20           # kept for backwards-compat imports; see DESIRED_AMMO_COUNT

_GET_RICH_WEIGHT = 0.70              # weight for the get_rich material drive formula

# ── Public constants (importable for tests and other modules) ─────────────────
GET_RICH_WEIGHT = _GET_RICH_WEIGHT   # public alias


def evaluate_need_result(ctx: AgentContext, state: dict[str, Any]) -> NeedEvaluationResult:
    """Compute NeedScores together with PR2 immediate/item need structures."""
    agent = ctx.self_state
    hp: int = agent.get("hp", 100)
    hunger: int = agent.get("hunger", 0)
    thirst: int = agent.get("thirst", 0)
    sleepiness: int = agent.get("sleepiness", 0)
    # Use only liquid wealth (money + inventory) for goal drives — equipment
    # value is deliberately excluded so that owning a gun or armour does NOT
    # count as accumulated wealth for the get_rich / hunt / unravel drives.
    wealth: int = _agent_liquid_wealth(agent)
    material_threshold: int = agent.get("material_threshold", 3000)
    global_goal: str = agent.get("global_goal", "get_rich")
    kill_target_id: str | None = agent.get("kill_target_id")

    immediate_needs = evaluate_immediate_needs(ctx, state)
    item_needs = evaluate_item_needs(ctx, state)

    # ── Survival ──────────────────────────────────────────────────────────────
    survive_now = _score_survive_now(hp)
    heal_self = max(_score_heal_self(hp), _immediate_urgency(immediate_needs, "heal_now"))
    eat = max(_clamp(hunger / 100.0), _immediate_urgency(immediate_needs, "eat_now"))
    drink = max(_clamp(thirst / 100.0), _immediate_urgency(immediate_needs, "drink_now"))
    sleep = _clamp(sleepiness / 100.0)
    reload_or_rearm = _item_need_max_urgency(item_needs)

    # ── Environmental ─────────────────────────────────────────────────────────
    avoid_emission = _score_avoid_emission(ctx)

    # ── Wealth gate factor ────────────────────────────────────────────────────
    wealth_ratio = min(1.0, wealth / max(1, material_threshold))

    # ── Goal-directed ─────────────────────────────────────────────────────────
    get_rich = _clamp((1.0 - wealth_ratio) * _GET_RICH_WEIGHT)
    hunt_target = _score_hunt_target(agent, kill_target_id, wealth_ratio)
    unravel = _score_unravel(agent, global_goal, wealth_ratio)
    leave_zone = _score_leave_zone(agent)

    # ── Equipment gate: get_rich is suppressed while resupply is needed ────────
    get_rich = get_rich * (1.0 - reload_or_rearm)

    # ── Multiplicative suppression: risky drives dampened by survival pressure ──
    _survival_pressure = max(survive_now, heal_self * 0.5)
    if _survival_pressure > 0:
        get_rich = get_rich * max(0.0, 1.0 - _survival_pressure)
        hunt_target = hunt_target * max(0.0, 1.0 - _survival_pressure)
        unravel = unravel * max(0.0, 1.0 - _survival_pressure)

    # ── Completed goal: zero goal-driven drives, boost leave_zone (Fix 1) ────
    if agent.get("global_goal_achieved"):
        hunt_target = 0.0
        unravel = 0.0
        if not agent.get("has_left_zone"):
            leave_zone = max(leave_zone, 0.8)

    # ── Economic ─────────────────────────────────────────────────────────────
    trade = _score_trade(agent, ctx)

    # ── Social (Phase 6+ — zeroed for now) ───────────────────────────────────
    negotiate = 0.0
    maintain_group = 0.0
    help_ally = 0.0
    join_group = 0.0

    scores = NeedScores(
        survive_now=survive_now,
        heal_self=heal_self,
        eat=eat,
        drink=drink,
        sleep=sleep,
        reload_or_rearm=reload_or_rearm,
        avoid_emission=avoid_emission,
        get_rich=get_rich,
        hunt_target=hunt_target,
        unravel_zone_mystery=unravel,
        leave_zone=leave_zone,
        trade=trade,
        negotiate=negotiate,
        maintain_group=maintain_group,
        help_ally=help_ally,
        join_group=join_group,
    )

    liquidity_options = find_liquidity_options(
        agent=agent,
        immediate_needs=immediate_needs,
        item_needs=item_needs,
    )
    safe_count = sum(1 for o in liquidity_options if o.safety == "safe")
    risky_count = sum(1 for o in liquidity_options if o.safety == "risky")
    emergency_count = sum(1 for o in liquidity_options if o.safety == "emergency_only")

    # Compute dominant item need affordability for trace enrichment.
    from .item_needs import choose_dominant_item_need
    from .liquidity import evaluate_affordability
    dominant = choose_dominant_item_need(list(item_needs))
    money = int(agent.get("money", 0))
    if dominant and dominant.expected_min_price is not None:
        required_price = dominant.expected_min_price
        money_missing = max(0, required_price - money)
        can_buy_now = money >= required_price
        if can_buy_now:
            planner_allowed_decision = "affordable"
        elif safe_count > 0:
            planner_allowed_decision = "sell_safe_then_buy"
        elif emergency_count > 0:
            planner_allowed_decision = "sell_emergency_then_buy"
        else:
            planner_allowed_decision = "fallback_get_money"
    else:
        required_price = None
        money_missing = 0
        can_buy_now = None
        planner_allowed_decision = "no_dominant_need"

    liquidity_summary = {
        "safe_sale_options": safe_count,
        "risky_sale_options": risky_count,
        "emergency_sale_options": emergency_count,
        "risky_liquidity_available": risky_count > 0,
        "can_buy_now": can_buy_now,
        "required_price": required_price,
        "money_missing": money_missing,
        "planner_allowed_decision": planner_allowed_decision,
        # Alias kept for compatibility with older trace consumers expecting "decision".
        "decision": planner_allowed_decision,
    }

    return NeedEvaluationResult(
        scores=scores,
        immediate_needs=tuple(immediate_needs),
        item_needs=tuple(item_needs),
        liquidity_summary=liquidity_summary,
        combat_readiness={
            "weapon_missing": next(
                (n.missing_count for n in item_needs if n.key == "weapon"), 0
            ),
            "ammo_missing": next(
                (n.missing_count for n in item_needs if n.key == "ammo"), 0
            ),
            "medicine_missing": next(
                (n.missing_count for n in item_needs if n.key == "medicine"), 0
            ),
        },
    )


def evaluate_needs(ctx: AgentContext, state: dict[str, Any]) -> NeedScores:
    """Backward-compatible wrapper returning only NeedScores."""
    return evaluate_need_result(ctx, state).scores


def _immediate_urgency(immediate_needs: list[Any], key: str) -> float:
    return max((float(n.urgency) for n in immediate_needs if n.key == key), default=0.0)


def _item_need_max_urgency(item_needs: list[Any]) -> float:
    return max((float(n.urgency) for n in item_needs if n.key != "upgrade"), default=0.0)


# ── Score helpers ─────────────────────────────────────────────────────────────

def _score_survive_now(hp: int) -> float:
    """1.0 when hp ≤ 10, linearly falls to 0 at hp = 30."""
    if hp <= _HP_SURVIVE_NOW_THRESHOLD:
        return 1.0
    return _clamp((_HP_SURVIVE_NOW_UPPER - hp) / (_HP_SURVIVE_NOW_UPPER - _HP_SURVIVE_NOW_THRESHOLD))


def _score_heal_self(hp: int) -> float:
    """1.0 when hp ≤ 20, linearly falls to 0 at hp = 50."""
    if hp <= _HP_HEAL_SELF_THRESHOLD:
        return 1.0
    return _clamp((_HP_HEAL_SELF_UPPER - hp) / (_HP_HEAL_SELF_UPPER - _HP_HEAL_SELF_THRESHOLD))


def _desired_supply_count(risk_tolerance: float, min_count: int, max_count: int) -> int:
    """Desired inventory count for a supply category based on risk tolerance.

    More risk-averse agents (low ``risk_tolerance``) want larger stocks; agents
    with a higher risk tolerance can get by with the minimum.

    Examples (min=1, max=3):
        risk_tolerance=0.0 → 3
        risk_tolerance=0.5 → 2
        risk_tolerance=1.0 → 1
    """
    return min_count + round((1.0 - risk_tolerance) * (max_count - min_count))


def _score_reload_or_rearm(agent: dict[str, Any]) -> float:
    """Pressure to resupply consumables, equipment, and upgrades.

    Priority order (mirrored in planner._plan_resupply):
      1. Food / drink stock below desired level   → 0.55
      2. No armor                                  → 0.70
      3. No weapon                                 → 0.65
      4. Ammo count below DESIRED_AMMO_COUNT       → up to 0.60 (scaled)
      5. Medicine stock below desired level        → 0.45
      6. Upgrade available for weapon or armor     → 0.25

    Returns the maximum urgency score across all detected gaps.
    Money is NOT checked here — the NPC should always want proper equipment
    regardless of wealth (the material_threshold gate is only for get_rich).
    """
    from app.games.zone_stalkers.balance.items import (
        AMMO_FOR_WEAPON, FOOD_ITEM_TYPES, DRINK_ITEM_TYPES, HEAL_ITEM_TYPES,
        WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES,
    )

    equipment = agent.get("equipment", {})
    inventory = agent.get("inventory", [])
    risk_tolerance = float(agent.get("risk_tolerance", 0.5))

    # Desired supply counts based on risk tolerance
    desired_food = _desired_supply_count(risk_tolerance, 1, 3)
    desired_drink = _desired_supply_count(risk_tolerance, 1, 3)
    desired_medicine = _desired_supply_count(risk_tolerance, 2, 4)

    # Count current inventory by category
    food_count = sum(1 for i in inventory if i.get("type") in FOOD_ITEM_TYPES)
    drink_count = sum(1 for i in inventory if i.get("type") in DRINK_ITEM_TYPES)
    medicine_count = sum(1 for i in inventory if i.get("type") in HEAL_ITEM_TYPES)

    has_weapon = equipment.get("weapon") is not None
    has_armor = equipment.get("armor") is not None

    score = 0.0

    # 1. Food / drink stock
    if food_count < desired_food or drink_count < desired_drink:
        score = max(score, 0.55)

    # 2. Armor
    if not has_armor:
        score = max(score, 0.70)

    # 3. Weapon
    if not has_weapon:
        score = max(score, 0.65)

    # 4. Ammo (count-based: need DESIRED_AMMO_COUNT items)
    if has_weapon:
        weapon_type: str | None = None
        w = equipment.get("weapon")
        if w and isinstance(w, dict):
            weapon_type = w.get("type")
        required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
        if required_ammo:
            ammo_count = sum(1 for i in inventory if i.get("type") == required_ammo)
            if ammo_count < DESIRED_AMMO_COUNT:
                # Score decreases as ammo count approaches the target
                ammo_gap_score = 0.60 * (1.0 - ammo_count / DESIRED_AMMO_COUNT)
                score = max(score, ammo_gap_score)

    # 5. Medicine stock
    if medicine_count < desired_medicine:
        score = max(score, 0.45)

    return score


def _score_upgrade_opportunity(
    equipment: dict[str, Any],
    agent_money: int,
    risk_tolerance: float,
) -> float:
    """Return 0.25 if an affordable upgrade exists for weapon or armor, else 0.0.

    Checks whether any item in the same slot category offers a closer
    ``risk_tolerance`` match to the agent AND has a higher base value (higher
    tier), AND is affordable at trader price (base × 1.5).
    """
    from app.games.zone_stalkers.balance.items import (
        ITEM_TYPES, WEAPON_ITEM_TYPES, ARMOR_ITEM_TYPES,
    )
    for slot, item_types in [("weapon", WEAPON_ITEM_TYPES), ("armor", ARMOR_ITEM_TYPES)]:
        current = equipment.get(slot)
        if not isinstance(current, dict):
            continue
        current_type = current.get("type")
        current_info = ITEM_TYPES.get(current_type or "", {})
        current_rt = float(current_info.get("risk_tolerance", 0.5))
        current_dist = abs(current_rt - risk_tolerance)
        current_value = int(current_info.get("value", 0))
        for k in item_types:
            if k == current_type:
                continue
            info = ITEM_TYPES.get(k)
            if info is None:
                continue
            dist = abs(float(info.get("risk_tolerance", 0.5)) - risk_tolerance)
            value = int(info.get("value", 0))
            if dist > current_dist:
                continue
            if value <= current_value:
                continue
            buy_price = int(value * 1.5)
            if agent_money < buy_price:
                continue
            return 0.25
    return 0.0


def _score_avoid_emission(ctx: AgentContext) -> float:
    """High when emission is active/imminent and agent is on dangerous terrain."""
    world_ctx = ctx.world_context
    emission_active: bool = world_ctx.get("emission_active", False)
    terrain: str = ctx.location_state.get("terrain_type", "")
    on_dangerous = terrain in EMISSION_DANGEROUS_TERRAIN

    if emission_active and on_dangerous:
        return 1.0

    # Check memory for imminent emission warning
    emission_warned = _is_emission_warned(ctx.self_state, world_ctx.get("world_turn", 0))
    if emission_warned and on_dangerous:
        return 0.9
    if emission_active or emission_warned:
        return 0.3
    return 0.0


def _score_hunt_target(
    agent: dict[str, Any],
    kill_target_id: str | None,
    wealth_ratio: float,
) -> float:
    """Drive to pursue the kill_stalker global goal."""
    if not kill_target_id:
        return 0.0
    if agent.get("global_goal") != "kill_stalker":
        return 0.0
    base = 0.8
    # Wealth gate: hunting is suppressed below threshold but never fully blocked
    return _clamp(base * max(0.25, wealth_ratio))


def _score_unravel(
    agent: dict[str, Any],
    global_goal: str,
    wealth_ratio: float,
) -> float:
    """Drive to pursue the unravel_zone_mystery global goal."""
    if global_goal != "unravel_zone_mystery":
        return 0.0
    base = 0.75
    return _clamp(base * max(0.40, wealth_ratio))


def _score_leave_zone(agent: dict[str, Any]) -> float:
    """Maximum pressure when global goal is achieved and exit is needed."""
    if agent.get("global_goal_achieved") and not agent.get("has_left_zone"):
        return 1.0
    return 0.0


def _score_trade(agent: dict[str, Any], ctx: AgentContext) -> float:
    """Drive to trade — sell artifacts or buy at a trader.

    P3 fix: ctx.visible_entities now includes traders from state["traders"],
    so trader_colocated correctly becomes True when a trader is co-located.
    """
    from app.games.zone_stalkers.balance.artifacts import ARTIFACT_TYPES
    artifact_types = frozenset(ARTIFACT_TYPES.keys())
    inventory = agent.get("inventory", [])
    has_artifacts = any(i.get("type") in artifact_types for i in inventory)

    # Only relevant when a trader is co-located
    trader_colocated = any(e.get("is_trader") for e in ctx.visible_entities)
    if has_artifacts and trader_colocated:
        return 0.7
    return 0.0


# ── Utility ────────────────────────────────────────────────────────────────────

def _clamp(value: float) -> float:
    """Clamp a float to [0.0, 1.0]."""
    return max(0.0, min(1.0, value))


def agent_wealth(agent: dict[str, Any]) -> int:
    """Sum of money + inventory values + equipped item values (public API).

    Used by external callers (tick_rules, groups, debug) that need the full
    picture of agent wealth including gear on the agent's back.
    ``evaluate_needs`` intentionally uses ``_agent_liquid_wealth`` instead so
    that owning equipment does not suppress the get_rich drive.
    """
    money: int = agent.get("money", 0)
    inv_value = sum(i.get("value", 0) for i in agent.get("inventory", []))
    eq_value = sum(
        item.get("value", 0)
        for item in agent.get("equipment", {}).values()
        if isinstance(item, dict)
    )
    return money + inv_value + eq_value


def _agent_liquid_wealth(agent: dict[str, Any]) -> int:
    """Liquid wealth used for goal-drive scoring: money + inventory only.

    Equipment value is excluded because gear is a *survival tool*, not
    accumulated wealth.  An NPC with a pistol but no money should still
    feel a strong drive to earn more — the pistol does not make them rich.
    """
    money: int = agent.get("money", 0)
    inv_value = sum(i.get("value", 0) for i in agent.get("inventory", []))
    return money + inv_value


# ── Private alias kept for backward-compatible external callers ───────────────
_agent_wealth = agent_wealth


def _is_emission_warned(agent: dict[str, Any], current_turn: int) -> bool:
    """Check if the agent has a live (not yet superseded) emission_imminent memory.

    Scans from the most recent memory entry backward for speed — once both
    relevant events are found the scan terminates early.
    """
    last_ended_turn = 0
    last_imminent_turn = 0
    memory = agent.get("memory", [])
    # Scan in reverse (most recent first) and stop once both are found
    for mem in reversed(memory):
        if mem.get("type") != "observation":
            continue
        kind = mem.get("effects", {}).get("action_kind")
        turn = mem.get("world_turn", 0)
        if kind == "emission_ended" and last_ended_turn == 0:
            last_ended_turn = turn
        elif kind == "emission_imminent" and last_imminent_turn == 0:
            last_imminent_turn = turn
        if last_ended_turn > 0 and last_imminent_turn > 0:
            break
    return last_imminent_turn > last_ended_turn
