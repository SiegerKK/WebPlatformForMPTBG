"""Mutant type definitions for Zone Stalkers."""

MUTANT_TYPES: dict = {
    "blind_dog": {
        "name": "Blind Dog",
        "hp": 40, "max_hp": 40,
        "damage": 15, "defense": 0,
        "loot_table": [{"item": "meat", "weight": 80}],
        "aggression": "territorial",
        "move_range": 2,
        "money_drop": 0,
    },
    "flesh": {
        "name": "Flesh",
        "hp": 80, "max_hp": 80,
        "damage": 20, "defense": 5,
        "loot_table": [
            {"item": "meat", "weight": 80},
            {"item": "bandage", "weight": 20},
        ],
        "aggression": "passive",
        "move_range": 1,
        "money_drop": 0,
    },
    "zombie": {
        "name": "Zombie",
        "hp": 60, "max_hp": 60,
        "damage": 10, "defense": 0,
        "loot_table": [
            {"item": "ammo_9mm", "weight": 40},
            {"item": "bandage", "weight": 30},
            {"item": "vodka", "weight": 30},
        ],
        "aggression": "aggressive",
        "move_range": 1,
        "money_drop": 50,
    },
    "bloodsucker": {
        "name": "Bloodsucker",
        "hp": 120, "max_hp": 120,
        "damage": 35, "defense": 10,
        "loot_table": [
            {"item": "medkit", "weight": 30},
            {"item": "ammo_545", "weight": 40},
            {"item": "ak74", "weight": 10},
        ],
        "aggression": "aggressive",
        "move_range": 3,
        "money_drop": 200,
    },
    "controller": {
        "name": "Controller",
        "hp": 100, "max_hp": 100,
        "damage": 20, "defense": 5,
        "loot_table": [
            {"item": "antirad", "weight": 50},
            {"item": "energy_drink", "weight": 50},
        ],
        "aggression": "aggressive",
        "move_range": 2,
        "money_drop": 300,
    },
}
