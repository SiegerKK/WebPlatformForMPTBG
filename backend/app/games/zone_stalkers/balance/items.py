"""Item type definitions for Zone Stalkers.

Full item catalogue — all items with their parameters (type, weight, value, effects).
Each item entry is a pure-data dict; no game logic lives here.

Categories:
  medical    — healing and radiation treatment consumables
  weapon     — ranged weapons with damage/range/ammo_type stats
  armor      — protective suits with defense rating
  ammo       — ammunition packs linked to a weapon via ammo_type
  consumable — food, drink and stimulants
  detector   — anomaly detection devices
"""

ITEM_TYPES: dict = {
    # ── Medical ───────────────────────────────────────────────────────────────
    "medkit": {
        "name": "Аптечка", "type": "medical",
        "weight": 0.5, "value": 200,
        "effects": {"hp": 50},
        "description": "Стандартная полевая аптечка. Восстанавливает 50 HP.",
    },
    "bandage": {
        "name": "Бинт", "type": "medical",
        "weight": 0.1, "value": 50,
        "effects": {"hp": 15},
        "description": "Перевязочный материал. Восстанавливает 15 HP.",
    },
    "antirad": {
        "name": "Антирад", "type": "medical",
        "weight": 0.2, "value": 150,
        "effects": {"radiation": -30},
        "description": "Препарат от радиационного отравления. Снижает радиацию на 30.",
    },
    "stimpack": {
        "name": "Стимпак", "type": "medical",
        "weight": 0.3, "value": 350,
        "effects": {"hp": 30, "hunger": 10},
        "description": "Боевой стимулятор. Восстанавливает 30 HP, немного повышает голод.",
    },
    # ── Weapons ───────────────────────────────────────────────────────────────
    "ak74": {
        "name": "АК-74", "type": "weapon",
        "weight": 3.5, "value": 1500,
        "damage": 25, "range": 3, "ammo_type": "5.45x39",
        "description": "Автомат Калашникова. Надёжное оружие среднего боя.",
    },
    "pistol": {
        "name": "Пистолет ПМ", "type": "weapon",
        "weight": 0.7, "value": 500,
        "damage": 15, "range": 2, "ammo_type": "9x18",
        "description": "Пистолет Макарова. Компактное личное оружие.",
    },
    "shotgun": {
        "name": "Обрез ТОЗ-34", "type": "weapon",
        "weight": 3.0, "value": 800,
        "damage": 40, "range": 1, "ammo_type": "12gauge",
        "description": "Двустволка ближнего боя. Высокий урон, малая дальность.",
    },
    # ── Armor ─────────────────────────────────────────────────────────────────
    "leather_jacket": {
        "name": "Кожаная куртка", "type": "armor",
        "weight": 2.0, "value": 300, "defense": 5,
        "description": "Простейшая защита. Дешёвая, но лучше чем ничего.",
    },
    "stalker_suit": {
        "name": "Комбинезон сталкера", "type": "armor",
        "weight": 5.0, "value": 1500, "defense": 15,
        "description": "Стандартный комбинезон с лёгкой бронёй и радиозащитой.",
    },
    "exoskeleton": {
        "name": "Экзоскелет", "type": "armor",
        "weight": 8.0, "value": 6000, "defense": 30,
        "description": "Тяжёлый боевой экзоскелет. Максимальная защита в Зоне.",
    },
    # ── Ammo ──────────────────────────────────────────────────────────────────
    "ammo_545": {
        "name": "Патроны 5.45х39 (30 шт.)", "type": "ammo",
        "weight": 0.3, "value": 100,
        "ammo_type": "5.45x39", "count": 30,
        "description": "Стандартные патроны для АК-74.",
    },
    "ammo_9mm": {
        "name": "Патроны 9х18 (20 шт.)", "type": "ammo",
        "weight": 0.2, "value": 60,
        "ammo_type": "9x18", "count": 20,
        "description": "Пистолетные патроны для ПМ.",
    },
    "ammo_12gauge": {
        "name": "Дробь 12 калибра (10 шт.)", "type": "ammo",
        "weight": 0.3, "value": 80,
        "ammo_type": "12gauge", "count": 10,
        "description": "Дробовые патроны для обреза ТОЗ-34.",
    },
    # ── Consumables ───────────────────────────────────────────────────────────
    "vodka": {
        "name": "Водка", "type": "consumable",
        "weight": 0.5, "value": 50,
        "effects": {"radiation": -10, "hp": -5, "thirst": -20},
        "description": "Народное средство от радиации. Снижает радиацию, немного портит здоровье.",
    },
    "bread": {
        "name": "Буханка хлеба", "type": "consumable",
        "weight": 0.3, "value": 20,
        "effects": {"stamina": 20, "hunger": -35},
        "description": "Простая еда. Утоляет голод на 35 единиц.",
    },
    "water": {
        "name": "Вода (0.5л)", "type": "consumable",
        "weight": 0.5, "value": 30,
        "effects": {"thirst": -50},
        "description": "Чистая вода. Утоляет жажду на 50 единиц.",
    },
    "energy_drink": {
        "name": "Энергетик", "type": "consumable",
        "weight": 0.3, "value": 80,
        "effects": {"stamina": 50, "thirst": -40, "hunger": -10},
        "description": "Энергетический напиток. Снимает усталость, утоляет жажду.",
    },
    "canned_food": {
        "name": "Тушёнка", "type": "consumable",
        "weight": 0.4, "value": 40,
        "effects": {"hunger": -50, "thirst": 5},
        "description": "Консервы. Хорошо утоляет голод, немного усиливает жажду.",
    },
    # ── Detectors ─────────────────────────────────────────────────────────────
    "echo_detector": {
        "name": "Детектор «Эхо»", "type": "detector",
        "weight": 0.5, "value": 500,
        "detection_radius": 2,
        "description": "Простой детектор аномалий. Радиус обнаружения 2.",
    },
    "veles_detector": {
        "name": "Детектор «Велес»", "type": "detector",
        "weight": 0.8, "value": 3000,
        "detection_radius": 4,
        "description": "Продвинутый детектор. Точно определяет артефакты в радиусе 4.",
    },
}

# ── Derived item-type sets (single source of truth used by rules & bots) ─────

# Items that can be consumed (medical + consumable categories)
CONSUMABLE_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items() if v["type"] in ("medical", "consumable")
)

# Items that restore HP (medkit, bandage, stimpack)
HEAL_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items()
    if v["type"] == "medical" and v.get("effects", {}).get("hp", 0) > 0
)

# Items that reduce hunger
FOOD_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items()
    if "hunger" in v.get("effects", {}) and v["effects"]["hunger"] < 0
)

# Items that reduce thirst
DRINK_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items()
    if "thirst" in v.get("effects", {}) and v["effects"]["thirst"] < 0
)

# Weapon item types
WEAPON_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items() if v["type"] == "weapon"
)

# Armor item types (wearable suits / jackets)
ARMOR_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items() if v["type"] == "armor"
)

# Ammo item types
AMMO_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items() if v["type"] == "ammo"
)

# Detector item types
DETECTOR_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items() if v["type"] == "detector"
)

# Mapping: weapon item key → ammo item key required for that weapon
AMMO_FOR_WEAPON: dict = {
    weapon_key: next(
        (ammo_key for ammo_key, ammo_v in ITEM_TYPES.items()
         if ammo_v["type"] == "ammo"
         and ammo_v.get("ammo_type") == weapon_v.get("ammo_type")),
        None,
    )
    for weapon_key, weapon_v in ITEM_TYPES.items()
    if weapon_v["type"] == "weapon"
}
