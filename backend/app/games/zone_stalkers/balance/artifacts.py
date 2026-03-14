"""Artifact type definitions for Zone Stalkers."""

ARTIFACT_TYPES: dict = {
    "soul": {
        "name": "Soul",
        "value": 2000,
        "effects": {"max_hp": 10},
        "radiation_per_turn": 2,
        "anomaly_origin": "gravitational",
    },
    "stone_flower": {
        "name": "Stone Flower",
        "value": 3000,
        "effects": {"defense": 5},
        "radiation_per_turn": 3,
        "anomaly_origin": "chemical",
    },
    "flash": {
        "name": "Flash",
        "value": 1500,
        "effects": {"stamina_regen": 5},
        "radiation_per_turn": 1,
        "anomaly_origin": "electro",
    },
    "fireball": {
        "name": "Fireball",
        "value": 2500,
        "effects": {"fire_resistance": 20},
        "radiation_per_turn": 2,
        "anomaly_origin": "fire",
    },
    "gravi": {
        "name": "Gravi",
        "value": 4000,
        "effects": {"carry_capacity": 10},
        "radiation_per_turn": 4,
        "anomaly_origin": "gravitational",
    },
    "moonlight": {
        "name": "Moonlight",
        "value": 5000,
        "effects": {"radiation_resistance": 30},
        "radiation_per_turn": 1,
        "anomaly_origin": "psi",
    },
    "battery": {
        "name": "Battery",
        "value": 1800,
        "effects": {"stamina": 20},
        "radiation_per_turn": 2,
        "anomaly_origin": "electro",
    },
    "urchin": {
        "name": "Urchin",
        "value": 2200,
        "effects": {"chemical_resistance": 25},
        "radiation_per_turn": 3,
        "anomaly_origin": "chemical",
    },
}
