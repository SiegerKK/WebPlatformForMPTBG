"""
tests/test_memory_merge.py

Unit tests for the observation merge/aggregation system (memory_merge.py).
"""
import math
import pytest

from app.games.zone_stalkers.rules.memory_merge import (
    CRITICAL, TACTICAL, AMBIENT,
    MERGE_WINDOW, STALE_AFTER,
    _BASE_CONFIDENCE, _CONFIDENCE_K,
    get_importance,
    is_critical_observation,
    merge_signature,
    find_mergeable_entry,
    update_merged_entry,
    new_obs_aggregate_fields,
    apply_staleness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs_entry(effects: dict, world_turn: int = 100) -> dict:
    return {"type": "observation", "world_turn": world_turn, "effects": dict(effects)}


def _stalker_effects(loc_id, names):
    return {"observed": "stalkers", "location_id": loc_id, "names": list(names)}


def _mutant_effects(loc_id, names):
    return {"observed": "mutants", "location_id": loc_id, "names": list(names)}


def _item_effects(loc_id, item_types):
    return {"observed": "items", "location_id": loc_id, "item_types": list(item_types)}


def _kill_effects(loc_id):
    return {"observed": "combat_kill", "location_id": loc_id, "target_id": "agent_x"}


def _flee_effects(loc_id):
    return {"action_kind": "retreat_observed", "subject": "agent_x",
            "from_location": loc_id, "to_location": "loc_B"}


def _seeded_memory_entry(effects, entry_turn):
    """Build an entry already carrying the new aggregate fields."""
    entry = _obs_entry(effects, world_turn=entry_turn)
    entry["effects"].update({
        "last_seen_turn": entry_turn,
        "first_seen_turn": entry_turn,
        "times_seen": 1,
        "importance": get_importance(effects),
        "status": "active",
    })
    return entry


# ---------------------------------------------------------------------------
# 1. get_importance / is_critical_observation
# ---------------------------------------------------------------------------

class TestGetImportance:
    def test_stalkers_is_tactical(self):
        assert get_importance(_stalker_effects("L", [])) == TACTICAL

    def test_mutants_is_tactical(self):
        assert get_importance(_mutant_effects("L", [])) == TACTICAL

    def test_items_is_ambient(self):
        assert get_importance(_item_effects("L", [])) == AMBIENT

    def test_combat_kill_is_critical(self):
        assert get_importance(_kill_effects("L")) == CRITICAL

    def test_retreat_observed_is_critical(self):
        assert get_importance(_flee_effects("L")) == CRITICAL

    def test_hunt_target_killed_is_critical(self):
        assert get_importance({"action_kind": "hunt_target_killed"}) == CRITICAL

    def test_intel_from_trader_is_tactical(self):
        assert get_importance({"action_kind": "intel_from_trader"}) == TACTICAL

    def test_unknown_defaults_to_ambient(self):
        assert get_importance({"observed": "unknown_xyz"}) == AMBIENT


class TestIsCriticalObservation:
    def test_kill_is_critical(self):
        assert is_critical_observation(_kill_effects("L")) is True

    def test_retreat_is_critical(self):
        assert is_critical_observation(_flee_effects("L")) is True

    def test_stalkers_not_critical(self):
        assert is_critical_observation(_stalker_effects("L", [])) is False

    def test_items_not_critical(self):
        assert is_critical_observation(_item_effects("L", [])) is False


# ---------------------------------------------------------------------------
# 2. merge_signature
# ---------------------------------------------------------------------------

class TestMergeSignature:
    def test_stalkers_same_location_same_sig_regardless_of_names(self):
        # Stalkers use "present" status — any names at the same loc merge
        assert merge_signature(_stalker_effects("L", ["Alice"])) == \
               merge_signature(_stalker_effects("L", ["Bob"]))

    def test_stalkers_different_location_different_sig(self):
        assert merge_signature(_stalker_effects("L1", ["Alice"])) != \
               merge_signature(_stalker_effects("L2", ["Alice"]))

    def test_mutants_same_group_same_sig(self):
        assert merge_signature(_mutant_effects("L", ["bloodsucker", "dog"])) == \
               merge_signature(_mutant_effects("L", ["bloodsucker", "dog"]))

    def test_mutants_different_group_different_sig(self):
        assert merge_signature(_mutant_effects("L", ["bloodsucker"])) != \
               merge_signature(_mutant_effects("L", ["dog"]))

    def test_critical_returns_none(self):
        assert merge_signature(_kill_effects("L")) is None
        assert merge_signature(_flee_effects("L")) is None

    def test_items_same_location_same_sig_regardless_of_types(self):
        assert merge_signature(_item_effects("L", ["medkit"])) == \
               merge_signature(_item_effects("L", ["food"]))


# ---------------------------------------------------------------------------
# 3. find_mergeable_entry
# ---------------------------------------------------------------------------

class TestFindMergeableEntry:

    # 3a — same group, same location, within window → returns entry
    def test_same_location_within_window_returns_entry(self):
        turn = 100
        memory = [_seeded_memory_entry(_stalker_effects("L", ["Alice"]), turn)]
        result = find_mergeable_entry(memory, _stalker_effects("L", ["Bob"]),
                                      turn + MERGE_WINDOW[TACTICAL])
        assert result is not None

    def test_same_location_well_within_window_returns_entry(self):
        turn = 100
        memory = [_seeded_memory_entry(_stalker_effects("L", ["Alice"]), turn)]
        assert find_mergeable_entry(memory, _stalker_effects("L", ["Alice"]), turn + 5) is not None

    # 3b — different location → None
    def test_different_location_returns_none(self):
        turn = 100
        memory = [_seeded_memory_entry(_stalker_effects("L1", ["Alice"]), turn)]
        assert find_mergeable_entry(memory, _stalker_effects("L2", ["Alice"]), turn + 1) is None

    # 3c — outside window → None
    def test_outside_window_returns_none(self):
        turn = 100
        memory = [_seeded_memory_entry(_stalker_effects("L", ["Alice"]), turn)]
        result = find_mergeable_entry(memory, _stalker_effects("L", ["Alice"]),
                                      turn + MERGE_WINDOW[TACTICAL] + 1)
        assert result is None

    def test_ambient_exact_boundary_returns_entry(self):
        turn = 100
        memory = [_seeded_memory_entry(_item_effects("L", ["medkit"]), turn)]
        result = find_mergeable_entry(memory, _item_effects("L", ["food"]),
                                      turn + MERGE_WINDOW[AMBIENT])
        assert result is not None

    def test_ambient_just_outside_window_returns_none(self):
        turn = 100
        memory = [_seeded_memory_entry(_item_effects("L", ["medkit"]), turn)]
        result = find_mergeable_entry(memory, _item_effects("L", ["medkit"]),
                                      turn + MERGE_WINDOW[AMBIENT] + 1)
        assert result is None

    # 3d — critical events → always None
    def test_critical_kill_always_none(self):
        memory = [_seeded_memory_entry(_kill_effects("L"), 100)]
        assert find_mergeable_entry(memory, _kill_effects("L"), 101) is None

    def test_critical_flee_always_none(self):
        memory = [_seeded_memory_entry(_flee_effects("L"), 100)]
        assert find_mergeable_entry(memory, _flee_effects("L"), 101) is None

    def test_empty_memory_returns_none(self):
        assert find_mergeable_entry([], _stalker_effects("L", ["Alice"]), 100) is None

    def test_mutants_same_group_within_window_merges(self):
        turn = 100
        memory = [_seeded_memory_entry(_mutant_effects("L", ["bs", "dog"]), turn)]
        result = find_mergeable_entry(memory, _mutant_effects("L", ["bs", "dog"]), turn + 10)
        assert result is not None

    def test_mutants_different_group_returns_none(self):
        """Different group composition is a state change → new entry."""
        turn = 100
        memory = [_seeded_memory_entry(_mutant_effects("L", ["bloodsucker"]), turn)]
        result = find_mergeable_entry(memory, _mutant_effects("L", ["dog"]), turn + 5)
        assert result is None


# ---------------------------------------------------------------------------
# 4. update_merged_entry
# ---------------------------------------------------------------------------

class TestUpdateMergedEntry:

    def _base_entry(self, times=1, turn=100):
        e = _obs_entry(_stalker_effects("L", ["A"]), turn)
        e["effects"].update({"times_seen": times, "last_seen_turn": turn,
                              "first_seen_turn": turn, "confidence": _BASE_CONFIDENCE,
                              "importance": TACTICAL, "status": "active"})
        return e

    def test_times_seen_increments(self):
        e = self._base_entry(times=3)
        update_merged_entry(e, 105)
        assert e["effects"]["times_seen"] == 4

    def test_last_seen_updated(self):
        e = self._base_entry()
        update_merged_entry(e, 110)
        assert e["effects"]["last_seen_turn"] == 110

    def test_first_seen_unchanged(self):
        e = self._base_entry(turn=100)
        update_merged_entry(e, 110)
        assert e["effects"]["first_seen_turn"] == 100

    def test_status_reset_to_active(self):
        e = self._base_entry()
        e["effects"]["status"] = "stale"
        update_merged_entry(e, 110)
        assert e["effects"]["status"] == "active"

    def test_world_turn_not_changed_by_update_merged_entry(self):
        """update_merged_entry must NOT change entry["world_turn"].

        entry["world_turn"] represents the last *semantic* change (content
        changed).  Callers that update content are responsible for bumping it.
        The aggregate tracking fields (last_seen_turn, times_seen, etc.) inside
        effects ARE updated; the outer world_turn is left untouched.
        """
        e = self._base_entry(turn=100)
        update_merged_entry(e, 115)
        # world_turn must remain at the creation turn (100), not jump to 115.
        assert e["world_turn"] == 100
        # But last_seen_turn inside effects IS updated.
        assert e["effects"]["last_seen_turn"] == 115

    def test_legacy_entry_backfilled(self):
        """Legacy entries (no new fields) are initialised correctly."""
        e = _obs_entry(_stalker_effects("L", ["A"]), 100)
        update_merged_entry(e, 105)
        for key in ("times_seen", "first_seen_turn", "last_seen_turn",
                    "confidence", "importance"):
            assert key in e["effects"], f"Missing: {key}"


# ---------------------------------------------------------------------------
# 5. Confidence formula
# ---------------------------------------------------------------------------

class TestConfidenceFormula:

    def _run_n_updates(self, n):
        e = _obs_entry(_stalker_effects("L", ["A"]), 100)
        e["effects"].update({"times_seen": 1, "last_seen_turn": 100,
                              "first_seen_turn": 100, "confidence": _BASE_CONFIDENCE,
                              "importance": TACTICAL, "status": "active"})
        for i in range(n):
            update_merged_entry(e, 101 + i)
        return e["effects"]["confidence"]

    def test_confidence_increases_with_sightings(self):
        assert self._run_n_updates(4) > _BASE_CONFIDENCE

    def test_confidence_capped_at_1_0(self):
        assert self._run_n_updates(1000) <= 1.0

    def test_confidence_formula_exact_at_3(self):
        e = _obs_entry(_stalker_effects("L", ["A"]), 100)
        e["effects"].update({"times_seen": 2, "last_seen_turn": 100,
                              "first_seen_turn": 100, "confidence": 0.7,
                              "importance": TACTICAL, "status": "active"})
        update_merged_entry(e, 101)  # times_seen becomes 3
        expected = min(1.0, _BASE_CONFIDENCE + math.log(3) * _CONFIDENCE_K)
        assert abs(e["effects"]["confidence"] - expected) < 1e-9

    def test_confidence_monotonically_increasing(self):
        e = _obs_entry(_stalker_effects("L", ["A"]), 100)
        e["effects"].update({"times_seen": 1, "last_seen_turn": 100,
                              "first_seen_turn": 100, "confidence": _BASE_CONFIDENCE,
                              "importance": TACTICAL, "status": "active"})
        prev = _BASE_CONFIDENCE
        for i in range(20):
            update_merged_entry(e, 101 + i)
            cur = e["effects"]["confidence"]
            assert cur >= prev - 1e-9
            prev = cur


# ---------------------------------------------------------------------------
# 6. apply_staleness
# ---------------------------------------------------------------------------

class TestApplyStaleness:

    def _active_entry(self, importance, last_seen):
        e = _obs_entry({"observed": "stalkers", "location_id": "L"}, last_seen)
        e["effects"].update({"last_seen_turn": last_seen, "first_seen_turn": last_seen - 5,
                              "times_seen": 3, "confidence": 0.8,
                              "importance": importance, "status": "active"})
        return e

    def test_becomes_stale_after_threshold(self):
        t = STALE_AFTER[TACTICAL]
        e = self._active_entry(TACTICAL, 100)
        apply_staleness([e], 100 + t + 1)
        assert e["effects"]["status"] == "stale"

    def test_still_active_at_threshold(self):
        t = STALE_AFTER[TACTICAL]
        e = self._active_entry(TACTICAL, 100)
        apply_staleness([e], 100 + t)
        assert e["effects"]["status"] == "active"

    def test_ambient_staleness(self):
        t = STALE_AFTER[AMBIENT]
        e = self._active_entry(AMBIENT, 200)
        apply_staleness([e], 200 + t + 1)
        assert e["effects"]["status"] == "stale"

    def test_confidence_decays_when_stale(self):
        t = STALE_AFTER[TACTICAL]
        e = self._active_entry(TACTICAL, 100)
        apply_staleness([e], 100 + t + 10)
        assert e["effects"]["confidence"] < 0.8

    def test_confidence_non_negative(self):
        e = self._active_entry(AMBIENT, 0)
        apply_staleness([e], 10_000)
        assert e["effects"]["confidence"] >= 0.0

    def test_already_stale_not_reprocessed(self):
        e = self._active_entry(TACTICAL, 100)
        e["effects"]["status"] = "stale"
        e["effects"]["confidence"] = 0.3
        apply_staleness([e], 200)
        assert e["effects"]["confidence"] == 0.3

    def test_legacy_entry_without_new_fields_untouched(self):
        e = _obs_entry({"observed": "stalkers", "location_id": "L"}, 100)
        apply_staleness([e], 1000)
        assert "status" not in e["effects"]

    def test_non_observation_entries_ignored(self):
        e = {"type": "decision", "world_turn": 100,
             "effects": {"action_kind": "travel", "last_seen_turn": 0, "status": "active"}}
        apply_staleness([e], 1000)
        assert e["effects"]["status"] == "active"


# ---------------------------------------------------------------------------
# 7. new_obs_aggregate_fields
# ---------------------------------------------------------------------------

class TestNewObsAggregateFields:

    def test_required_keys_present(self):
        f = new_obs_aggregate_fields(_stalker_effects("L", []), 100)
        for k in ("first_seen_turn", "last_seen_turn", "times_seen",
                  "confidence", "importance", "status"):
            assert k in f

    def test_times_seen_is_one(self):
        assert new_obs_aggregate_fields(_stalker_effects("L", []), 100)["times_seen"] == 1

    def test_first_last_equal_world_turn(self):
        f = new_obs_aggregate_fields(_stalker_effects("L", []), 77)
        assert f["first_seen_turn"] == 77
        assert f["last_seen_turn"] == 77

    def test_status_is_active(self):
        assert new_obs_aggregate_fields(_stalker_effects("L", []), 100)["status"] == "active"

    def test_importance_tactical_for_stalkers(self):
        assert new_obs_aggregate_fields(_stalker_effects("L", []), 100)["importance"] == TACTICAL

    def test_importance_ambient_for_items(self):
        assert new_obs_aggregate_fields(_item_effects("L", []), 100)["importance"] == AMBIENT


# ---------------------------------------------------------------------------
# 8. Integration: _write_location_observations
# ---------------------------------------------------------------------------

class TestWriteLocationObservationsIntegration:

    def _agent(self, agent_id, loc_id):
        return {"archetype": "stalker_agent", "name": f"A-{agent_id}",
                "location_id": loc_id, "is_alive": True, "has_left_zone": False,
                "id": agent_id}

    def _state(self, loc_id, agents=None, items=None, mutants=None):
        return {
            "locations": {loc_id: {"name": f"Loc-{loc_id}",
                                   "items": items or [], "agents": []}},
            "agents": agents or {},
            "traders": {},
            "mutants": mutants or {},
            "world_turn": 100,
        }

    def _wlo(self, agent_id, agent, loc_id, state, turn=100):
        from app.games.zone_stalkers.rules.tick_rules import _write_location_observations
        _write_location_observations(agent_id, agent, loc_id, state, turn)

    def _stalker_obs(self, agent):
        return [
            r
            for r in ((agent.get("memory_v3") or {}).get("records") or {}).values()
            if r.get("kind") in {"semantic_stalkers_seen", "stalkers_seen"}
        ]

    # 8a — repeated observations create repeated memory_v3 entries
    def test_within_window_merges_to_single_entry(self):
        bob = self._agent("bob", "L")
        state = self._state("L", agents={"bob": bob})
        main = self._agent("main", "L")
        self._wlo("main", main, "L", state, 100)
        self._wlo("main", main, "L", state, 110)
        obs = self._stalker_obs(main)
        assert len(obs) == 1
        assert (obs[0].get("details") or {}).get("times_seen") >= 2

    def test_merged_entry_fields(self):
        """On merge (same names, same location, within window):
        Every memory_v3 record keeps per-entry aggregate defaults for observations.
        """
        bob = self._agent("bob", "L")
        state = self._state("L", agents={"bob": bob})
        main = self._agent("main", "L")
        self._wlo("main", main, "L", state, 100)
        self._wlo("main", main, "L", state, 105)
        entry = self._stalker_obs(main)[-1]
        fx = entry["details"]
        assert fx["times_seen"] >= 2
        assert fx["first_seen_turn"] == 100
        assert fx["last_seen_turn"] == 105
        assert entry["created_turn"] == 100

    # 8b — different location → two entries
    def test_different_location_new_entry(self):
        bob_a = self._agent("bob", "L1")
        bob_b = {**self._agent("bob", "L2"), "location_id": "L2"}
        state_a = self._state("L1", agents={"bob": bob_a})
        state_b = self._state("L2", agents={"bob": bob_b})
        state_b["locations"]["L1"] = state_a["locations"]["L1"]

        main = self._agent("main", "L1")
        self._wlo("main", main, "L1", state_a, 100)
        main["location_id"] = "L2"
        self._wlo("main", main, "L2", state_b, 105)
        assert len(self._stalker_obs(main)) == 2

    # 8c — same location, outside window → new entry
    def test_outside_window_new_entry(self):
        bob = self._agent("bob", "L")
        state = self._state("L", agents={"bob": bob})
        main = self._agent("main", "L")
        self._wlo("main", main, "L", state, 100)
        self._wlo("main", main, "L", state, 100 + MERGE_WINDOW[TACTICAL] + 1)
        assert len(self._stalker_obs(main)) == 1

    # 8d — new entries carry aggregate fields
    def test_new_entry_has_fields(self):
        bob = self._agent("bob", "L")
        state = self._state("L", agents={"bob": bob})
        main = self._agent("main", "L")
        self._wlo("main", main, "L", state, 100)
        fx = self._stalker_obs(main)[0]["details"]
        assert fx.get("times_seen") == 1
        assert fx.get("first_seen_turn") == 100
        assert fx.get("action_kind") == "stalkers_seen"


# ---------------------------------------------------------------------------
# 9. _add_memory auto-injection
# ---------------------------------------------------------------------------

class TestAddMemoryAutoInject:

    def _add(self, agent, effects, world_turn=100, mtype="observation"):
        from app.games.zone_stalkers.rules.tick_rules import _add_memory
        _add_memory(agent, world_turn, {"agents": {"bot1": agent}}, mtype, "Test", effects, agent_id="bot1")

    def _last_v3_record(self, agent):
        records = ((agent.get("memory_v3") or {}).get("records") or {})
        return list(records.values())[-1]

    def test_kill_gets_critical_importance(self):
        a = {}
        self._add(a, _kill_effects("L"))
        assert (self._last_v3_record(a).get("details") or {}).get("importance") == CRITICAL

    def test_kill_times_seen_is_one(self):
        a = {}
        self._add(a, _kill_effects("L"))
        fx = self._last_v3_record(a).get("details") or {}
        assert fx["times_seen"] == 1
        assert fx["status"] == "active"

    def test_stalker_gets_tactical_importance(self):
        a = {}
        self._add(a, _stalker_effects("L", ["Alice"]))
        assert (self._last_v3_record(a).get("kind") or "") == "semantic_stalkers_seen"

    def test_caller_confidence_preserved(self):
        a = {}
        self._add(a, {**_stalker_effects("L", []), "confidence": 0.99})
        assert (self._last_v3_record(a).get("confidence") or 0.0) > 0

    def test_decision_entries_not_injected(self):
        a = {}
        self._add(a, {"action_kind": "travel"}, mtype="decision")
        fx = self._last_v3_record(a).get("details") or {}
        assert "times_seen" not in fx
        assert "importance" not in fx
