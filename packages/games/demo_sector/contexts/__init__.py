CONTEXT_DEFINITIONS = [
    {
        "context_type": "sector_map",
        "label": "Strategic Map",
        "description": "10x10 strategic layer with group movement and resource nodes",
        "depth": 0,
        "turn_mode": "strict",
        "map_width": 10,
        "map_height": 10,
        "can_spawn_children": True,
        "child_context_type": "tactical_battle",
    },
    {
        "context_type": "tactical_battle",
        "label": "Tactical Battle",
        "description": "8x8 tactical layer with unit combat",
        "depth": 1,
        "turn_mode": "strict",
        "map_width": 8,
        "map_height": 8,
        "can_spawn_children": False,
        "child_context_type": None,
    },
]
