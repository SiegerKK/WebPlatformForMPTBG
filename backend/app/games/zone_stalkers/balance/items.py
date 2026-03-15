"""Item type definitions for Zone Stalkers.

Full item catalogue — all items with their parameters (type, weight, value, effects).
Each item entry is a pure-data dict; no game logic lives here.

Categories:
  medical         — healing and radiation treatment consumables
  weapon          — ranged weapons with damage/range/ammo_type stats
  armor           — protective suits with defense rating
  ammo            — ammunition packs linked to a weapon via ammo_type
  consumable      — food, drink and stimulants
  detector        — anomaly detection devices
  secret_document — classified files, encrypted media, and research notes about
                    the Zone's origin; sought by stalkers pursuing the
                    ``unravel_zone_mystery`` global goal

``risk_tolerance`` (float 0.0–1.0) encodes the aggressiveness/risk profile that
suits an agent wanting this item.  Low values (≈0.0) mean a cautious stalker
prefers it; high values (≈1.0) mean a risk-seeking, combat-oriented stalker
prefers it.  The bot preference system uses this field to match items to agents
via their own ``risk_tolerance`` attribute.
"""

ITEM_TYPES: dict = {
    # ── Medical ───────────────────────────────────────────────────────────────
    "bandage": {
        "name": "Бинт", "type": "medical",
        "weight": 0.1, "value": 50, "risk_tolerance": 0.1,
        "effects": {"hp": 15},
        "description": "Перевязочный материал. Восстанавливает 15 HP.",
    },
    "medkit": {
        "name": "Аптечка", "type": "medical",
        "weight": 0.5, "value": 200, "risk_tolerance": 0.2,
        "effects": {"hp": 50},
        "description": "Стандартная полевая аптечка. Восстанавливает 50 HP.",
    },
    "army_medkit": {
        "name": "Военная аптечка", "type": "medical",
        "weight": 0.6, "value": 450, "risk_tolerance": 0.4,
        "effects": {"hp": 80},
        "description": "Военная аптечка высшего класса. Восстанавливает 80 HP.",
    },
    "stimpack": {
        "name": "Стимпак", "type": "medical",
        "weight": 0.3, "value": 350, "risk_tolerance": 0.7,
        "effects": {"hp": 50, "hunger": 10},
        "description": "Боевой стимулятор. Восстанавливает 50 HP, немного повышает голод.",
    },
    "morphine": {
        "name": "Морфин", "type": "medical",
        "weight": 0.15, "value": 300, "risk_tolerance": 0.5,
        "effects": {"hp": 25, "sleepiness": -20},
        "description": "Обезболивающее. Восстанавливает 25 HP, снижает усталость на 20.",
    },
    "antirad": {
        "name": "Антирад", "type": "medical",
        "weight": 0.2, "value": 150, "risk_tolerance": 0.3,
        "effects": {"radiation": -30},
        "description": "Препарат от радиационного отравления. Снижает радиацию на 30.",
    },
    "rad_cure": {
        "name": "Рад-Пурге", "type": "medical",
        "weight": 0.2, "value": 380, "risk_tolerance": 0.5,
        "effects": {"radiation": -60},
        "description": "Мощный антирадиационный препарат. Снижает радиацию на 60.",
    },
    # ── Weapons ───────────────────────────────────────────────────────────────
    "pistol": {
        "name": "Пистолет ПМ", "type": "weapon",
        "weight": 0.7, "value": 500, "risk_tolerance": 0.15,
        "damage": 15, "range": 2, "ammo_type": "9x18",
        "description": "Пистолет Макарова. Компактное личное оружие.",
    },
    "shotgun": {
        "name": "Обрез ТОЗ-34", "type": "weapon",
        "weight": 3.0, "value": 800, "risk_tolerance": 0.25,
        "damage": 40, "range": 1, "ammo_type": "12gauge",
        "description": "Двустволка ближнего боя. Высокий урон, малая дальность.",
    },
    "ak74": {
        "name": "АК-74", "type": "weapon",
        "weight": 3.5, "value": 1500, "risk_tolerance": 0.5,
        "damage": 25, "range": 3, "ammo_type": "5.45x39",
        "description": "Автомат Калашникова. Надёжное оружие среднего боя.",
    },
    "pkm": {
        "name": "ПКМ (пулемёт)", "type": "weapon",
        "weight": 7.5, "value": 3500, "risk_tolerance": 0.9,
        "damage": 35, "range": 3, "ammo_type": "7.62x54R",
        "description": "Ручной пулемёт Калашникова. Высокий DPS, тяжёлый.",
    },
    "svu_svd": {
        "name": "СВД (снайперская)", "type": "weapon",
        "weight": 4.2, "value": 4500, "risk_tolerance": 0.7,
        "damage": 50, "range": 5, "ammo_type": "7.62x54R",
        "description": "Снайперская винтовка. Максимальная дальность и урон.",
    },
    # ── Armor ─────────────────────────────────────────────────────────────────
    "leather_jacket": {
        "name": "Кожаная куртка", "type": "armor",
        "weight": 2.0, "value": 300, "defense": 5, "risk_tolerance": 0.1,
        "description": "Простейшая защита. Дешёвая, но лучше чем ничего.",
    },
    "stalker_suit": {
        "name": "Комбинезон сталкера", "type": "armor",
        "weight": 5.0, "value": 1500, "defense": 15, "risk_tolerance": 0.3,
        "description": "Стандартный комбинезон с лёгкой бронёй и радиозащитой.",
    },
    "combat_armor": {
        "name": "Боевой бронежилет", "type": "armor",
        "weight": 6.5, "value": 3000, "defense": 22, "risk_tolerance": 0.9,
        "description": "Военный бронежилет. Хорошая защита от пуль и аномалий.",
    },
    "seva_suit": {
        "name": "Костюм СЕВА", "type": "armor",
        "weight": 6.0, "value": 3500, "defense": 18, "risk_tolerance": 0.5,
        "description": "Научный комбинезон СЕВА с усиленной радиозащитой.",
    },
    "exoskeleton": {
        "name": "Экзоскелет", "type": "armor",
        "weight": 8.0, "value": 6000, "defense": 30, "risk_tolerance": 0.6,
        "description": "Тяжёлый боевой экзоскелет. Максимальная защита в Зоне.",
    },
    # ── Ammo ──────────────────────────────────────────────────────────────────
    "ammo_9mm": {
        "name": "Патроны 9х18 (20 шт.)", "type": "ammo",
        "weight": 0.2, "value": 60, "risk_tolerance": 0.3,
        "ammo_type": "9x18", "count": 20,
        "description": "Пистолетные патроны для ПМ.",
    },
    "ammo_12gauge": {
        "name": "Дробь 12 калибра (10 шт.)", "type": "ammo",
        "weight": 0.3, "value": 80, "risk_tolerance": 0.5,
        "ammo_type": "12gauge", "count": 10,
        "description": "Дробовые патроны для обреза ТОЗ-34.",
    },
    "ammo_545": {
        "name": "Патроны 5.45х39 (30 шт.)", "type": "ammo",
        "weight": 0.3, "value": 100, "risk_tolerance": 0.6,
        "ammo_type": "5.45x39", "count": 30,
        "description": "Стандартные патроны для АК-74.",
    },
    "ammo_762": {
        "name": "Патроны 7.62х54R (20 шт.)", "type": "ammo",
        "weight": 0.4, "value": 180, "risk_tolerance": 0.8,
        "ammo_type": "7.62x54R", "count": 20,
        "description": "Винтовочные патроны для ПКМ и СВД.",
    },
    # ── Consumables ───────────────────────────────────────────────────────────
    "bread": {
        "name": "Буханка хлеба", "type": "consumable",
        "weight": 0.3, "value": 20, "risk_tolerance": 0.3,
        "effects": {"hunger": -35},
        "description": "Простая еда. Утоляет голод на 35 единиц.",
    },
    "canned_food": {
        "name": "Тушёнка", "type": "consumable",
        "weight": 0.4, "value": 40, "risk_tolerance": 0.2,
        "effects": {"hunger": -50, "thirst": 5},
        "description": "Консервы. Хорошо утоляет голод, немного усиливает жажду.",
    },
    "military_ration": {
        "name": "Сухой паёк", "type": "consumable",
        "weight": 0.35, "value": 65, "risk_tolerance": 0.6,
        "effects": {"hunger": -70, "thirst": 10},
        "description": "Военный сухой паёк. Максимально утоляет голод.",
    },
    "water": {
        "name": "Вода (0.5л)", "type": "consumable",
        "weight": 0.5, "value": 30, "risk_tolerance": 0.3,
        "effects": {"thirst": -50},
        "description": "Чистая вода. Утоляет жажду на 50 единиц.",
    },
    "purified_water": {
        "name": "Очищенная вода (1л)", "type": "consumable",
        "weight": 1.0, "value": 70, "risk_tolerance": 0.2,
        "effects": {"thirst": -80},
        "description": "Очищенная вода 1л. Полностью утоляет жажду.",
    },
    "energy_drink": {
        "name": "Энергетик", "type": "consumable",
        "weight": 0.3, "value": 80, "risk_tolerance": 0.6,
        "effects": {"sleepiness": -30, "thirst": -40, "hunger": -10},
        "description": "Энергетический напиток. Снижает усталость на 30, утоляет жажду.",
    },
    "vodka": {
        "name": "Водка", "type": "consumable",
        "weight": 0.5, "value": 50, "risk_tolerance": 0.9,
        "effects": {"radiation": -10, "hp": -5, "thirst": -20},
        "description": "Народное средство от радиации. Снижает радиацию, немного портит здоровье.",
    },
    "glucose": {
        "name": "Раствор глюкозы", "type": "consumable",
        "weight": 0.15, "value": 120, "risk_tolerance": 0.7,
        "effects": {"hp": 15, "hunger": -30},
        "description": "Питательный раствор. Немного лечит и снижает голод на 30 единиц.",
    },
    # ── Secret Documents ──────────────────────────────────────────────────────
    # Rare classified materials scattered across restricted Zone locations.
    # Sought by stalkers pursuing the ``unravel_zone_mystery`` global goal.
    "classified_report": {
        "name": "Секретный отчёт", "type": "secret_document",
        "weight": 0.05, "value": 2000, "risk_tolerance": 0.6,
        "description": "Засекреченный доклад неизвестного ведомства. Содержит данные об аномальной активности в Зоне.",
    },
    "encrypted_disk": {
        "name": "Зашифрованный диск", "type": "secret_document",
        "weight": 0.02, "value": 3500, "risk_tolerance": 0.7,
        "description": "Жёсткий диск с зашифрованными данными о происхождении Зоны. Представляет большую научную ценность.",
    },
    "zone_research_notes": {
        "name": "Исследовательские записки", "type": "secret_document",
        "weight": 0.08, "value": 1200, "risk_tolerance": 0.4,
        "description": "Рукописные заметки учёного, работавшего в Зоне. Содержат наблюдения за аномалиями и мутантами.",
    },
    # ── Detectors ─────────────────────────────────────────────────────────────
    "echo_detector": {
        "name": "Детектор «Эхо»", "type": "detector",
        "weight": 0.5, "value": 500, "risk_tolerance": 0.2,
        "detection_radius": 2,
        "description": "Простой детектор аномалий. Радиус обнаружения 2.",
    },
    "bear_detector": {
        "name": "Детектор «Медведь»", "type": "detector",
        "weight": 0.7, "value": 1500, "risk_tolerance": 0.5,
        "detection_radius": 3,
        "description": "Средний детектор. Надёжно обнаруживает аномалии в радиусе 3.",
    },
    "veles_detector": {
        "name": "Детектор «Велес»", "type": "detector",
        "weight": 0.8, "value": 3000, "risk_tolerance": 0.7,
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
    if v.get("effects", {}).get("hunger", 0) < 0
)

# Items that reduce thirst
DRINK_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items()
    if v.get("effects", {}).get("thirst", 0) < 0
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

# Secret document item types (sought by stalkers with the unravel_zone_mystery goal)
SECRET_DOCUMENT_ITEM_TYPES: frozenset = frozenset(
    k for k, v in ITEM_TYPES.items() if v["type"] == "secret_document"
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
