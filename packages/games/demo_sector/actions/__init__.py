ACTION_DEFINITIONS = [
    {
        "action_id": "move_group",
        "context_type": "sector_map",
        "description": "Move a group to an adjacent tile",
        "payload_schema": {
            "group_id": "str",
            "target_position": {"x": "int", "y": "int"},
        },
    },
    {
        "action_id": "end_turn",
        "context_type": "sector_map",
        "description": "End the current player's turn",
        "payload_schema": {},
    },
    {
        "action_id": "inspect_sector",
        "context_type": "sector_map",
        "description": "Inspect a sector tile",
        "payload_schema": {"position": {"x": "int", "y": "int"}},
    },
    {
        "action_id": "select_group",
        "context_type": "sector_map",
        "description": "Select a group for orders",
        "payload_schema": {"group_id": "str"},
    },
    {
        "action_id": "move_unit",
        "context_type": "tactical_battle",
        "description": "Move a unit to a new position",
        "payload_schema": {
            "unit_id": "str",
            "target_position": {"x": "int", "y": "int"},
        },
    },
    {
        "action_id": "attack_unit",
        "context_type": "tactical_battle",
        "description": "Attack an enemy unit",
        "payload_schema": {
            "attacker_id": "str",
            "target_id": "str",
        },
    },
    {
        "action_id": "end_turn",
        "context_type": "tactical_battle",
        "description": "End the current unit's turn",
        "payload_schema": {},
    },
    {
        "action_id": "retreat",
        "context_type": "tactical_battle",
        "description": "Retreat the group from battle",
        "payload_schema": {"group_id": "str"},
    },
]
