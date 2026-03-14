"""Item type definitions for Zone Stalkers."""

ITEM_TYPES: dict = {
    # Medical
    "medkit": {
        "name": "First Aid Kit", "type": "medical",
        "weight": 0.5, "value": 200,
        "effects": {"hp": 50},
    },
    "bandage": {
        "name": "Bandage", "type": "medical",
        "weight": 0.1, "value": 50,
        "effects": {"hp": 15},
    },
    "antirad": {
        "name": "Anti-Radiation Drug", "type": "medical",
        "weight": 0.2, "value": 150,
        "effects": {"radiation": -30},
    },
    # Weapons
    "ak74": {
        "name": "AK-74", "type": "weapon",
        "weight": 3.5, "value": 1500,
        "damage": 25, "range": 3, "ammo_type": "5.45x39",
    },
    "pistol": {
        "name": "PM Pistol", "type": "weapon",
        "weight": 0.7, "value": 500,
        "damage": 15, "range": 2, "ammo_type": "9x18",
    },
    "shotgun": {
        "name": "TOZ-34 Shotgun", "type": "weapon",
        "weight": 3.0, "value": 800,
        "damage": 40, "range": 1, "ammo_type": "12gauge",
    },
    # Armor
    "leather_jacket": {
        "name": "Leather Jacket", "type": "armor",
        "weight": 2.0, "value": 300, "defense": 5,
    },
    "stalker_suit": {
        "name": "Stalker Suit", "type": "armor",
        "weight": 5.0, "value": 1500, "defense": 15,
    },
    "exoskeleton": {
        "name": "Exoskeleton", "type": "armor",
        "weight": 8.0, "value": 6000, "defense": 30,
    },
    # Ammo
    "ammo_545": {
        "name": "5.45x39 ammo (30)", "type": "ammo",
        "weight": 0.3, "value": 100,
        "ammo_type": "5.45x39", "count": 30,
    },
    "ammo_9mm": {
        "name": "9x18 ammo (20)", "type": "ammo",
        "weight": 0.2, "value": 60,
        "ammo_type": "9x18", "count": 20,
    },
    "ammo_12gauge": {
        "name": "12 Gauge shells (10)", "type": "ammo",
        "weight": 0.3, "value": 80,
        "ammo_type": "12gauge", "count": 10,
    },
    # Consumables
    "vodka": {
        "name": "Vodka", "type": "consumable",
        "weight": 0.5, "value": 50,
        "effects": {"radiation": -10, "hp": -5, "thirst": -20},
    },
    "bread": {
        "name": "Bread", "type": "consumable",
        "weight": 0.3, "value": 20,
        "effects": {"stamina": 20, "hunger": -35},
    },
    "energy_drink": {
        "name": "Energy Drink", "type": "consumable",
        "weight": 0.3, "value": 80,
        "effects": {"stamina": 50, "thirst": -40, "hunger": -10},
    },
    # Detectors
    "echo_detector": {
        "name": "Echo Detector", "type": "detector",
        "weight": 0.5, "value": 500,
        "detection_radius": 2,
    },
    "veles_detector": {
        "name": "Veles Detector", "type": "detector",
        "weight": 0.8, "value": 3000,
        "detection_radius": 4,
    },
}

# ── Derived item-type sets (single source of truth used by rules & bots) ─────

# Items that can be consumed (medical + consumable categories)
CONSUMABLE_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items() if v["type"] in ("medical", "consumable")
)

# Items that restore HP (medkit, bandage)
HEAL_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items()
    if v["type"] == "medical" and v.get("effects", {}).get("hp", 0) > 0
)

# Items that reduce hunger
FOOD_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items()
    if "hunger" in v.get("effects", {})
)

# Items that reduce thirst
DRINK_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items()
    if "thirst" in v.get("effects", {})
)
