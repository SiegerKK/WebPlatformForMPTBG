# Location Knowledge PR Series — Knowledge-First Zone Exploration

## Goal

Introduce a non-omniscient Zone exploration model for NPCs.

NPCs must not automatically know all locations. Each NPC should know only:

```text
1. locations personally visited;
2. neighboring locations discovered from a visited location;
3. locations learned from other NPCs / traders / intel;
4. stale or partial location facts inherited through knowledge exchange.
```

This must be implemented as **structured knowledge**, not as repeated event-memory records.

The target shape is parallel to existing `knowledge_v1.known_npcs`, but for locations:

```python
agent["knowledge_v1"]["known_locations"]
```

This follows the existing optimization direction: previous docs already describe `knowledge_v1` with `known_npcs`, `known_locations`, `known_traders`, and `known_hazards` as compact structured tables instead of memory spam.

## Why this must be split

This feature touches several hot systems:

```text
knowledge_v1
context_builder
pathfinding
planner/objectives
NPC conversations
intel economy
emission shelter logic
artifact search
debug/performance metrics
```

A single PR would be too risky. Split into 4 PRs:

```text
PR 1 — Location knowledge table and update rules
PR 2 — Known-graph navigation and planner integration
PR 3 — NPC location knowledge exchange and location intel
PR 4 — Performance, debug projections, benchmarks and long-run tests
```

## Performance target

Expected scale:

```text
world locations: 500–1000
known locations per NPC: 300–600
NPC count: ~40 initially, may grow later
```

Approximate hot knowledge entries:

```text
40 NPC * 600 known_locations = 24,000 compact entries
```

This is acceptable only if:
- entries are compact;
- no full location dicts are copied into knowledge;
- no deep copy of all known locations occurs every brain tick;
- pathfinding caches are revision-based;
- knowledge exchange is top-K / budgeted;
- context_builder exposes summaries rather than giant location arrays.

## Core design principles

### 1. Structured table, not event memory

Do not create memory records for every location fact.

Allowed:
```text
semantic high-level story memory:
"Stalker learned about an old bunker from Trader"
```

Not allowed:
```text
memory record for every neighbor
memory record for every location field
memory scan to reconstruct location knowledge each tick
```

### 2. Knowledge levels

A location entry must distinguish:

```text
unknown          — no entry
known_exists     — NPC knows the location exists, but no snapshot
known_route_only — NPC knows an edge/path fragment, but not contents
known_snapshot   — NPC has a stale/partial snapshot
visited          — NPC personally visited and confirmed snapshot
```

### 3. Direct knowledge beats hearsay

Merge priority:

```text
direct_visit > direct_neighbor_observation > trader_intel > witness_report > rumor
newer high-confidence data > older low-confidence data
```

But old direct knowledge may become stale.

### 4. Staleness and confidence

Each fact needs:

```text
observed_turn
received_turn
source
source_agent_id
confidence
stale_after_turn
```

### 5. Compact snapshots

Store projected, gameplay-relevant fields only:

```text
terrain/type
danger estimate
has_trader / trader_id if known
has_shelter
has_exit
anomaly/artifact potential estimate
known neighbor ids
last searched turn / exhausted cooldown
```

Never store:
- full mutable `state["locations"][loc_id]`;
- all agents currently there;
- large transient item lists;
- memory records.

### 6. Two graphs

Separate:

```text
true world graph      — engine/admin/debug only
agent known graph     — planner/pathfinding for NPC decisions
```

NPC planning must use known graph unless the objective explicitly represents exploration of unknown frontier.

---

# PR order

## PR 1 — Location knowledge model and update rules

Add data schema and update logic:
- `ensure_location_knowledge_v1`
- `upsert_known_location`
- direct visit snapshot
- neighbor discovery as `known_exists`
- migration/default spawn knowledge
- compact debug summary

No planner/pathfinding changes yet, except optional passive recording.

## PR 2 — Known-graph navigation and planner integration

Use known graph for:
- route planning;
- trader/shelter selection;
- artifact search;
- emission shelter choice;
- hunt lead reachability.

Add `EXPLORE_FRONTIER` / `GATHER_LOCATION_INTEL` fallbacks when target/trader/shelter is unknown or unreachable in known graph.

## PR 3 — Knowledge exchange and location intel economy

NPCs can exchange location knowledge:
- conversations;
- trader intel;
- bought routes;
- shelter/trader/anomaly rumors;
- limited top-K sharing budget.

No bulk copying of 300–600 location entries per conversation.

## PR 4 — Performance, debug and long-run tests

Add:
- perf metrics;
- context cache integration;
- known graph path cache;
- benchmarks for 500–1000 locations and 40 NPCs;
- debug UI/projection summaries;
- long-run smoke tests.

---

# Expected gameplay impact

This feature improves:

```text
exploration
artifact economy
emission survival
route planning
trader discovery
hunting / target tracking
NPC social information exchange
intel trading
faction/geography asymmetry
```

It also makes deaths from emission or failed hunts more explainable:

```text
NPC did not know a safe shelter
NPC knew a stale route
NPC believed an old anomaly rumor
NPC lacked route knowledge to target location
```

---

# Non-goals for this PR series

Do not implement:
- full fog-of-war rendering for player;
- high-volume story memory for locations;
- global omniscient NPC pathfinding;
- expensive per-tick all-location scoring;
- complex deception / lying system initially;
- probabilistic map geometry changes.

Those can be future work after the location knowledge foundation is stable.
