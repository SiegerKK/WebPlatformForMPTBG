from app.core.generators.service import WorldGenerator

def test_deterministic_world():
    gen1 = WorldGenerator("test", "1.0", "my-seed")
    gen2 = WorldGenerator("test", "1.0", "my-seed")
    world1 = gen1.generate_world({"width": 5, "height": 5})
    world2 = gen2.generate_world({"width": 5, "height": 5})
    assert world1 == world2

def test_different_seeds():
    gen1 = WorldGenerator("test", "1.0", "seed-a")
    gen2 = WorldGenerator("test", "1.0", "seed-b")
    world1 = gen1.generate_world({"width": 5, "height": 5})
    world2 = gen2.generate_world({"width": 5, "height": 5})
    assert world1["world_seed_value"] != world2["world_seed_value"]

def test_map_generation():
    gen = WorldGenerator("test", "1.0", "map-seed")
    map_data = gen.generate_map({"width": 3, "height": 3})
    assert map_data["width"] == 3
    assert map_data["height"] == 3
    assert len(map_data["tiles"]) == 3
    assert len(map_data["tiles"][0]) == 3
