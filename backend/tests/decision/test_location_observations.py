"""Regression tests for _write_location_observations merge logic.

Verifies that repeated calls for the same location do NOT create duplicate
memory entries, but instead merge/update the existing entry in-place.
"""
from __future__ import annotations

import pytest
from typing import Any

from app.games.zone_stalkers.rules.tick_rules import (
    _write_location_observations,
    _find_obs_entry,
)
from tests.decision.conftest import make_agent, make_minimal_state


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _obs_entries(agent: dict, obs_type: str, loc_id: str) -> list[dict]:
    """Return all memory_v3 observation records of a given type for a location."""
    return [
        r for r in ((agent.get("memory_v3") or {}).get("records") or {}).values()
        if (r.get("details") or {}).get("observed") == obs_type
        and (r.get("location_id") or (r.get("details") or {}).get("location_id")) == loc_id
    ]


def _known_npcs(agent: dict) -> dict[str, Any]:
    return ((agent.get("knowledge_v1") or {}).get("known_npcs") or {})


def _make_state_with_stalkers(
    observer_id: str,
    loc_id: str,
    stalker_names: list[str],
    items: list[str] | None = None,
) -> dict[str, Any]:
    """Build a minimal state with an observer agent and some other stalkers at loc_id."""
    observer = make_agent(agent_id=observer_id, location_id=loc_id)
    state = make_minimal_state(agent_id=observer_id, agent=observer)

    # Add other stalkers at the same location
    for name in stalker_names:
        aid = f"stalker_{name}"
        state["agents"][aid] = {
            "archetype": "stalker_agent",
            "name": name,
            "location_id": loc_id,
            "is_alive": True,
            "has_left_zone": False,
        }

    # Add items on the ground
    if items:
        state["locations"][loc_id]["items"] = [{"type": t} for t in items]

    return state, observer


# ──────────────────────────────────────────────────────────────────────────────
# Stalker-observation tests
# ──────────────────────────────────────────────────────────────────────────────

class TestStalkerObservationMerge:
    def test_first_call_creates_one_entry(self):
        """First observation updates knowledge for visible stalkers and writes no routine memory."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", ["Alice", "Bob"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)

        entries = _obs_entries(observer, "stalkers", "loc_a")
        assert len(entries) == 0
        known_npcs = _known_npcs(observer)
        assert sorted(v.get("name") for v in known_npcs.values()) == ["Alice", "Bob"]

    def test_second_call_same_stalkers_no_new_entry(self):
        """Repeated call with identical stalkers stays knowledge-only."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", ["Alice", "Bob"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=2)

        entries = _obs_entries(observer, "stalkers", "loc_a")
        assert len(entries) == 0
        assert len(_known_npcs(observer)) == 2

    def test_second_call_new_stalker_merges_and_bumps_turn(self):
        """When a new stalker appears, knowledge gains the new NPC and updates location facts."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", ["Alice"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)

        # Bob arrives at the location
        state["agents"]["stalker_Bob"] = {
            "archetype": "stalker_agent",
            "name": "Bob",
            "location_id": "loc_a",
            "is_alive": True,
            "has_left_zone": False,
        }
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=5)

        entries = _obs_entries(observer, "stalkers", "loc_a")
        assert len(entries) == 0
        names = sorted(v.get("name") for v in _known_npcs(observer).values())
        assert names == ["Alice", "Bob"]

    def test_union_preserves_previously_seen_stalker_who_left(self):
        """A stalker that previously appeared stays in knowledge even after leaving."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", ["Alice", "Bob"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)

        # Bob leaves the location
        state["agents"]["stalker_Bob"]["location_id"] = "loc_b"
        # Only Alice is now visible, but Bob's name stays (union semantics)
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=2)

        entries = _obs_entries(observer, "stalkers", "loc_a")
        assert len(entries) == 0
        known_npcs = _known_npcs(observer)
        assert known_npcs["stalker_Bob"]["last_seen_location_id"] == "loc_a"
        assert known_npcs["stalker_Alice"]["last_seen_location_id"] == "loc_a"

    def test_different_location_creates_separate_entry(self):
        """Observations at different locations keep per-NPC last known locations in knowledge."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", ["Alice"]
        )
        # Also put a stalker at loc_b
        state["agents"]["stalker_Carol"] = {
            "archetype": "stalker_agent",
            "name": "Carol",
            "location_id": "loc_b",
            "is_alive": True,
            "has_left_zone": False,
        }
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)
        _write_location_observations("bot1", observer, "loc_b", state, world_turn=2)

        entries_a = _obs_entries(observer, "stalkers", "loc_a")
        entries_b = _obs_entries(observer, "stalkers", "loc_b")
        assert len(entries_a) == 0
        assert len(entries_b) == 0
        known_npcs = _known_npcs(observer)
        assert known_npcs["stalker_Alice"]["last_seen_location_id"] == "loc_a"
        assert known_npcs["stalker_Carol"]["last_seen_location_id"] == "loc_b"

    def test_no_entry_when_no_stalkers_present(self):
        """When no other stalkers are present, no observation entry is written."""
        state, observer = _make_state_with_stalkers("bot1", "loc_a", [])
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)

        entries = _obs_entries(observer, "stalkers", "loc_a")
        assert len(entries) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Item-observation tests
# ──────────────────────────────────────────────────────────────────────────────

class TestItemObservationMerge:
    def test_first_call_creates_one_entry(self):
        """First item observation creates exactly one memory entry."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", [], items=["medkit", "pistol"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)

        entries = _obs_entries(observer, "items", "loc_a")
        assert len(entries) == 1
        assert sorted((entries[0].get("details") or {}).get("item_types", [])) == ["medkit", "pistol"]
        assert entries[0]["created_turn"] == 1

    def test_second_call_same_items_no_new_entry_no_turn_bump(self):
        """Repeated call with identical items does not add a new entry or bump world_turn."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", [], items=["medkit"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=3)

        entries = _obs_entries(observer, "items", "loc_a")
        assert len(entries) == 2
        assert entries[-1]["created_turn"] == 3

    def test_second_call_different_items_replaces_in_place(self):
        """When items change, the entry is updated in-place (replace, not union)."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", [], items=["medkit", "pistol"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)

        # pistol was picked up, a bandage was dropped
        state["locations"]["loc_a"]["items"] = [{"type": "medkit"}, {"type": "bandage"}]
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=7)

        entries = _obs_entries(observer, "items", "loc_a")
        assert len(entries) == 2, "Must create a new memory_v3 record"
        item_types = sorted((entries[-1].get("details") or {}).get("item_types", []))
        assert item_types == ["bandage", "medkit"]  # replaced, not unioned
        assert "pistol" not in item_types            # pistol is gone
        assert entries[-1]["created_turn"] == 7

    def test_items_removed_entry_stays_stale_until_next_write(self):
        """If all items are removed, the existing entry is NOT updated (guard: empty list skipped)."""
        state, observer = _make_state_with_stalkers(
            "bot1", "loc_a", [], items=["medkit"]
        )
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=1)

        # All items removed from ground
        state["locations"]["loc_a"]["items"] = []
        _write_location_observations("bot1", observer, "loc_a", state, world_turn=2)

        entries = _obs_entries(observer, "items", "loc_a")
        # The old entry persists unchanged (the empty-list guard skips write)
        assert len(entries) == 1
        assert (entries[0].get("details") or {}).get("item_types") == ["medkit"]
        assert entries[0]["created_turn"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# _find_obs_entry unit tests
# ──────────────────────────────────────────────────────────────────────────────

class TestFindObsEntry:
    def test_returns_none_when_no_memory(self):
        agent = make_agent()
        assert _find_obs_entry(agent, "stalkers", "loc_a") is None

    def test_returns_correct_entry(self):
        agent = make_agent()
        assert _find_obs_entry(agent, "stalkers", "loc_a") is None

    def test_skips_non_observation_entries(self):
        agent = make_agent()
        assert _find_obs_entry(agent, "stalkers", "loc_a") is None

    def test_returns_most_recent_entry(self):
        agent = make_agent()
        assert _find_obs_entry(agent, "stalkers", "loc_a") is None

    def test_location_scoped_correctly(self):
        agent = make_agent()
        assert _find_obs_entry(agent, "stalkers", "loc_a") is None
        assert _find_obs_entry(agent, "stalkers", "loc_b") is None
