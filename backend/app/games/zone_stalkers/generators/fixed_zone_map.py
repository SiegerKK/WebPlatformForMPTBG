"""
Fixed canonical 32-location map for Zone Stalkers.

FIXED_ZONE_LOCATIONS maps location_id → static location descriptor.
Runtime state fields (agents, anomalies, artifacts, items) are NOT included
here; the generator adds them as empty lists when building the world.

Connection format: {"to": str, "travel_time": int}  (travel_time in minutes)
"""

from typing import Dict, Any

FIXED_ZONE_LOCATIONS: Dict[str, Any] = {

    # ─────────────── CORDON (C1-C6) ───────────────────────────────────────────
    "loc_C1": {
        "id": "loc_C1",
        "name": "Военный блокпост",
        "region": "cordon",
        "terrain_type": "plain",
        "anomaly_activity": 1,
        "connections": [
            {"to": "loc_C2", "travel_time": 8},
            {"to": "loc_C4", "travel_time": 11},
            {"to": "loc_S8", "travel_time": 17},   # cross-region
        ],
    },
    "loc_C2": {
        "id": "loc_C2",
        "name": "Деревня новичков",
        "region": "cordon",
        "terrain_type": "plain",
        "anomaly_activity": 0,
        "connections": [
            {"to": "loc_C1", "travel_time": 8},
            {"to": "loc_C3", "travel_time": 9},
            {"to": "loc_C6", "travel_time": 12},
        ],
    },
    "loc_C3": {
        "id": "loc_C3",
        "name": "Элеватор / Мельница",
        "region": "cordon",
        "terrain_type": "industrial",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_C2", "travel_time": 9},
            {"to": "loc_C4", "travel_time": 7},
            {"to": "loc_C5", "travel_time": 8},
        ],
    },
    "loc_C4": {
        "id": "loc_C4",
        "name": "АТП",
        "region": "cordon",
        "terrain_type": "industrial",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_C1", "travel_time": 11},
            {"to": "loc_C3", "travel_time": 7},
            {"to": "loc_C5", "travel_time": 6},
            {"to": "loc_C6", "travel_time": 10},
        ],
    },
    "loc_C5": {
        "id": "loc_C5",
        "name": "Железнодорожный мост",
        "region": "cordon",
        "terrain_type": "industrial",
        "anomaly_activity": 1,
        "connections": [
            {"to": "loc_C3", "travel_time": 8},
            {"to": "loc_C4", "travel_time": 6},
            {"to": "loc_C6", "travel_time": 10},
            {"to": "loc_G1", "travel_time": 18},   # cross-region
        ],
    },
    "loc_C6": {
        "id": "loc_C6",
        "name": "Северная ферма",
        "region": "cordon",
        "terrain_type": "plain",
        "anomaly_activity": 1,
        "connections": [
            {"to": "loc_C2", "travel_time": 12},
            {"to": "loc_C4", "travel_time": 10},
            {"to": "loc_C5", "travel_time": 10},
            {"to": "loc_D5", "travel_time": 20},   # cross-region
        ],
    },

    # ─────────────── GARBAGE / СВАЛКА (G1-G6) ─────────────────────────────────
    "loc_G1": {
        "id": "loc_G1",
        "name": "Барахолка",
        "region": "garbage",
        "terrain_type": "plain",
        "anomaly_activity": 1,
        "connections": [
            {"to": "loc_C5", "travel_time": 18},   # cross-region
            {"to": "loc_G2", "travel_time": 10},
            {"to": "loc_G4", "travel_time": 8},
            {"to": "loc_G5", "travel_time": 9},
        ],
    },
    "loc_G2": {
        "id": "loc_G2",
        "name": "Депо",
        "region": "garbage",
        "terrain_type": "industrial",
        "anomaly_activity": 3,
        "connections": [
            {"to": "loc_G1", "travel_time": 10},
            {"to": "loc_G3", "travel_time": 11},
            {"to": "loc_G4", "travel_time": 8},
            {"to": "loc_G5", "travel_time": 7},
            {"to": "loc_D2", "travel_time": 16},   # cross-region
        ],
    },
    "loc_G3": {
        "id": "loc_G3",
        "name": "Кладбище техники",
        "region": "garbage",
        "terrain_type": "industrial",
        "anomaly_activity": 6,
        "connections": [
            {"to": "loc_G2", "travel_time": 11},
            {"to": "loc_G4", "travel_time": 12},
            {"to": "loc_G6", "travel_time": 10},
        ],
    },
    "loc_G4": {
        "id": "loc_G4",
        "name": "Блокпост Долга",
        "region": "garbage",
        "terrain_type": "industrial",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_G1", "travel_time": 8},
            {"to": "loc_G2", "travel_time": 8},
            {"to": "loc_G3", "travel_time": 12},
            {"to": "loc_G6", "travel_time": 9},
            {"to": "loc_A1", "travel_time": 15},   # cross-region
        ],
    },
    "loc_G5": {
        "id": "loc_G5",
        "name": "Южные бандитские стоянки",
        "region": "garbage",
        "terrain_type": "plain",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_G1", "travel_time": 9},
            {"to": "loc_G2", "travel_time": 7},
            {"to": "loc_G6", "travel_time": 11},
            {"to": "loc_D6", "travel_time": 14},   # cross-region
        ],
    },
    "loc_G6": {
        "id": "loc_G6",
        "name": "Восточный ломовой двор",
        "region": "garbage",
        "terrain_type": "industrial",
        "anomaly_activity": 3,
        "connections": [
            {"to": "loc_G3", "travel_time": 10},
            {"to": "loc_G4", "travel_time": 9},
            {"to": "loc_G5", "travel_time": 11},
        ],
    },

    # ─────────────── AGROPROM (A1-A6) ─────────────────────────────────────────
    "loc_A1": {
        "id": "loc_A1",
        "name": "Восточные железнодорожные ворота",
        "region": "agroprom",
        "terrain_type": "industrial",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_G4", "travel_time": 15},   # cross-region
            {"to": "loc_A2", "travel_time": 7},
            {"to": "loc_A3", "travel_time": 11},
        ],
    },
    "loc_A2": {
        "id": "loc_A2",
        "name": "Завод Агропром",
        "region": "agroprom",
        "terrain_type": "industrial",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_A1", "travel_time": 7},
            {"to": "loc_A3", "travel_time": 10},
            {"to": "loc_A4", "travel_time": 12},
            {"to": "loc_A5", "travel_time": 14},
            {"to": "loc_A6", "travel_time": 13},
        ],
    },
    "loc_A3": {
        "id": "loc_A3",
        "name": "Восточный комплекс НИИ",
        "region": "agroprom",
        "terrain_type": "industrial",
        "anomaly_activity": 4,
        "connections": [
            {"to": "loc_A1", "travel_time": 11},
            {"to": "loc_A2", "travel_time": 10},
            {"to": "loc_A4", "travel_time": 9},
            {"to": "loc_A6", "travel_time": 6},
        ],
    },
    "loc_A4": {
        "id": "loc_A4",
        "name": "Западный комплекс НИИ",
        "region": "agroprom",
        "terrain_type": "industrial",
        "anomaly_activity": 5,
        "connections": [
            {"to": "loc_A2", "travel_time": 12},
            {"to": "loc_A3", "travel_time": 9},
            {"to": "loc_A5", "travel_time": 8},
            {"to": "loc_A6", "travel_time": 5},
        ],
    },
    "loc_A5": {
        "id": "loc_A5",
        "name": "Болото Агропрома",
        "region": "agroprom",
        "terrain_type": "plain",
        "anomaly_activity": 5,
        "connections": [
            {"to": "loc_A2", "travel_time": 14},
            {"to": "loc_A4", "travel_time": 8},
            {"to": "loc_S7", "travel_time": 17},   # cross-region
            {"to": "loc_S8", "travel_time": 15},   # cross-region
        ],
    },
    "loc_A6": {
        "id": "loc_A6",
        "name": "Подземелья Агропрома",
        "region": "agroprom",
        "terrain_type": "industrial",
        "anomaly_activity": 7,
        "connections": [
            {"to": "loc_A2", "travel_time": 13},
            {"to": "loc_A3", "travel_time": 6},
            {"to": "loc_A4", "travel_time": 5},
        ],
    },

    # ─────────────── DARK VALLEY / ТЁМНАЯ ДОЛИНА (D1-D6) ─────────────────────
    "loc_D1": {
        "id": "loc_D1",
        "name": "Старый завод",
        "region": "dark_valley",
        "terrain_type": "buildings",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_D2", "travel_time": 8},
            {"to": "loc_D3", "travel_time": 6},
            {"to": "loc_D4", "travel_time": 9},
            {"to": "loc_D6", "travel_time": 10},
        ],
    },
    "loc_D2": {
        "id": "loc_D2",
        "name": "АЗС",
        "region": "dark_valley",
        "terrain_type": "plain",
        "anomaly_activity": 3,
        "connections": [
            {"to": "loc_D1", "travel_time": 8},
            {"to": "loc_D3", "travel_time": 9},
            {"to": "loc_D5", "travel_time": 10},
            {"to": "loc_G2", "travel_time": 16},   # cross-region
        ],
    },
    "loc_D3": {
        "id": "loc_D3",
        "name": "Тёмная Долина / Завод X18",
        "region": "dark_valley",
        "terrain_type": "buildings",
        "anomaly_activity": 7,
        "connections": [
            {"to": "loc_D1", "travel_time": 6},
            {"to": "loc_D2", "travel_time": 9},
            {"to": "loc_D4", "travel_time": 8},
        ],
    },
    "loc_D4": {
        "id": "loc_D4",
        "name": "Свинарник",
        "region": "dark_valley",
        "terrain_type": "plain",
        "anomaly_activity": 3,
        "connections": [
            {"to": "loc_D1", "travel_time": 9},
            {"to": "loc_D3", "travel_time": 8},
            {"to": "loc_D5", "travel_time": 8},
            {"to": "loc_D6", "travel_time": 12},
        ],
    },
    "loc_D5": {
        "id": "loc_D5",
        "name": "Южная ферма",
        "region": "dark_valley",
        "terrain_type": "plain",
        "anomaly_activity": 1,
        "connections": [
            {"to": "loc_D2", "travel_time": 10},
            {"to": "loc_D4", "travel_time": 8},
            {"to": "loc_D6", "travel_time": 8},
            {"to": "loc_C6", "travel_time": 20},   # cross-region
        ],
    },
    "loc_D6": {
        "id": "loc_D6",
        "name": "Южный блокпост",
        "region": "dark_valley",
        "terrain_type": "hills",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_D1", "travel_time": 10},
            {"to": "loc_D4", "travel_time": 12},
            {"to": "loc_D5", "travel_time": 8},
            {"to": "loc_G5", "travel_time": 14},   # cross-region
        ],
    },

    # ─────────────── SWAMPS / БОЛОТА (S1-S8) ──────────────────────────────────
    "loc_S1": {
        "id": "loc_S1",
        "name": "База Чистого Неба",
        "region": "swamps",
        "terrain_type": "plain",
        "anomaly_activity": 1,
        "connections": [
            {"to": "loc_S2", "travel_time": 12},
            {"to": "loc_S3", "travel_time": 15},
        ],
    },
    "loc_S2": {
        "id": "loc_S2",
        "name": "Насосная станция",
        "region": "swamps",
        "terrain_type": "industrial",
        "anomaly_activity": 3,
        "connections": [
            {"to": "loc_S1", "travel_time": 12},
            {"to": "loc_S3", "travel_time": 9},
            {"to": "loc_S5", "travel_time": 10},
        ],
    },
    "loc_S3": {
        "id": "loc_S3",
        "name": "Машинный двор",
        "region": "swamps",
        "terrain_type": "industrial",
        "anomaly_activity": 4,
        "connections": [
            {"to": "loc_S1", "travel_time": 15},
            {"to": "loc_S2", "travel_time": 9},
            {"to": "loc_S4", "travel_time": 11},
            {"to": "loc_S5", "travel_time": 10},
        ],
    },
    "loc_S4": {
        "id": "loc_S4",
        "name": "Южный хутор",
        "region": "swamps",
        "terrain_type": "plain",
        "anomaly_activity": 3,
        "connections": [
            {"to": "loc_S3", "travel_time": 11},
            {"to": "loc_S5", "travel_time": 10},
            {"to": "loc_S6", "travel_time": 9},
            {"to": "loc_S7", "travel_time": 12},
        ],
    },
    "loc_S5": {
        "id": "loc_S5",
        "name": "Северный хутор",
        "region": "swamps",
        "terrain_type": "plain",
        "anomaly_activity": 3,
        "connections": [
            {"to": "loc_S2", "travel_time": 10},
            {"to": "loc_S3", "travel_time": 10},
            {"to": "loc_S4", "travel_time": 10},
            {"to": "loc_S6", "travel_time": 9},
        ],
    },
    "loc_S6": {
        "id": "loc_S6",
        "name": "Руины деревни",
        "region": "swamps",
        "terrain_type": "buildings",
        "anomaly_activity": 4,
        "connections": [
            {"to": "loc_S4", "travel_time": 9},
            {"to": "loc_S5", "travel_time": 9},
            {"to": "loc_S7", "travel_time": 11},
            {"to": "loc_S8", "travel_time": 13},
        ],
    },
    "loc_S7": {
        "id": "loc_S7",
        "name": "Рыбацкий хутор",
        "region": "swamps",
        "terrain_type": "plain",
        "anomaly_activity": 4,
        "connections": [
            {"to": "loc_S4", "travel_time": 12},
            {"to": "loc_S6", "travel_time": 11},
            {"to": "loc_S8", "travel_time": 9},
            {"to": "loc_A5", "travel_time": 17},   # cross-region
        ],
    },
    "loc_S8": {
        "id": "loc_S8",
        "name": "Северный хутор у болота",
        "region": "swamps",
        "terrain_type": "plain",
        "anomaly_activity": 2,
        "connections": [
            {"to": "loc_S6", "travel_time": 13},
            {"to": "loc_S7", "travel_time": 9},
            {"to": "loc_C1", "travel_time": 17},   # cross-region
            {"to": "loc_A5", "travel_time": 15},   # cross-region
        ],
    },
}
