# NPC Brain v3 — Overview

NPC Brain v3 replaces fragmented, reactive behavior with a deterministic decision chain where goals, constraints, memory, and execution are explicitly modeled.

## Why this system exists

- Stabilize NPC behavior under survival pressure, economy pressure, and long-running goals.
- Separate *why* the NPC chooses something from *how* it executes it.
- Make decisions explainable in logs/debug UI and reproducible in tests.
- Enable robust long operations (ActivePlan v3) and future hunt/kill-target operations.

## Full runtime pipeline

1. **Agent state** (hp/hunger/thirst/sleepiness/radiation/inventory/equipment/location/goal).
2. **NeedEvaluationResult** (`scores`, `ImmediateNeed`, `ItemNeed`, `liquidity_summary`, `combat_readiness`).
3. **MemoryStore v3** ingestion/retrieval/index updates.
4. **BeliefState** assembly from world + memory.
5. **Objective candidates** generation.
6. **ObjectiveDecision** scoring + selection (+ blockers + anti-ping-pong rules).
7. **Objective → Intent adapter**.
8. **Planner** builds executable steps.
9. **ActivePlan v3** (source of truth for long-running execution after PR5).
10. **Trace / memory / debug UI export** (`brain_trace`, profile panels, compact/full JSON).

## Core invariants

- **Objective = reason** for choosing behavior.
- **Intent = execution bridge** from decision layer to planner.
- **Memory v3 = structured, indexed, queryable** state extension.
- **ActivePlan v3 = source of truth** for long actions, continuation, repair, abort (post-PR5 baseline).

## Canonical documents

- [01_pr1_foundation_sleep_survival.md](./01_pr1_foundation_sleep_survival.md)
- [02_pr2_needs_liquidity_resupply.md](./02_pr2_needs_liquidity_resupply.md)
- [03_pr3_memory_beliefs.md](./03_pr3_memory_beliefs.md)
- [04_pr4_objectives_debug_ui.md](./04_pr4_objectives_debug_ui.md)
- [05_pr5_active_plan.md](./05_pr5_active_plan.md)
- [06_final_decision_chain_examples.md](./06_final_decision_chain_examples.md)
- [07_post_pr5_kill_stalker_goal.md](./07_post_pr5_kill_stalker_goal.md)
