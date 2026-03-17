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

from .models.agent_context import AgentContext
from .models.need_scores import NeedScores

# ── Constants ─────────────────────────────────────────────────────────────────
_HP_SURVIVE_NOW_THRESHOLD = 10       # hp at or below this → survive_now = 1.0
_HP_SURVIVE_NOW_UPPER = 30           # hp above this → survive_now = 0.0
_HP_HEAL_SELF_THRESHOLD = 20         # hp at or below this → heal_self = 1.0
_HP_HEAL_SELF_UPPER = 50             # hp above this → heal_self = 0.0

_HUNGER_CRITICAL = 80                # original critical threshold from tick_rules
_THIRST_CRITICAL = 80
_HUNGER_EMERGENCY = 70               # maps to high urgency
_THIRST_EMERGENCY = 70
_SLEEP_HIGH = 75                     # sleepiness threshold from tick_rules

_DESIRED_AMMO_RESERVE = 20           # target ammo count for full reload_or_rearm score

_GET_RICH_WEIGHT = 0.70              # weight for the get_rich material drive formula
_GOAL_MIN_FLOOR = 0.10               # minimum goal drive score even when wealth < threshold


def evaluate_needs(ctx: AgentContext, state: dict[str, Any]) -> NeedScores:
    """Compute NeedScores from the agent's current context.

    Parameters
    ----------
    ctx
        Pre-built AgentContext for this agent.
    state
        The full world state dict (read-only; used for relational lookups).

    Returns
    -------
    NeedScores
        All scores clamped to [0.0, 1.0].
    """
    agent = ctx.self_state
    hp: int = agent.get("hp", 100)
    hunger: int = agent.get("hunger", 0)
    thirst: int = agent.get("thirst", 0)
    sleepiness: int = agent.get("sleepiness", 0)
    wealth: int = _agent_wealth(agent)
    material_threshold: int = agent.get("material_threshold", 3000)
    global_goal: str = agent.get("global_goal", "get_rich")
    kill_target_id: str | None = agent.get("kill_target_id")

    # ── Survival ──────────────────────────────────────────────────────────────
    survive_now = _score_survive_now(hp)
    heal_self = _score_heal_self(hp)
    eat = _clamp(hunger / 100.0)
    drink = _clamp(thirst / 100.0)
    sleep = _clamp(sleepiness / 100.0)
    reload_or_rearm = _score_reload_or_rearm(agent)

    # ── Environmental ─────────────────────────────────────────────────────────
    avoid_emission = _score_avoid_emission(ctx)

    # ── Wealth gate factor ────────────────────────────────────────────────────
    wealth_ratio = min(1.0, wealth / max(1, material_threshold))

    # ── Goal-directed ─────────────────────────────────────────────────────────
    get_rich = _clamp((1.0 - wealth_ratio) * _GET_RICH_WEIGHT)
    hunt_target = _score_hunt_target(agent, kill_target_id, wealth_ratio)
    unravel = _score_unravel(agent, global_goal, wealth_ratio)
    leave_zone = _score_leave_zone(agent)

    # ── Economic ─────────────────────────────────────────────────────────────
    trade = _score_trade(agent, ctx)

    # ── Social (Phase 6+ — zeroed for now) ───────────────────────────────────
    negotiate = 0.0
    maintain_group = 0.0
    help_ally = 0.0
    join_group = 0.0

    return NeedScores(
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


def _score_reload_or_rearm(agent: dict[str, Any]) -> float:
    """Pressure to acquire weapon / armor / ammo."""
    equipment = agent.get("equipment", {})
    inventory = agent.get("inventory", [])

    has_weapon = equipment.get("weapon") is not None
    has_armor = equipment.get("armor") is not None

    if not has_weapon:
        return 1.0
    if not has_armor:
        return 0.7

    # Check ammo for equipped weapon
    from app.games.zone_stalkers.balance.items import AMMO_FOR_WEAPON
    weapon_type: str | None = None
    w = equipment.get("weapon")
    if w and isinstance(w, dict):
        weapon_type = w.get("type")
    required_ammo = AMMO_FOR_WEAPON.get(weapon_type) if weapon_type else None
    if required_ammo:
        ammo_count = sum(
            i.get("quantity", 1) for i in inventory if i.get("type") == required_ammo
        )
        has_ammo_score = min(1.0, ammo_count / _DESIRED_AMMO_RESERVE)
        if has_ammo_score < 0.5:
            return _clamp(1.0 - has_ammo_score)
    return 0.0


def _score_avoid_emission(ctx: AgentContext) -> float:
    """High when emission is active/imminent and agent is on dangerous terrain."""
    _EMISSION_DANGEROUS_TERRAIN = frozenset({
        "plain", "hills", "swamp", "field_camp", "slag_heaps", "bridge",
    })
    world_ctx = ctx.world_context
    emission_active: bool = world_ctx.get("emission_active", False)
    terrain: str = ctx.location_state.get("terrain_type", "")
    on_dangerous = terrain in _EMISSION_DANGEROUS_TERRAIN

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
    """Drive to trade — sell artifacts or buy at a trader."""
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


def _agent_wealth(agent: dict[str, Any]) -> int:
    """Sum of money + inventory values + equipped item values."""
    money: int = agent.get("money", 0)
    inv_value = sum(i.get("value", 0) for i in agent.get("inventory", []))
    eq_value = sum(
        item.get("value", 0)
        for item in agent.get("equipment", {}).values()
        if isinstance(item, dict)
    )
    return money + inv_value + eq_value


def _is_emission_warned(agent: dict[str, Any], current_turn: int) -> bool:
    """Check if the agent has a live (not yet superseded) emission_imminent memory."""
    last_ended_turn = 0
    last_imminent_turn = 0
    for mem in agent.get("memory", []):
        if mem.get("type") != "observation":
            continue
        kind = mem.get("effects", {}).get("action_kind")
        turn = mem.get("world_turn", 0)
        if kind == "emission_ended" and turn > last_ended_turn:
            last_ended_turn = turn
        elif kind == "emission_imminent" and turn > last_imminent_turn:
            last_imminent_turn = turn
    return last_imminent_turn > last_ended_turn
