"""constants — shared constants for the Zone Stalkers decision subsystem.

Centralises values that were previously duplicated across multiple modules
(tick_rules.py, needs.py, intents.py).  Import from here to keep all three
in sync without a single source-of-truth problem.
"""
from __future__ import annotations

# Terrain types where agents are killed / heavily damaged by an emission.
# Must stay in sync with tick_rules._EMISSION_DANGEROUS_TERRAIN (which is kept
# for backwards compat with existing helpers in that module).
EMISSION_DANGEROUS_TERRAIN: frozenset[str] = frozenset({
    "plain", "hills", "swamp", "field_camp", "slag_heaps", "bridge",
})

# Target number of ammo items (boxes) the agent should carry for each equipped weapon.
# Used by both ``needs._score_reload_or_rearm`` and ``planner._plan_resupply``.
DESIRED_AMMO_COUNT: int = 3

# PR4 objective soft thresholds for non-critical need restoration.
SOFT_RESTORE_FOOD_THRESHOLD: int = 50
SOFT_RESTORE_DRINK_THRESHOLD: int = 40
SOFT_REST_THRESHOLD: int = 50
CRITICAL_REST_THRESHOLD: int = 80

# ── Active-plan step timeout constants (Part 2) ───────────────────────────────
# Number of turns a step may stay in PENDING status before being timed out.
ACTIVE_PLAN_PENDING_TIMEOUT_TURNS: int = 5
ACTIVE_PLAN_TRADE_PENDING_TIMEOUT_TURNS: int = 5
ACTIVE_PLAN_EXPLORE_PENDING_TIMEOUT_TURNS: int = 5
# Grace turns added on top of the expected step duration before calling a RUNNING step timed out.
ACTIVE_PLAN_RUNNING_GRACE_TURNS: int = 5

# ── Hunter preparation — equipment class ranking ──────────────────────────────
# Used by evaluate_hunter_equipment_advantage() in the objective generator.
WEAPON_CLASS_RANK: dict[str, int] = {
    "none": 0,
    "melee": 1,
    "pistol": 2,
    "shotgun": 3,
    "rifle": 4,
    "sniper": 5,
}

ARMOR_CLASS_RANK: dict[str, int] = {
    "none": 0,
    "unknown": 0,
    "light": 1,
    "medium": 2,
    "heavy": 3,
}

# Minimum thresholds for a hunter (kill_stalker NPC) to be considered prepared.
HUNT_MIN_CASH_RESERVE: int = 300
HUNT_MIN_AMMO_ROUNDS: int = 20
HUNT_MIN_MED_ITEMS: int = 2
HUNT_MIN_ARMOR_CLASS_FOR_STRONG_TARGET: str = "medium"
HUNT_MIN_WEAPON_CLASS_FOR_RIFLE_TARGET: str = "rifle"

# Minimum advantage score required before engaging a target.
HUNT_REQUIRED_ADVANTAGE_SCORE: float = 0.20
HUNT_REQUIRED_ADVANTAGE_SCORE_STRONG_TARGET: float = 0.35   # target combat_strength >= 0.8
HUNT_REQUIRED_ADVANTAGE_SCORE_CO_LOCATED: float = 0.10      # opportunistic engagement
