# NPC Brain v3 — Documentation Index

NPC Brain v3 is the deterministic decision engine powering Zone Stalkers NPCs. It separates *why* an NPC acts (objectives, beliefs) from *how* it acts (active plan, runtime steps), making NPC behavior explainable, testable, and robust under long-running operations.

## Documents

| Document | Description |
|---|---|
| [mechanics.md](./mechanics.md) | Full reference: decision pipeline, needs, MemoryStore v3, BeliefState, objective scoring, ActivePlan v3 lifecycle |
| [decision_chain_examples.md](./decision_chain_examples.md) | Reference guide with runtime flow examples (hunger/thirst, emission, memory routing, E2E kill_stalker, E2E get_rich) |
| [hunt_search_and_traces.md](./hunt_search_and_traces.md) | HuntLead model, TargetBelief with possible_locations, staged target_not_found suppression, look_for_tracks, question_witnesses, hunt trace debug map |
| [kill_stalker_goal.md](./kill_stalker_goal.md) | `global_goal = kill_stalker` mechanics: target selection, intel gathering, tracking, ENGAGE_TARGET, kill confirmation |
| [debug_profile_and_map.md](./debug_profile_and_map.md) | NPC profile panels, full debug JSON export, NPC story export, location hunt traces, zone_debug_delta debug map overlay |

## Core concepts

- **Objective** — *why* the NPC acts. Every behavior is driven by a scored objective.
- **Intent** — internal execution bridge from the decision layer to the planner.
- **MemoryStore v3** — structured, indexed, queryable memory extension.
- **BeliefState** — world snapshot + memory retrieval assembled into planner-ready context.
- **ActivePlan v3** — source of truth for multi-step long-running operations.
