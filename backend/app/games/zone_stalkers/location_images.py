"""
Shared helpers for Zone Stalkers location image slots.

This module is the single source of truth for slot names and the
sync/migrate helpers so that both world_rules.py and router.py stay in sync.
"""
from __future__ import annotations

ORDERED_LOCATION_IMAGE_SLOTS: tuple[str, ...] = (
    "clear", "fog", "rain", "night_clear", "night_rain"
)
VALID_LOCATION_IMAGE_SLOTS: frozenset[str] = frozenset(ORDERED_LOCATION_IMAGE_SLOTS)


def sync_location_primary_image_url(loc: dict) -> None:
    """Keep loc['image_url'] in sync with image_slots[primary_image_slot]."""
    slots = loc.get("image_slots") or {}
    primary = loc.get("primary_image_slot")

    if primary and slots.get(primary):
        loc["image_url"] = slots[primary]
        return

    # If no slots populated but we have a raw image_url, keep it (legacy).
    if loc.get("image_url") and not any(v for v in slots.values() if v):
        return

    # Auto-select first available slot.
    for key in ORDERED_LOCATION_IMAGE_SLOTS:
        if slots.get(key):
            loc["primary_image_slot"] = key
            loc["image_url"] = slots[key]
            return

    loc["image_url"] = None


def migrate_location_images(loc: dict) -> None:
    """Lift legacy image_url into image_slots['clear'] if no slots exist yet."""
    if "image_slots" not in loc:
        loc["image_slots"] = {}

    slots = loc["image_slots"]
    existing_url = loc.get("image_url")

    # If image_url is set but no slots are populated, migrate it to 'clear'.
    if existing_url and not any(v for v in slots.values() if v):
        slots["clear"] = existing_url
        loc.setdefault("primary_image_slot", "clear")

    sync_location_primary_image_url(loc)
