"""Anomaly type definitions for Zone Stalkers."""

ANOMALY_TYPES: dict = {
    "gravitational": {
        "name": "Gravitational Anomaly",
        "damage": 30, "damage_type": "physical",
        "radius": 1, "radiation": 0,
        "artifact_types": ["soul", "gravi"],
    },
    "chemical": {
        "name": "Chemical Anomaly",
        "damage": 15, "damage_type": "chemical",
        "radius": 2, "radiation": 20,
        "artifact_types": ["stone_flower", "urchin"],
    },
    "electro": {
        "name": "Electro Anomaly",
        "damage": 25, "damage_type": "electrical",
        "radius": 1, "radiation": 0,
        "artifact_types": ["flash", "battery"],
    },
    "fire": {
        "name": "Burner Anomaly",
        "damage": 20, "damage_type": "fire",
        "radius": 1, "radiation": 0,
        "artifact_types": ["fireball"],
    },
    "psi": {
        "name": "Psi Emitter",
        "damage": 10, "damage_type": "psi",
        "radius": 3, "radiation": 10,
        "artifact_types": ["moonlight"],
    },
}
