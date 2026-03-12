STRATEGIC_UI_SCHEMA = {
    "context_type": "sector_map",
    "map": {"width": 10, "height": 10, "cell_size": 64, "layers": ["terrain", "resources", "groups", "fog"]},
    "actions": ["move_group", "end_turn", "inspect_sector", "select_group"],
    "panels": ["group_info", "resource_info", "turn_indicator"],
}

TACTICAL_UI_SCHEMA = {
    "context_type": "tactical_battle",
    "map": {"width": 8, "height": 8, "cell_size": 80, "layers": ["terrain", "obstacles", "cover", "units"]},
    "actions": ["move_unit", "attack_unit", "end_turn", "retreat"],
    "panels": ["unit_info", "battle_log", "turn_indicator"],
}
