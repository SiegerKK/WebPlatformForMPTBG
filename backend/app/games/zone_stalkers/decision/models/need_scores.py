"""NeedScores — normalised pressure values for each drive.

All scores are floats in [0.0, 1.0].
Higher = more urgent.

Formulas live in ``decision/needs.py::evaluate_needs``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NeedScores:
    """Pressure values for every recognised NPC drive.

    The dominant drive (highest score) determines the selected Intent.
    Ties are broken by a fixed priority list defined in ``intents.py``.
    """

    # ── Survival ──────────────────────────────────────────────────────────────
    survive_now: float = 0.0        # hp critically low
    heal_self: float = 0.0          # hp low but not immediately lethal
    eat: float = 0.0                # hunger level
    drink: float = 0.0              # thirst level
    sleep: float = 0.0              # sleepiness level
    reload_or_rearm: float = 0.0    # missing weapon, armor, or ammo

    # ── Environmental ─────────────────────────────────────────────────────────
    avoid_emission: float = 0.0     # emission active or imminent

    # ── Goal-directed ─────────────────────────────────────────────────────────
    get_rich: float = 0.0           # wealth accumulation pressure
    hunt_target: float = 0.0        # kill_stalker goal urgency
    unravel_zone_mystery: float = 0.0  # document/mystery goal urgency
    leave_zone: float = 0.0         # global goal achieved → exit

    # ── Economic ─────────────────────────────────────────────────────────────
    trade: float = 0.0              # sell artifacts / buy supplies at trader

    # ── Social (Phase 6+) ────────────────────────────────────────────────────
    negotiate: float = 0.0          # social interaction opportunity
    maintain_group: float = 0.0     # group cohesion or group need detected
    help_ally: float = 0.0          # ally in distress nearby
    join_group: float = 0.0         # opportunity/desire to form a group
