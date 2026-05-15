"""Shared helpers and source-of-truth constants for Zone Stalkers location images."""
from __future__ import annotations

from typing import Any

WEATHER_IMAGE_SLOTS: tuple[str, ...] = (
    "clear",
    "fog",
    "rain",
    "night_clear",
    "night_rain",
)
PSI_IMAGE_SLOTS: tuple[str, ...] = (
    "low",
    "medium",
    "high",
    "critical",
    "max",
)
UNDERGROUND_IMAGE_SLOTS: tuple[str, ...] = (
    "default",
    "dark",
    "emergency_light",
    "power_failure",
    "flooded",
    "toxic",
    "anomaly",
    "psi_low",
    "psi_high",
    "combat",
)
LOCATION_IMAGE_GROUPS: tuple[str, ...] = ("normal", "gloom", "anomaly", "psi", "underground")
LOCATION_IMAGE_GROUP_SLOT_MAP: dict[str, tuple[str, ...]] = {
    "normal": WEATHER_IMAGE_SLOTS,
    "gloom": WEATHER_IMAGE_SLOTS,
    "anomaly": WEATHER_IMAGE_SLOTS,
    "psi": PSI_IMAGE_SLOTS,
    "underground": UNDERGROUND_IMAGE_SLOTS,
}

# Legacy compatibility constants (old 5 flat slots)
ORDERED_LOCATION_IMAGE_SLOTS: tuple[str, ...] = WEATHER_IMAGE_SLOTS
VALID_LOCATION_IMAGE_SLOTS: frozenset[str] = frozenset(ORDERED_LOCATION_IMAGE_SLOTS)
VALID_LOCATION_IMAGE_GROUPS: frozenset[str] = frozenset(LOCATION_IMAGE_GROUPS)


def _bool_flag(value: Any) -> bool:
    return bool(value) if value is not None else False


def normalize_location_image_profile(loc: dict[str, Any]) -> dict[str, bool]:
    profile = loc.get("image_profile")
    if not isinstance(profile, dict):
        profile = {}
    normalized = {
        "is_anomalous": _bool_flag(profile.get("is_anomalous")),
        "is_psi": _bool_flag(profile.get("is_psi")),
        "is_underground": _bool_flag(profile.get("is_underground")),
    }
    loc["image_profile"] = normalized
    return normalized


def get_enabled_image_groups(profile: dict[str, Any] | None) -> list[str]:
    p = profile or {}
    if p.get("is_underground"):
        groups = ["underground"]
        if p.get("is_anomalous"):
            groups.append("anomaly")
        if p.get("is_psi"):
            groups.append("psi")
        return groups

    groups = ["normal", "gloom"]
    if p.get("is_anomalous"):
        groups.append("anomaly")
    if p.get("is_psi"):
        groups.append("psi")
    return groups


def get_required_image_groups(profile: dict[str, Any] | None) -> list[str]:
    p = profile or {}
    return ["underground"] if p.get("is_underground") else ["normal", "gloom"]


def _get_group_slots_dict(slots_v2: dict[str, Any], group: str) -> dict[str, Any]:
    group_slots = slots_v2.get(group)
    if not isinstance(group_slots, dict):
        group_slots = {}
        slots_v2[group] = group_slots
    return group_slots


def ensure_image_slots_v2_skeleton(loc: dict[str, Any]) -> None:
    profile = normalize_location_image_profile(loc)
    slots_v2 = loc.get("image_slots_v2")
    if not isinstance(slots_v2, dict):
        slots_v2 = {}
    loc["image_slots_v2"] = slots_v2

    ensured_groups = set(get_required_image_groups(profile)) | set(get_enabled_image_groups(profile))
    for group in ensured_groups:
        if group not in LOCATION_IMAGE_GROUP_SLOT_MAP:
            continue
        group_slots = _get_group_slots_dict(slots_v2, group)
        for slot in LOCATION_IMAGE_GROUP_SLOT_MAP[group]:
            group_slots.setdefault(slot, None)


def _slot_url(slots_v2: dict[str, Any], group: str, slot: str) -> str | None:
    group_data = slots_v2.get(group)
    if not isinstance(group_data, dict):
        return None
    value = group_data.get(slot)
    return value if isinstance(value, str) and value else None


def any_image_in_slots_v2(slots_v2: dict[str, Any] | None) -> bool:
    if not isinstance(slots_v2, dict):
        return False
    for group in LOCATION_IMAGE_GROUPS:
        group_slots = slots_v2.get(group)
        if not isinstance(group_slots, dict):
            continue
        for slot in LOCATION_IMAGE_GROUP_SLOT_MAP[group]:
            value = group_slots.get(slot)
            if isinstance(value, str) and value:
                return True
    return False


def get_fallback_image_refs(loc: dict[str, Any]) -> list[tuple[str, str]]:
    profile = normalize_location_image_profile(loc)
    refs: list[tuple[str, str]] = []

    if profile.get("is_underground"):
        refs.extend([("underground", s) for s in UNDERGROUND_IMAGE_SLOTS])
        if profile.get("is_psi"):
            refs.append(("psi", "low"))
        if profile.get("is_anomalous"):
            refs.append(("anomaly", "clear"))
        return refs

    refs.extend([
        ("normal", "clear"),
        ("gloom", "clear"),
        ("normal", "fog"),
        ("normal", "rain"),
        ("normal", "night_clear"),
        ("normal", "night_rain"),
        ("gloom", "fog"),
        ("gloom", "rain"),
        ("gloom", "night_clear"),
        ("gloom", "night_rain"),
    ])

    if profile.get("is_anomalous"):
        refs.extend([("anomaly", s) for s in WEATHER_IMAGE_SLOTS])
    if profile.get("is_psi"):
        refs.append(("psi", "low"))
    return refs


def _migrate_legacy_to_v2(loc: dict[str, Any]) -> None:
    slots_v2 = loc.get("image_slots_v2")
    if not isinstance(slots_v2, dict):
        slots_v2 = {}
    loc["image_slots_v2"] = slots_v2

    old_slots = loc.get("image_slots")
    if isinstance(old_slots, dict):
        normal = _get_group_slots_dict(slots_v2, "normal")
        for slot in WEATHER_IMAGE_SLOTS:
            old_val = old_slots.get(slot)
            if isinstance(old_val, str) and old_val and not normal.get(slot):
                normal[slot] = old_val

    old_primary = loc.get("primary_image_slot")
    existing_ref = loc.get("primary_image_ref")
    if isinstance(old_primary, str) and old_primary in WEATHER_IMAGE_SLOTS:
        if not isinstance(existing_ref, dict) or existing_ref.get("group") == "normal":
            loc["primary_image_ref"] = {"group": "normal", "slot": old_primary}

    legacy_url = loc.get("image_url")
    if isinstance(legacy_url, str) and legacy_url and not any_image_in_slots_v2(slots_v2):
        normal = _get_group_slots_dict(slots_v2, "normal")
        if not normal.get("clear"):
            normal["clear"] = legacy_url
        if not isinstance(loc.get("primary_image_ref"), dict):
            loc["primary_image_ref"] = {"group": "normal", "slot": "clear"}


def _sync_legacy_from_v2(loc: dict[str, Any]) -> None:
    slots_v2 = loc.get("image_slots_v2") if isinstance(loc.get("image_slots_v2"), dict) else {}
    normal = slots_v2.get("normal") if isinstance(slots_v2.get("normal"), dict) else {}

    legacy_slots: dict[str, str | None] = {slot: None for slot in WEATHER_IMAGE_SLOTS}
    for slot in WEATHER_IMAGE_SLOTS:
        value = normal.get(slot)
        legacy_slots[slot] = value if isinstance(value, str) and value else None

    loc["image_slots"] = legacy_slots

    ref = loc.get("primary_image_ref")
    if isinstance(ref, dict) and ref.get("group") == "normal" and ref.get("slot") in WEATHER_IMAGE_SLOTS:
        loc["primary_image_slot"] = ref.get("slot")
    else:
        first_normal = next((slot for slot in WEATHER_IMAGE_SLOTS if legacy_slots.get(slot)), None)
        loc["primary_image_slot"] = first_normal


def get_location_primary_image_url(loc: dict[str, Any]) -> str | None:
    # Keep a snapshot of pre-migration data so legacy-only fallback stays possible
    # when there are truly no v2 slot URLs.
    pre_migrate_slots_v2 = loc.get("image_slots_v2") if isinstance(loc.get("image_slots_v2"), dict) else None
    pre_migrate_legacy_url = loc.get("image_url")

    migrate_location_images_v2(loc)
    slots_v2 = loc.get("image_slots_v2") if isinstance(loc.get("image_slots_v2"), dict) else {}
    ref = loc.get("primary_image_ref")

    if isinstance(ref, dict):
        group = ref.get("group")
        slot = ref.get("slot")
        if isinstance(group, str) and isinstance(slot, str):
            url = _slot_url(slots_v2, group, slot)
            if url:
                return url

    for group, slot in get_fallback_image_refs(loc):
        url = _slot_url(slots_v2, group, slot)
        if url:
            loc["primary_image_ref"] = {"group": group, "slot": slot}
            return url

    # Strict rule for v2: do not fallback to stale image_url when v2 already has
    # any slot URL (including disabled groups preserved in state).
    if any_image_in_slots_v2(slots_v2):
        return None

    # Legacy-only migration fallback: allow image_url only when v2 had no values.
    if isinstance(pre_migrate_legacy_url, str) and pre_migrate_legacy_url and not any_image_in_slots_v2(pre_migrate_slots_v2):
        return pre_migrate_legacy_url
    return None


def sync_location_primary_image_url_v2(loc: dict[str, Any]) -> None:
    migrate_location_images_v2(loc)
    loc["image_url"] = get_location_primary_image_url(loc)
    _sync_legacy_from_v2(loc)


def migrate_location_images_v2(loc: dict[str, Any]) -> None:
    ensure_image_slots_v2_skeleton(loc)
    _migrate_legacy_to_v2(loc)

    ref = loc.get("primary_image_ref")
    if not isinstance(ref, dict):
        loc["primary_image_ref"] = None
    else:
        group = ref.get("group")
        slot = ref.get("slot")
        if (
            not isinstance(group, str)
            or not isinstance(slot, str)
            or group not in VALID_LOCATION_IMAGE_GROUPS
            or slot not in LOCATION_IMAGE_GROUP_SLOT_MAP[group]
        ):
            loc["primary_image_ref"] = None

    ensure_image_slots_v2_skeleton(loc)


# Legacy wrappers kept for old callsites.
def sync_location_primary_image_url(loc: dict[str, Any]) -> None:
    sync_location_primary_image_url_v2(loc)


def migrate_location_images(loc: dict[str, Any]) -> None:
    migrate_location_images_v2(loc)
    _sync_legacy_from_v2(loc)
    if loc.get("image_url") is None:
        loc["image_url"] = get_location_primary_image_url(loc)


def validate_image_group_slot(group: str, slot: str) -> bool:
    return group in VALID_LOCATION_IMAGE_GROUPS and slot in LOCATION_IMAGE_GROUP_SLOT_MAP.get(group, ())


def is_group_enabled_for_location(loc: dict[str, Any], group: str) -> bool:
    profile = normalize_location_image_profile(loc)
    return group in get_enabled_image_groups(profile)
