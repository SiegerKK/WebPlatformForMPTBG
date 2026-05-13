# Zone Stalkers — Memory Optimization PR Sequence for 100 NPC at x600

## Purpose

This document defines the safe PR sequence for implementing the next-generation NPC memory architecture.

Target scenario:

```text
100 NPC
x600 auto-run
long-lived NPCs
memory_v3 saturated at 500 records
high churn from observations/plans/context building/state serialization
```

This sequence intentionally avoids a single huge rewrite. Implement it as separate PRs:

```text
PR 1 — Memory write policy and spam control
PR 2 — Incremental eviction and bounded indexes
PR 3 — Knowledge tables / known_npcs
PR 4 — Context builder cache
PR 5 — Cold memory store
PR 6 — Debug trace separation
PR 7 — Benchmarks and long-run validation
```

## Why this sequence

Current diagnosis:

```text
memory_v3 cap works, but saturated memory still costs CPU;
every new record can trigger indexing, trim, eviction and index rebuild;
by_tag can become wide and expensive;
stalkers_seen can dominate memory;
context_builder may scan/sort memory repeatedly;
memory_v3 lives inside hot world state, so JSON/zlib/Redis/DB pay for it.
```

The strategic direction:

```text
hot state should contain only operational runtime;
knowledge should be structured and updatable;
episodic memory should store only meaningful stories;
debug trace should be separated from gameplay memory;
cold memory should be loaded only when Brain/debug needs it.
```

## Dependency order

```text
PR 1 before PR 2/3:
  It reduces write volume and defines event policy.

PR 2 before PR 5:
  Cold memory must not preserve inefficient index/eviction mechanics.

PR 3 before PR 4:
  Context cache should cache knowledge-based context, not event-spam context.

PR 4 before PR 5 if possible:
  Brain memory access becomes explicit before cold loading.

PR 5 only after memory shape stabilizes:
  Cold store migration is risky and should not be mixed with policy redesign.

PR 6 can be before or after PR 5:
  But trace separation is easier after PR 1 policy exists.
```

## Do not mix with current correctness PRs

If the corpse/survival/target-confirmation PR is still open, do not mix these memory-performance changes into it.

```text
Finish correctness PR first.
Then start Memory Optimization PR 1 from fresh main.
```

## Global hard rules

```text
[ ] Do not reintroduce legacy agent["memory"].
[ ] Do not increase MEMORY_V3_MAX_RECORDS above 500.
[ ] Do not store debug trace as gameplay memory.
[ ] Do not remove meaningful target/corpse/combat/death memories.
[ ] Do not make NPCs forget targets/traders/hazards.
[ ] Do not break npc_history_v2 / full_debug story projection.
[ ] Do not implement cold store before write policy and indexes are stabilized.
```

## Success criteria for the whole sequence

Functional:

```text
[ ] NPC remembers important NPCs and latest known locations.
[ ] NPC can still hunt, verify corpse leads, confirm target death and leave Zone.
[ ] NPC still reacts to enemies, traders, hazards, emissions and corpses.
[ ] Debug UI can show meaningful story history on demand.
```

Performance:

```text
[ ] 100 NPC at x600 does not collapse from memory churn.
[ ] memory write path is bounded.
[ ] by_tag is bounded or removed from persisted hot memory.
[ ] context_builder does not repeatedly scan/sort 500 records per decision.
[ ] hot state size is significantly smaller after cold memory store.
[ ] Redis payload and zlib compression time are reduced.
```

Data shape:

```text
[ ] known_npcs stores latest structured facts per NPC.
[ ] stalkers_seen no longer stores full repeated name lists as primary memory.
[ ] active_plan failures are aggregated.
[ ] memory_summary exists in hot agent state.
[ ] memory_revision and knowledge_revision are maintained.
```

## Recommended branch names

```text
copilot/memory-pr1-write-policy-spam-control
copilot/memory-pr2-incremental-eviction
copilot/memory-pr3-knowledge-tables
copilot/memory-pr4-context-cache
copilot/memory-pr5-cold-store
copilot/memory-pr6-debug-trace
copilot/memory-pr7-benchmarking
```
