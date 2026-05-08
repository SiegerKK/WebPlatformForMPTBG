"""Intent — the dominant behavioural intention selected for one tick.

At any given moment an agent has exactly one dominant Intent
(Intent invariant 11.1 from the refactor spec).

``kind`` is a string constant that maps to a set of valid plan steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ── Valid intent kinds ────────────────────────────────────────────────────────
# Survival
INTENT_ESCAPE_DANGER = "escape_danger"
INTENT_HEAL_SELF = "heal_self"
INTENT_SEEK_FOOD = "seek_food"
INTENT_SEEK_WATER = "seek_water"
INTENT_REST = "rest"
INTENT_RESUPPLY = "resupply"

# Environmental
INTENT_FLEE_EMISSION = "flee_emission"
INTENT_WAIT_IN_SHELTER = "wait_in_shelter"

# Economic
INTENT_TRADE = "trade"
INTENT_LOOT = "loot"
INTENT_EXPLORE = "explore"
INTENT_SELL_ARTIFACTS = "sell_artifacts"

# Goal-directed
INTENT_GET_RICH = "get_rich"
INTENT_HUNT_TARGET = "hunt_target"
INTENT_SEARCH_INFORMATION = "search_information"
INTENT_LEAVE_ZONE = "leave_zone"
INTENT_UPGRADE_EQUIPMENT = "upgrade_equipment"

# Social (Phase 6+)
INTENT_NEGOTIATE = "negotiate"
INTENT_ASSIST_ALLY = "assist_ally"
INTENT_FORM_GROUP = "form_group"
INTENT_FOLLOW_GROUP_PLAN = "follow_group_plan"
INTENT_MAINTAIN_GROUP = "maintain_group"

# Fallback
INTENT_IDLE = "idle"

# ── Ordered tuple of all valid intent kinds ───────────────────────────────────
ALL_INTENTS: tuple[str, ...] = (
    INTENT_ESCAPE_DANGER,
    INTENT_FLEE_EMISSION,
    INTENT_WAIT_IN_SHELTER,
    INTENT_HEAL_SELF,
    INTENT_SEEK_WATER,
    INTENT_SEEK_FOOD,
    INTENT_REST,
    INTENT_RESUPPLY,
    INTENT_SELL_ARTIFACTS,
    INTENT_TRADE,
    INTENT_UPGRADE_EQUIPMENT,
    INTENT_LOOT,
    INTENT_EXPLORE,
    INTENT_GET_RICH,
    INTENT_HUNT_TARGET,
    INTENT_SEARCH_INFORMATION,
    INTENT_LEAVE_ZONE,
    INTENT_NEGOTIATE,
    INTENT_ASSIST_ALLY,
    INTENT_MAINTAIN_GROUP,
    INTENT_FORM_GROUP,
    INTENT_FOLLOW_GROUP_PLAN,
    INTENT_IDLE,
)


@dataclass
class Intent:
    """The dominant behavioural intention for one agent on one tick.

    Parameters
    ----------
    kind
        One of the ``INTENT_*`` string constants defined in this module.
    score
        The NeedScore value that drove this intent (0.0–1.0).
    source_goal
        The global goal this intent serves, if any (e.g. ``"get_rich"``).
    target_id
        Agent ID of the focus target (e.g. kill target, ally).
    target_location_id
        Location the agent intends to travel to / act at.
    reason
        Human-readable explanation used in debug/explain output.
    created_turn
        World turn when this intent was created.
    expires_turn
        World turn after which this intent is considered stale.
        ``None`` means it persists until plan completion or interrupt.
    """

    kind: str
    score: float
    source_goal: Optional[str] = None
    target_id: Optional[str] = None
    target_location_id: Optional[str] = None
    reason: Optional[str] = None
    created_turn: Optional[int] = None
    expires_turn: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None
