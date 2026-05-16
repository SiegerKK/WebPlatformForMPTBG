# PR 3 — Location Knowledge Exchange and Location Intel

## Dependency

Requires:
- PR 1: location knowledge table
- PR 2: planner can use known graph

## Goal

Allow NPCs to share and trade location knowledge without copying whole maps or writing high-volume memory records.

NPCs can learn:
- location existence;
- route fragments;
- trader/shelter/exit locations;
- anomaly/artifact rumors;
- danger estimates;
- stale snapshots.

The receiving NPC should learn only what the source NPC knew, with lower confidence and correct timestamps.

---

# Exchange model

Location knowledge exchange must be **budgeted top-K**, not full table copy.

Constants:

```python
MAX_LOCATION_KNOWLEDGE_SHARED_PER_INTERACTION = 5
MAX_LOCATION_EDGES_SHARED_PER_LOCATION = 4
LOCATION_KNOWLEDGE_SHARED_CONFIDENCE_MULTIPLIER = 0.75
LOCATION_KNOWLEDGE_RUMOR_CONFIDENCE_MULTIPLIER = 0.55
```

Never copy 300–600 locations in one conversation.

---

# Share packet

Create compact packet:

```python
{
    "location_id": "loc_A",
    "knowledge_level": "known_snapshot",
    "source": "shared_by_agent",
    "source_agent_id": "agent_debug_1",

    "observed_turn": 12000,
    "received_turn": 13000,
    "confidence": 0.65,

    "snapshot": {
        "location_type": "anomaly_field",
        "danger_level_estimate": 0.7,
        "has_trader": False,
        "has_shelter": False,
        "has_exit": False,
        "artifact_potential_estimate": 0.8,
        "anomaly_risk_estimate": 0.9,
    },

    "edges": {
        "loc_B": {
            "target_location_id": "loc_B",
            "confidence": 0.55,
            "source": "shared_route_fragment",
        }
    }
}
```

Receiver merges via `upsert_known_location(...)`.

---

# Selection policy: what to share

When two NPCs interact, choose top-K relevant location knowledge.

Priority:
1. current goal relevance;
2. shelters if emission risk;
3. known traders if other NPC has survival/economic needs;
4. exits if other NPC has completed goal or debt escape;
5. anomaly/artifact locations if other NPC wants money;
6. target-related locations if conversation is hunt/intel-related;
7. recently visited locations;
8. high confidence and not too stale.

Do not share:
- low-confidence stale rumors unless specifically asked;
- dangerous misleading route as fact;
- all known locations.

---

# Social contexts

Integrate with:

## Trader intel
Trader can sell:
```text
nearest trader route
nearest shelter route
artifact/anomaly rumor
exit route
location existence
route fragment
```

## NPC conversation
NPCs can exchange:
```text
visited location snapshot
known shelter/trader
route fragment
anomaly rumor
```

## Hunt witnesses
Witness reports may include:
```text
target last seen at loc_X
I know loc_X exists
route fragment to loc_X if source knows it
```

---

# Staleness

For shared knowledge:

```python
received_turn = world_turn
observed_turn = source_entry["observed_turn"]
confidence = source_entry["confidence"] * multiplier
```

Do not set `observed_turn = world_turn` unless the source directly observed it now.

This preserves stale information.

---

# Provenance

Each shared entry must include:

```text
source = shared_by_agent / trader_intel / rumor
source_agent_id or trader_id
observed_turn
received_turn
confidence
```

Debug should be able to answer:
```text
"Why does NPC know about loc_X?"
```

---

# Anti-CPU rules

## No full copy

Do not do:

```python
receiver["known_locations"].update(source["known_locations"])
```

## No deep copy of huge table

Copy only selected small packets.

## No all-pair route sharing

Share at most:
- location node facts;
- direct edges from that location;
- optionally a route path of max 5–10 location ids.

## Bounded per-interaction cost

Target:
```text
O(K log N) or O(N) only on interaction, not every tick
K <= 5
N <= 600 source known locations
```

Prefer using indexes from PR4 later.

---

# Mechanics

## Asking about location

Add intent/objective:

```text
ASK_ABOUT_LOCATION
```

Use when:
- target location known by name but route unknown;
- no known trader/shelter/exit;
- hunter has stale lead at unknown location.

## Buying location intel

Add or extend trade/intel:
```text
BUY_LOCATION_INTEL
```

Payload:
```python
{
    "intel_type": "shelter" | "trader" | "exit" | "anomaly" | "route_to_location",
    "target_location_id": optional,
    "max_price": ...,
}
```

Result:
- updates known_locations;
- writes one semantic memory/event, not per-fact spam.

---

# Tests

Create:

```text
backend/tests/knowledge/test_location_knowledge_exchange.py
```

Required tests:

```python
def test_share_location_knowledge_copies_only_top_k(): ...
def test_receiver_gets_stale_observed_turn_not_current_turn(): ...
def test_shared_direct_visit_becomes_hearsay_not_direct_visit_for_receiver(): ...
def test_shared_neighbor_exists_does_not_reveal_full_snapshot(): ...
def test_trader_intel_adds_location_with_trader_source(): ...
def test_hunt_witness_report_includes_location_existence_and_optional_route_fragment(): ...
def test_exchange_does_not_copy_600_locations(): ...
def test_confidence_decays_on_shared_knowledge(): ...
def test_direct_visit_overrides_old_shared_rumor(): ...
```

---

# Acceptance criteria

```text
[ ] NPCs can share limited location knowledge.
[ ] Receiver gets only source's known facts, not true world facts.
[ ] Shared facts preserve stale observed_turn.
[ ] Exchange is top-K bounded.
[ ] No memory spam for each shared location fact.
[ ] Location intel can support trader/shelter/anomaly/hunt planning.
[ ] Direct visit later overrides hearsay.
```
