"""intents — select the dominant intent from evaluated NeedScores.

``select_intent(ctx, needs, world_turn)`` returns the single Intent that the
agent should pursue this tick (Intent invariant 11.1 from the refactor spec).

Language note:
    All ``reason`` strings in this module are written in **Russian**.
    This is intentional — Zone Stalkers is a Russian-language game and all
    in-game memory/decision text is in Russian.  The ``reason`` field is
    displayed to players via the explain/debug interface and stored in
    ``agent._v2_context``.

Tie-break order (fixed priority — from the addendum §5.1):
    1.  survive_now
    2.  heal_self
    3.  avoid_emission
    4.  drink
    5.  eat
    6.  sleep
    7.  reload_or_rearm
    8.  maintain_group
    9.  help_ally
   10.  trade
   11.  get_rich
   12.  hunt_target
   13.  unravel_zone_mystery
   14.  leave_zone
   15.  negotiate
   16.  join_group
   17.  idle  (fallback)

Interrupt policy (addendum §6):
    Hard interrupts (always override current plan):
        - survive_now  ≥ 0.7
        - avoid_emission ≥ 0.8
        - heal_self ≥ 0.8  (if no heal item available anywhere)

    Soft interrupts (may trigger replanning):
        - any drive score jumps by > 0.3 in one tick

    No-interrupt threshold:
        - drive delta < 0.1  (cosmetic changes — never replanned)
"""
from __future__ import annotations

from typing import Any, Optional

from .constants import EMISSION_DANGEROUS_TERRAIN
from .models.agent_context import AgentContext
from .models.need_evaluation import NeedEvaluationResult
from .models.need_scores import NeedScores
from .models.intent import (
    Intent,
    INTENT_ESCAPE_DANGER,
    INTENT_FLEE_EMISSION,
    INTENT_WAIT_IN_SHELTER,
    INTENT_HEAL_SELF,
    INTENT_SEEK_FOOD,
    INTENT_SEEK_WATER,
    INTENT_REST,
    INTENT_RESUPPLY,
    INTENT_TRADE,
    INTENT_SELL_ARTIFACTS,
    INTENT_GET_RICH,
    INTENT_HUNT_TARGET,
    INTENT_SEARCH_INFORMATION,
    INTENT_LEAVE_ZONE,
    INTENT_NEGOTIATE,
    INTENT_ASSIST_ALLY,
    INTENT_FORM_GROUP,
    INTENT_MAINTAIN_GROUP,
    INTENT_IDLE,
)
from app.games.zone_stalkers.rules.tick_constants import (
    CRITICAL_HUNGER_THRESHOLD,
    CRITICAL_THIRST_THRESHOLD,
)

# Score threshold below which a drive is considered negligible
_NEGLIGIBLE_THRESHOLD = 0.05

# Hard-interrupt thresholds
_HARD_INTERRUPT_SURVIVE_NOW = 0.70
_HARD_INTERRUPT_EMISSION = 0.80
_HARD_INTERRUPT_HEAL = 0.80

# Soft consume thresholds to avoid non-critical seek_* -> wait loops.
_SOFT_CONSUME_FOOD_THRESHOLD = 50
_SOFT_CONSUME_DRINK_THRESHOLD = 40


# ── Priority-ordered mapping: drive name → (intent_kind, reason_template) ─────
#
# The list is ordered by tie-break priority (highest first).
# Each entry: (drive_attr, intent_kind, reason_template)
# NOTE: drink/eat templates below are non-critical baseline reasons.
# Critical hunger/thirst reasons are handled earlier via ImmediateNeed/hard-threshold logic.
# P5 fix: maintain_group → INTENT_MAINTAIN_GROUP (was erroneously INTENT_FOLLOW_GROUP_PLAN)
_PRIORITY_MAP: list[tuple[str, str, str]] = [
    ("survive_now",        INTENT_ESCAPE_DANGER,      "HP критически низкий"),
    ("heal_self",          INTENT_HEAL_SELF,           "Нужно лечение"),
    ("avoid_emission",     INTENT_FLEE_EMISSION,       "Угроза выброса"),
    ("drink",              INTENT_SEEK_WATER,          "Жажда растёт"),
    ("eat",                INTENT_SEEK_FOOD,           "Голод растёт"),
    ("sleep",              INTENT_REST,                "Сильная усталость"),
    ("reload_or_rearm",    INTENT_RESUPPLY,            "Не хватает снаряжения"),
    ("maintain_group",     INTENT_MAINTAIN_GROUP,      "Нужды группы"),
    ("help_ally",          INTENT_ASSIST_ALLY,         "Союзник в опасности"),
    ("trade",              INTENT_SELL_ARTIFACTS,      "Продать артефакты"),
    ("get_rich",           INTENT_GET_RICH,            "Накопление богатства"),
    ("hunt_target",        INTENT_HUNT_TARGET,         "Преследование цели"),
    ("unravel_zone_mystery", INTENT_SEARCH_INFORMATION, "Раскрыть тайну Зоны"),
    ("leave_zone",         INTENT_LEAVE_ZONE,          "Глобальная цель достигнута"),
    ("negotiate",          INTENT_NEGOTIATE,           "Социальная возможность"),
    ("join_group",         INTENT_FORM_GROUP,          "Возможность создать группу"),
]


def select_intent(
    ctx: AgentContext,
    needs: NeedScores,
    world_turn: int,
    need_result: NeedEvaluationResult | None = None,
) -> Intent:
    """Choose the dominant Intent for this tick.

    Parameters
    ----------
    ctx
        AgentContext for this agent.
    needs
        Evaluated NeedScores for this tick.
    world_turn
        Current world turn (stored in ``created_turn``).

    Returns
    -------
    Intent
        The selected Intent — never ``None``.  Falls back to ``INTENT_IDLE``.
    """
    agent = ctx.self_state
    global_goal: str = agent.get("global_goal", "get_rich")
    kill_target_id: Optional[str] = agent.get("kill_target_id")

    # ── PR2 immediate needs: critical survival/healing first ──────────────────
    if need_result is not None:
        immediate = [n for n in need_result.immediate_needs if n.trigger_context in ("survival", "healing")]
        drink_now = next((n for n in immediate if n.key == "drink_now"), None)
        eat_now = next((n for n in immediate if n.key == "eat_now"), None)
        heal_now = next((n for n in immediate if n.key == "heal_now"), None)

        if drink_now and drink_now.urgency >= CRITICAL_THIRST_THRESHOLD / 100.0:
            return _make_intent(
                INTENT_SEEK_WATER,
                float(drink_now.urgency),
                source_goal=None,
                reason=drink_now.reason or f"Критическая жажда — срочный поиск воды (жажда {agent.get('thirst', 0)}%)",
                created_turn=world_turn,
            )
        if eat_now and eat_now.urgency >= CRITICAL_HUNGER_THRESHOLD / 100.0:
            return _make_intent(
                INTENT_SEEK_FOOD,
                float(eat_now.urgency),
                source_goal=None,
                reason=eat_now.reason or f"Критический голод — срочный поиск еды (голод {agent.get('hunger', 0)}%)",
                created_turn=world_turn,
            )
        if heal_now and heal_now.urgency >= _HARD_INTERRUPT_HEAL:
            return _make_intent(
                INTENT_HEAL_SELF,
                float(heal_now.urgency),
                source_goal=None,
                reason=heal_now.reason or f"HP опасно низкий — срочное лечение (HP={agent.get('hp', '?')})",
                created_turn=world_turn,
            )

    # ── Special case: emission threat (always overrides equipment/resource needs) ──
    # Emission is a life-threatening event — it must override reload_or_rearm (1.0)
    # and any other survival needs.
    if needs.avoid_emission > _NEGLIGIBLE_THRESHOLD:
        terrain = ctx.location_state.get("terrain_type", "")
        if terrain not in EMISSION_DANGEROUS_TERRAIN:
            # Safe terrain — shelter in place
            return _make_intent(
                INTENT_WAIT_IN_SHELTER,
                needs.avoid_emission,
                source_goal=None,
                reason="Нахожусь в укрытии — жду окончания выброса",
                created_turn=world_turn,
            )
        else:
            # Dangerous terrain — flee; target_location_id filled by planner
            return _make_intent(
                INTENT_FLEE_EMISSION,
                needs.avoid_emission,
                source_goal=None,
                reason="Угроза выброса — нужно бежать в укрытие",
                created_turn=world_turn,
            )

    # ── Hard interrupts for critical needs (survive > emission [handled above] > heal > drink > eat) ──
    if needs.survive_now >= _HARD_INTERRUPT_SURVIVE_NOW:
        return _make_intent(
            INTENT_ESCAPE_DANGER,
            needs.survive_now,
            source_goal=None,
            reason=f"HP критически низкий — срочное бегство (HP={agent.get('hp', '?')})",
            created_turn=world_turn,
        )
    if needs.heal_self >= _HARD_INTERRUPT_HEAL:
        return _make_intent(
            INTENT_HEAL_SELF,
            needs.heal_self,
            source_goal=None,
            reason=f"HP опасно низкий — срочное лечение (HP={agent.get('hp', '?')})",
            created_turn=world_turn,
        )
    # Critical thirst / hunger use the same integer thresholds as PlanMonitor
    # (tick_constants.CRITICAL_THIRST_THRESHOLD / CRITICAL_HUNGER_THRESHOLD = 80)
    # to prevent the sleep-abort loop where PlanMonitor aborts sleep for hunger/thirst
    # but select_intent picks rest again.
    if agent.get("thirst", 0) >= CRITICAL_THIRST_THRESHOLD:
        return _make_intent(
            INTENT_SEEK_WATER,
            needs.drink,
            source_goal=None,
            reason=f"Критическая жажда — срочный поиск воды (жажда {agent.get('thirst', 0)}%)",
            created_turn=world_turn,
        )
    if agent.get("hunger", 0) >= CRITICAL_HUNGER_THRESHOLD:
        return _make_intent(
            INTENT_SEEK_FOOD,
            needs.eat,
            source_goal=None,
            reason=f"Критический голод — срочный поиск еды (голод {agent.get('hunger', 0)}%)",
            created_turn=world_turn,
        )

    # ── Walk the priority map ─────────────────────────────────────────────────
    best_intent: Optional[Intent] = None
    best_score: float = _NEGLIGIBLE_THRESHOLD  # anything below this is ignored

    has_food_immediate = False
    has_drink_immediate = False
    if need_result is not None:
        has_food_immediate = any(
            n.key == "eat_now" and n.trigger_context in ("survival", "rest_preparation")
            for n in need_result.immediate_needs
        )
        has_drink_immediate = any(
            n.key == "drink_now" and n.trigger_context in ("survival", "rest_preparation")
            for n in need_result.immediate_needs
        )

    for drive_attr, intent_kind, reason_tmpl in _PRIORITY_MAP:
        if drive_attr == "eat" and not has_food_immediate and int(agent.get("hunger", 0)) < _SOFT_CONSUME_FOOD_THRESHOLD:
            continue
        if drive_attr == "drink" and not has_drink_immediate and int(agent.get("thirst", 0)) < _SOFT_CONSUME_DRINK_THRESHOLD:
            continue
        score: float = getattr(needs, drive_attr, 0.0)
        if score > best_score:
            best_score = score
            if drive_attr == "reload_or_rearm":
                reason_tmpl = _resupply_reason_template(need_result) or reason_tmpl
            # Enrich reason template with agent context
            reason = _enrich_reason(reason_tmpl, drive_attr, needs, agent)
            target_id, target_loc_id = _resolve_targets(intent_kind, ctx, kill_target_id)
            best_intent = _make_intent(
                intent_kind,
                score,
                source_goal=_source_goal_for(intent_kind, global_goal),
                target_id=target_id,
                target_location_id=target_loc_id,
                reason=reason,
                created_turn=world_turn,
            )

    if best_intent is None:
        return _make_intent(INTENT_IDLE, 0.0, reason="Нет активных потребностей", created_turn=world_turn)

    return best_intent


def is_hard_interrupt(needs: NeedScores, agent: dict[str, Any] | None = None) -> bool:
    """Return True if current needs demand an unconditional plan reset."""
    if needs.survive_now >= _HARD_INTERRUPT_SURVIVE_NOW:
        return True
    if needs.avoid_emission >= _HARD_INTERRUPT_EMISSION:
        return True
    if needs.heal_self >= _HARD_INTERRUPT_HEAL:
        return True
    if agent is not None:
        if agent.get("thirst", 0) >= CRITICAL_THIRST_THRESHOLD:
            return True
        if agent.get("hunger", 0) >= CRITICAL_HUNGER_THRESHOLD:
            return True
    else:
        # Fallback: use need score approximation when agent dict unavailable
        if needs.drink >= CRITICAL_THIRST_THRESHOLD / 100.0:
            return True
        if needs.eat >= CRITICAL_HUNGER_THRESHOLD / 100.0:
            return True
    return False


# ── Private helpers ────────────────────────────────────────────────────────────

def _make_intent(
    kind: str,
    score: float,
    source_goal: Optional[str] = None,
    target_id: Optional[str] = None,
    target_location_id: Optional[str] = None,
    reason: Optional[str] = None,
    created_turn: Optional[int] = None,
    expires_turn: Optional[int] = None,
) -> Intent:
    return Intent(
        kind=kind,
        score=score,
        source_goal=source_goal,
        target_id=target_id,
        target_location_id=target_location_id,
        reason=reason,
        created_turn=created_turn,
        expires_turn=expires_turn,
    )


def _source_goal_for(intent_kind: str, global_goal: str) -> Optional[str]:
    """Map intent kind back to the global goal it serves."""
    mapping: dict[str, str] = {
        INTENT_GET_RICH: "get_rich",
        INTENT_HUNT_TARGET: "kill_stalker",
        INTENT_SEARCH_INFORMATION: "unravel_zone_mystery",
        INTENT_LEAVE_ZONE: global_goal,
    }
    return mapping.get(intent_kind)


def _resolve_targets(
    intent_kind: str,
    ctx: AgentContext,
    kill_target_id: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (target_id, target_location_id) for the given intent."""
    if intent_kind == INTENT_HUNT_TARGET and kill_target_id:
        # Try to find last known location from known_targets
        for t in ctx.known_targets:
            if t.get("agent_id") == kill_target_id:
                return kill_target_id, t.get("location_id")
        return kill_target_id, None
    return None, None


def _enrich_reason(
    template: str,
    drive_attr: str,
    needs: NeedScores,
    agent: dict[str, Any],
) -> str:
    """Add numeric context to the reason string."""
    value = getattr(needs, drive_attr, 0.0)
    enrichments: dict[str, str] = {
        "survive_now":  f"HP = {agent.get('hp', '?')} (крит. ≤10)",
        "heal_self":    f"HP = {agent.get('hp', '?')} (порог ≤50)",
        "eat":          f"голод {agent.get('hunger', 0)}%",
        "drink":        f"жажда {agent.get('thirst', 0)}%",
        "sleep":        f"усталость {agent.get('sleepiness', 0)}%",
        "avoid_emission": f"выброс (score={value:.2f})",
    }
    detail = enrichments.get(drive_attr)
    if detail:
        return f"{template} ({detail})"
    return f"{template} (score={value:.2f})"


def _resupply_reason_template(need_result: NeedEvaluationResult | None) -> str | None:
    if need_result is None:
        return None
    item_needs = list(need_result.item_needs)
    if not item_needs:
        return None
    from .item_needs import choose_dominant_item_need

    dominant = choose_dominant_item_need(item_needs)
    if dominant is None:
        return None
    mapping = {
        "drink": "Недостаточный запас воды",
        "food": "Недостаточный запас еды",
        "ammo": "Недостаточно патронов",
        "medicine": "Недостаточно медикаментов",
        "weapon": "Нет оружия",
        "armor": "Нет брони",
    }
    return mapping.get(dominant.key, dominant.reason or None)
