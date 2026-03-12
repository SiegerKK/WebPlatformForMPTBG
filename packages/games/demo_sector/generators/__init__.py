import random
import copy
from typing import Any, Dict, List
from packages.games.demo_sector.balance import (
    STRATEGIC_MAP_WIDTH, STRATEGIC_MAP_HEIGHT, TERRAIN_TYPES,
    RESOURCE_NODES_COUNT, INITIAL_GROUPS_PER_SIDE, GROUP_HP_POOL,
    TACTICAL_MAP_WIDTH, TACTICAL_MAP_HEIGHT,
    OBSTACLES_MIN, OBSTACLES_MAX, COVER_CELLS_MIN, COVER_CELLS_MAX,
    UNITS_PER_GROUP, UNIT_HP,
)


class StrategicMapGenerator:
    generator_id = "strategic_map"

    def generate(self, seed: str, config: Dict[str, Any] = None) -> Dict[str, Any]:
        rng = random.Random(seed)
        config = config or {}
        tiles = {}
        for y in range(STRATEGIC_MAP_HEIGHT):
            for x in range(STRATEGIC_MAP_WIDTH):
                terrain = rng.choice(TERRAIN_TYPES)
                tiles[f"{x},{y}"] = {"x": x, "y": y, "terrain": terrain}
        resource_positions = []
        attempts = 0
        while len(resource_positions) < RESOURCE_NODES_COUNT and attempts < 1000:
            attempts += 1
            x = rng.randint(2, STRATEGIC_MAP_WIDTH - 3)
            y = rng.randint(2, STRATEGIC_MAP_HEIGHT - 3)
            pos = {"x": x, "y": y}
            if pos not in resource_positions:
                resource_positions.append(pos)
        resource_nodes = {}
        resource_types = ["ore", "energy", "data", "fuel"]
        for i, pos in enumerate(resource_positions):
            rtype = resource_types[i % len(resource_types)]
            resource_nodes[f"res_{i}"] = {"id": f"res_{i}", "position": pos, "resource_type": rtype, "controller": None, "value": 1}
        sides = config.get("sides", ["side_a", "side_b"])
        participants = config.get("participants", {})
        groups = {}
        start_positions = {
            "side_a": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
            "side_b": [{"x": 8, "y": 9}, {"x": 9, "y": 9}],
        }
        for side in sides:
            positions = start_positions.get(side, [{"x": 0, "y": 0}])
            owner = participants.get(side, side)
            for i in range(INITIAL_GROUPS_PER_SIDE):
                gid = f"group_{side}_{i}"
                pos = positions[i] if i < len(positions) else {"x": i, "y": 0}
                groups[gid] = {"id": gid, "side": side, "owner_participant_id": owner, "position": pos, "hp_pool": GROUP_HP_POOL, "alive": True}
        return {"tiles": tiles, "resource_nodes": resource_nodes, "groups": groups, "turn": 1, "active_side": sides[0] if sides else "side_a"}

    def get_entities(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        entities = []
        for gid, g in result.get("groups", {}).items():
            entities.append({"archetype_id": "group", "display_name": gid, "components": g, "tags": ["strategic", "group"], "visibility_scope": "public"})
        for rid, rn in result.get("resource_nodes", {}).items():
            entities.append({"archetype_id": "resource_node", "display_name": rid, "components": rn, "tags": ["resource"], "visibility_scope": "public"})
        return entities


class TacticalMapGenerator:
    generator_id = "tactical_map"

    def generate(self, seed: str, config: Dict[str, Any] = None) -> Dict[str, Any]:
        rng = random.Random(seed)
        config = config or {}
        all_positions = [{"x": x, "y": y} for y in range(TACTICAL_MAP_HEIGHT) for x in range(TACTICAL_MAP_WIDTH)]
        used = set()
        def pick_pos():
            while True:
                pos = rng.choice(all_positions)
                key = (pos["x"], pos["y"])
                if key not in used:
                    used.add(key)
                    return dict(pos)
        n_obstacles = rng.randint(OBSTACLES_MIN, OBSTACLES_MAX)
        obstacles = [pick_pos() for _ in range(n_obstacles)]
        n_cover = rng.randint(COVER_CELLS_MIN, COVER_CELLS_MAX)
        cover = [pick_pos() for _ in range(n_cover)]
        sides = config.get("sides", ["side_a", "side_b"])
        participants = config.get("participants", {})
        side_starts = {
            "side_a": [{"x": 0, "y": y} for y in range(UNITS_PER_GROUP)],
            "side_b": [{"x": TACTICAL_MAP_WIDTH - 1, "y": y} for y in range(UNITS_PER_GROUP)],
        }
        units = {}
        for side in sides:
            owner = participants.get(side, side)
            starts = side_starts.get(side, [{"x": 0, "y": i} for i in range(UNITS_PER_GROUP)])
            for i in range(UNITS_PER_GROUP):
                uid = f"unit_{side}_{i}"
                pos = starts[i] if i < len(starts) else {"x": 0, "y": i}
                units[uid] = {"id": uid, "side": side, "owner_participant_id": owner, "position": pos, "hp": UNIT_HP, "alive": True, "has_moved": False, "has_attacked": False}
        unit_ids = list(units.keys())
        rng.shuffle(unit_ids)
        return {"obstacles": obstacles, "cover": cover, "units": units, "initiative_order": unit_ids, "active_unit_id": unit_ids[0] if unit_ids else None, "turn": 1}

    def get_entities(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        entities = []
        for uid, u in result.get("units", {}).items():
            entities.append({"archetype_id": "unit", "display_name": uid, "components": u, "tags": ["tactical", "unit"], "visibility_scope": "public"})
        for obs in result.get("obstacles", []):
            entities.append({"archetype_id": "obstacle", "components": {"position": obs, "blocking": True}, "tags": ["obstacle"], "visibility_scope": "public"})
        for cov in result.get("cover", []):
            entities.append({"archetype_id": "cover", "components": {"position": cov, "cover_bonus": 1}, "tags": ["cover"], "visibility_scope": "public"})
        return entities
