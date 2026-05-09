# PR3 — Memory v3 and Belief State

## MemoryStore v3

Core entities:

- `MemoryRecord`,
- `MemoryQuery`,
- indexed storage + retrieval scoring.

Memory layers:

- `working`, `episodic`, `semantic`, `spatial`, `social`, `threat`, `goal`.

Indexes:

- `by_layer`, `by_kind`, `by_location`, `by_entity`, `by_item_type`, `by_tag`.

## Legacy bridge and migration behavior

- Live bridge from legacy `_add_memory` into memory_v3.
- Lazy import of old memory.
- Skip transient noise entries (e.g. `sleep_interval_applied`).
- Use real `agent_id` (not display name) in stored records.
- Extract and store `entity_ids` for structured retrieval.
- Hunt intel from social sources must be canonicalized on bridge:
  - `intel_from_trader` → `target_intel`
  - `intel_from_stalker` → `target_intel`
- Canonical `target_intel` keeps target/source entity references, location, confidence, and source tags so target tracking works on the next tick.

## Retrieval and lifecycle

- Retrieval scoring drives ranked memory usage.
- `last_accessed_turn` is updated on read.
- Decay/consolidation keep memory quality and relevance over time.

## BeliefState integration

BeliefState combines world snapshot and memory retrieval into planner-ready context.

Minimal mandatory planner lookups:

- `find_trader`,
- `find_food`,
- `find_water`,
- `avoid_threat`.

Trace requirement:

- `brain_trace.memory_used` must expose retrieved records used for decision/planning.

## Hunt-related storage prerequisites (for future stages)

Memory taxonomy required for later hunt operations:

- `target_seen`,
- `target_last_known_location`,
- `target_not_found`,
- `target_route_observed`,
- `target_equipment_seen`,
- `target_combat_strength_observed`,
- `target_death_confirmed`,
- `target_intel`.

## TargetBelief requirements for hunt intel

- `TargetBelief.last_known_location_id` may be derived from:
  - `target_seen`,
  - `target_last_known_location`,
  - `target_intel`.
- Migration-safe reads should also tolerate legacy/social aliases already present in `memory_v3`:
  - `intel_from_trader`,
  - `intel_from_stalker`.
- Canonical semantics stay distinct:
  - direct observation → `target_seen` / `target_last_known_location`
  - reported intelligence → `target_intel`

## Hunt intel loop prevention invariant

- If social intel already resolved to a target location, the next belief rebuild must expose a non-null `last_known_location_id`.
- After that, objective generation should promote tracking behavior instead of repeating generic intel collection.

> Objective scoring and ActivePlan repair are intentionally out of PR3 scope.
