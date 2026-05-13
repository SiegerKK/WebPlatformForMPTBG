"""PR3 Knowledge Tables tests."""
from __future__ import annotations
from typing import Any
import pytest
from app.games.zone_stalkers.knowledge.knowledge_store import (
    MAX_DETAILED_KNOWN_NPCS_PER_AGENT,
    MAX_KNOWN_NPCS_PER_AGENT,
    ensure_knowledge_v1,
    upsert_known_npc,
    upsert_known_location,
    upsert_known_trader,
    upsert_known_hazard,
    effective_known_npc_confidence,
    build_knowledge_summary,
)
from app.games.zone_stalkers.memory.memory_events import write_memory_event_to_v3
from app.games.zone_stalkers.memory.store import ensure_memory_v3


def _bare_agent(agent_id: str = "bot1") -> dict[str, Any]:
    return {"name": agent_id, "memory_v3": None, "knowledge_v1": None}


def _stalkers_seen_entry(world_turn, location_id, names, seen_agent_ids):
    return {
        "world_turn": world_turn, "type": "observation", "title": "stalkers",
        "effects": {"observed": "stalkers", "location_id": location_id,
                    "names": names, "seen_agent_ids": seen_agent_ids},
        "summary": "saw stalkers",
    }


def _ku_entry(world_turn, action_kind, **effects):
    return {"world_turn": world_turn, "type": "observation", "title": "test",
            "effects": {"action_kind": action_kind, **effects}, "summary": "test"}


def _count_records(agent):
    return len(ensure_memory_v3(agent).get("records", {}))


def test_ensure_knowledge_v1_creates_structure():
    agent = {}
    k = ensure_knowledge_v1(agent)
    assert isinstance(k, dict)
    for key in ("known_npcs", "known_locations", "known_traders", "known_hazards", "stats"):
        assert key in k
    assert k["revision"] == 0


def test_ensure_knowledge_v1_idempotent():
    agent = {}
    k1 = ensure_knowledge_v1(agent)
    k1["known_npcs"]["npc1"] = {"agent_id": "npc1"}
    k2 = ensure_knowledge_v1(agent)
    assert k2 is k1
    assert "npc1" in k2["known_npcs"]


def test_ensure_knowledge_v1_repairs_missing_subkeys():
    agent = {"knowledge_v1": {"revision": 5}}
    k = ensure_knowledge_v1(agent)
    assert isinstance(k["known_npcs"], dict)
    assert isinstance(k["stats"], dict)
    assert k["revision"] == 5


def test_upsert_known_npc_creates_compact_entry():
    agent = _bare_agent()
    upsert_known_npc(agent, other_agent_id="npc1", name="Stalker 1",
                     location_id="loc_A", world_turn=100,
                     source="direct_observation", confidence=0.9)
    k = agent["knowledge_v1"]
    assert "npc1" in k["known_npcs"]
    e = k["known_npcs"]["npc1"]
    assert e["name"] == "Stalker 1"
    assert e["last_seen_location_id"] == "loc_A"
    assert e["last_seen_turn"] == 100
    assert e["is_alive"] is True
    assert e["detail_level"] == "compact"
    assert e["confidence"] == 0.9


def test_upsert_known_npc_updates_higher_priority():
    agent = _bare_agent()
    upsert_known_npc(agent, other_agent_id="npc1", name="S1",
                     location_id="loc_A", world_turn=100,
                     source="witness_report", confidence=0.5)
    upsert_known_npc(agent, other_agent_id="npc1", name="S1",
                     location_id="loc_B", world_turn=110,
                     source="direct_observation", confidence=0.9)
    e = agent["knowledge_v1"]["known_npcs"]["npc1"]
    assert e["last_seen_location_id"] == "loc_B"
    assert e["source"] == "direct_observation"


def test_upsert_known_npc_does_not_overwrite_location_lower_priority():
    agent = _bare_agent()
    upsert_known_npc(agent, other_agent_id="npc1", name="S1",
                     location_id="loc_B", world_turn=110,
                     source="direct_observation", confidence=0.9)
    upsert_known_npc(agent, other_agent_id="npc1", name="S1",
                     location_id="loc_C", world_turn=120,
                     source="rumor", confidence=0.3)
    e = agent["knowledge_v1"]["known_npcs"]["npc1"]
    assert e["last_seen_location_id"] == "loc_B"
    assert e["last_seen_turn"] == 120


def test_upsert_known_npc_death_status_sets_is_alive_false():
    agent = _bare_agent()
    upsert_known_npc(agent, other_agent_id="npc1", name="S1",
                     location_id="loc_D", world_turn=200,
                     source="corpse_seen", confidence=0.95,
                     death_status={"is_alive": False, "death_cause": "bullet", "killer_id": "npc2"})
    e = agent["knowledge_v1"]["known_npcs"]["npc1"]
    assert e["is_alive"] is False
    assert e["death_cause"] == "bullet"
    assert e["killer_id"] == "npc2"
    assert e["alive_confidence"] <= 0.1


def test_upsert_known_npc_with_observed_agent_creates_detailed():
    agent = _bare_agent()
    observed = {"equipment": {"weapon": {"type": "ak74"}, "armor": {"type": "stalker_suit"}},
                "global_goal": "collect_artifacts"}
    upsert_known_npc(agent, other_agent_id="npc1", name="S1",
                     location_id="loc_A", world_turn=100,
                     source="direct_observation", confidence=0.9,
                     observed_agent=observed)
    e = agent["knowledge_v1"]["known_npcs"]["npc1"]
    assert e["detail_level"] == "detailed"
    assert "equipment_summary" in e
    assert e["equipment_summary"]["weapon_class"] == "rifle"
    assert "artifact_hunter" in e.get("role_hints", [])


def test_known_npc_effective_confidence_decays_without_mutating_state():
    agent = _bare_agent()
    upsert_known_npc(agent, other_agent_id="npc1", name="X",
                     location_id="loc_A", world_turn=0,
                     source="direct_observation", confidence=1.0)
    e = agent["knowledge_v1"]["known_npcs"]["npc1"]
    stored_conf = e["confidence"]

    eff_0 = effective_known_npc_confidence(e, 0)
    eff_half = effective_known_npc_confidence(e, 1440)
    eff_full = effective_known_npc_confidence(e, 2880)

    assert e["confidence"] == stored_conf
    assert e.get("effective_confidence") is None
    assert eff_0 == pytest.approx(1.0, abs=0.01)
    assert eff_half == pytest.approx(0.5, abs=0.01)
    assert eff_full == pytest.approx(0.25, abs=0.01)


def test_stalkers_seen_upserts_known_npcs_without_many_memory_records():
    agent = _bare_agent()
    for turn in range(100, 600, 100):
        entry = _stalkers_seen_entry(turn, "loc_A",
                                     ["Alpha", "Beta", "Gamma"],
                                     ["agent_alpha", "agent_beta", "agent_gamma"])
        write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=entry, world_turn=turn)

    k = agent.get("knowledge_v1", {})
    known = k.get("known_npcs", {})
    assert "agent_alpha" in known
    assert "agent_beta" in known
    assert "agent_gamma" in known
    assert known["agent_alpha"]["last_seen_location_id"] == "loc_A"
    rc = _count_records(agent)
    assert rc <= 10, f"Too many memory records: {rc}"


def test_target_seen_updates_known_npc_location_and_confidence():
    agent = _bare_agent()
    agent["kill_target_id"] = "target_1"

    e1 = _ku_entry(100, "target_seen", target_id="target_1", target_name="Цель",
                   location_id="loc_A", hp=80)
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=e1, world_turn=100)
    k = agent.get("knowledge_v1", {})
    assert "target_1" in k.get("known_npcs", {})
    e = k["known_npcs"]["target_1"]
    assert e["last_seen_location_id"] == "loc_A"
    assert e["confidence"] >= 0.9

    e2 = _ku_entry(200, "target_seen", target_id="target_1", target_name="Цель",
                   location_id="loc_B", hp=60)
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=e2, world_turn=200)
    e2v = k["known_npcs"]["target_1"]
    assert e2v["last_seen_location_id"] == "loc_B"
    assert e2v["last_seen_turn"] == 200


def test_corpse_seen_marks_known_npc_dead():
    agent = _bare_agent()
    upsert_known_npc(agent, other_agent_id="victim_1", name="Сталкер",
                     location_id="loc_A", world_turn=100,
                     source="direct_observation", confidence=0.9)

    e = _ku_entry(200, "corpse_seen",
                  dead_agent_id="victim_1", dead_agent_name="Сталкер",
                  corpse_id="corpse_abc", location_id="loc_B",
                  death_cause="bullet", killer_id="npc_killer",
                  directly_observed=True, confidence=0.95)
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=e, world_turn=200)

    k = agent.get("knowledge_v1", {})
    entry = k.get("known_npcs", {}).get("victim_1")
    assert entry is not None
    assert entry["is_alive"] is False
    assert entry["last_seen_location_id"] == "loc_B"


def test_target_corpse_reported_is_lead_not_goal_completion():
    agent = _bare_agent()
    agent["kill_target_id"] = "target_1"

    e = _ku_entry(100, "target_corpse_reported",
                  target_id="target_1", target_name="Цель",
                  reported_corpse_location_id="loc_D",
                  source_agent_id="witness_x",
                  confidence=0.75, directly_observed=False)
    write_memory_event_to_v3(agent_id="bot1", agent=agent, legacy_entry=e, world_turn=100)

    k = agent.get("knowledge_v1", {})
    entry = k.get("known_npcs", {}).get("target_1")
    assert entry is not None
    assert entry["is_alive"] is False
    assert entry["source"] == "witness_report"

    records = ensure_memory_v3(agent).get("records", {})
    death_confirmed = [r for r in records.values()
                       if isinstance(r, dict) and r.get("kind") == "target_death_confirmed"]
    assert len(death_confirmed) == 0, "target_corpse_reported must not confirm death"


def test_known_npcs_cap_keeps_target_and_recent_enemies():
    agent = _bare_agent()
    agent["kill_target_id"] = "target_1"

    upsert_known_npc(agent, other_agent_id="target_1", name="Kill Target",
                     location_id="loc_X", world_turn=5000,
                     source="direct_observation", confidence=0.95)
    upsert_known_npc(agent, other_agent_id="enemy_1", name="Enemy",
                     location_id="loc_Y", world_turn=4800,
                     source="combat", confidence=0.8, threat_level=0.7)

    for i in range(MAX_KNOWN_NPCS_PER_AGENT + 10):
        upsert_known_npc(agent, other_agent_id=f"neutral_{i}", name=f"N{i}",
                         location_id="loc_A", world_turn=100 + i,
                         source="direct_observation", confidence=0.5)

    known = agent.get("knowledge_v1", {}).get("known_npcs", {})
    assert len(known) <= MAX_KNOWN_NPCS_PER_AGENT
    assert "target_1" in known
    assert "enemy_1" in known


def test_detailed_known_npcs_cap_demotes_neutral_old_entries():
    agent = _bare_agent()
    observed = {"equipment": {"weapon": {"type": "pistol"}, "armor": {"type": "leather_jacket"}}}
    for i in range(MAX_DETAILED_KNOWN_NPCS_PER_AGENT + 5):
        upsert_known_npc(agent, other_agent_id=f"npc_{i}", name=f"NPC {i}",
                         location_id="loc_A", world_turn=100 + i,
                         source="direct_observation", confidence=0.8,
                         observed_agent=observed)

    known = agent.get("knowledge_v1", {}).get("known_npcs", {})
    detailed_count = sum(1 for e in known.values()
                         if isinstance(e, dict) and e.get("detail_level") == "detailed")
    assert detailed_count <= MAX_DETAILED_KNOWN_NPCS_PER_AGENT


def test_upsert_known_location():
    agent = _bare_agent()
    upsert_known_location(agent, location_id="loc_A", name="Bunker",
                          world_turn=100, safe_shelter=True, confidence=1.0)
    loc = agent["knowledge_v1"]["known_locations"]["loc_A"]
    assert loc["name"] == "Bunker"
    assert loc["safe_shelter"] is True
    upsert_known_location(agent, location_id="loc_A", name="Bunker", world_turn=200, confidence=1.0)
    assert agent["knowledge_v1"]["known_locations"]["loc_A"]["last_visited_turn"] == 200


def test_upsert_known_trader():
    agent = _bare_agent()
    upsert_known_trader(agent, trader_id="trader_sidor", location_id="loc_Bar",
                        world_turn=100, name="Сидорович",
                        buys_artifacts=True, sells_food=True)
    trader = agent["knowledge_v1"]["known_traders"]["trader_sidor"]
    assert trader["buys_artifacts"] is True
    assert trader["location_id"] == "loc_Bar"


def test_upsert_known_hazard():
    agent = _bare_agent()
    upsert_known_hazard(agent, location_id="loc_D6", kind="emission_death",
                        world_turn=4016, confidence=0.9)
    hazard = agent["knowledge_v1"]["known_hazards"]["loc_D6:emission_death"]
    assert hazard["kind"] == "emission_death"
    assert hazard["confidence"] == 0.9
    upsert_known_hazard(agent, location_id="loc_D6", kind="emission_death",
                        world_turn=4020, confidence=0.95)
    assert agent["knowledge_v1"]["known_hazards"]["loc_D6:emission_death"]["last_seen_turn"] == 4020


def test_debug_projection_includes_knowledge_summary():
    agent = _bare_agent()
    upsert_known_npc(agent, other_agent_id="npc1", name="Stalker A",
                     location_id="loc_A", world_turn=100,
                     source="direct_observation", confidence=0.9)
    upsert_known_npc(agent, other_agent_id="npc2", name="Stalker B",
                     location_id="loc_B", world_turn=110,
                     source="direct_observation", confidence=0.85)
    upsert_known_trader(agent, trader_id="trader_1", location_id="loc_T",
                        world_turn=100, confidence=1.0)
    upsert_known_hazard(agent, location_id="loc_D", kind="anomaly", world_turn=100)

    summary = build_knowledge_summary(agent, world_turn=100)

    assert summary["known_npcs_count"] == 2
    assert summary["known_traders_count"] == 1
    assert summary["known_hazards_count"] == 1
    assert len(summary["top_recent_known_npcs"]) == 2
    assert summary["top_recent_known_npcs"][0]["agent_id"] == "npc2"
    assert "effective_confidence" in summary["top_recent_known_npcs"][0]
    assert summary["revision"] > 0


def test_debug_projection_empty_for_agent_without_knowledge():
    agent = _bare_agent()
    summary = build_knowledge_summary(agent, world_turn=100)
    assert summary["known_npcs_count"] == 0
    assert summary["known_traders_count"] == 0
    assert summary["revision"] == 0
    assert summary["top_recent_known_npcs"] == []
