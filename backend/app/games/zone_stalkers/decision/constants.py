"""constants — shared constants for the Zone Stalkers decision subsystem.

Centralises values that were previously duplicated across multiple modules
(tick_rules.py, needs.py, intents.py).  Import from here to keep all three
in sync without a single source-of-truth problem.
"""
from __future__ import annotations

# Terrain types where agents are killed / heavily damaged by an emission.
# Must stay in sync with tick_rules._EMISSION_DANGEROUS_TERRAIN (which is kept
# for backwards compat with existing helpers in that module).
EMISSION_DANGEROUS_TERRAIN: frozenset[str] = frozenset({
    "plain", "hills", "swamp", "field_camp", "slag_heaps", "bridge",
})
