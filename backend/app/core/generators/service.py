import random
from typing import List, Dict, Any

class WorldGenerator:
    """Seed-based deterministic world generator"""
    def __init__(self, generator_id: str, version: str, seed: str):
        self.generator_id = generator_id
        self.version = version
        self.seed = seed
        self.rng = random.Random(f"{generator_id}:{version}:{seed}")

    def generate_world(self, config: dict) -> dict:
        width = config.get("width", 10)
        height = config.get("height", 10)
        return {
            "generator_id": self.generator_id,
            "seed": self.seed,
            "version": self.version,
            "width": width,
            "height": height,
            "world_seed_value": self.rng.randint(0, 2**32),
        }

    def generate_map(self, config: dict) -> dict:
        width = config.get("width", 10)
        height = config.get("height", 10)
        tiles = []
        tile_types = ["plains", "forest", "mountain", "water", "desert"]
        for y in range(height):
            row = []
            for x in range(width):
                tile = self.rng.choice(tile_types)
                row.append({"x": x, "y": y, "type": tile})
            tiles.append(row)
        return {"width": width, "height": height, "tiles": tiles}

    def generate_entities(self, config: dict) -> List[dict]:
        count = config.get("count", 5)
        archetype = config.get("archetype", "unit")
        entities = []
        for i in range(count):
            entities.append({
                "archetype": archetype,
                "components": {
                    "position": {"x": self.rng.randint(0, 10), "y": self.rng.randint(0, 10)},
                    "stats": {"hp": self.rng.randint(5, 20), "attack": self.rng.randint(1, 5)},
                },
                "tags": [archetype],
            })
        return entities
