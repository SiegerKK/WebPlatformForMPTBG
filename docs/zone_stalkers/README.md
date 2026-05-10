# Zone Stalkers — Documentation

Zone Stalkers is a tactical survival simulation game built on top of WebPlatformForMPTBG. NPCs are driven by NPC Brain v3: a deterministic decision pipeline with explicit goals, memory, beliefs, and long-running execution plans.

## Structure

### [`brain_v3/`](./brain_v3/README.md)

Core NPC AI architecture — how the brain works now.

| Document | Description |
|---|---|
| [brain_v3/README.md](./brain_v3/README.md) | Overview and index of brain_v3 documents |
| [brain_v3/mechanics.md](./brain_v3/mechanics.md) | Full decision pipeline, needs, memory, beliefs, objectives, ActivePlan v3 |
| [brain_v3/decision_chain_examples.md](./brain_v3/decision_chain_examples.md) | Reference guide with end-to-end decision chain examples |
| [brain_v3/hunt_search_and_traces.md](./brain_v3/hunt_search_and_traces.md) | HuntLead, TargetBelief, search actions, hunt trace debug map |
| [brain_v3/kill_stalker_goal.md](./brain_v3/kill_stalker_goal.md) | `global_goal = kill_stalker` — target selection, search, engagement |
| [brain_v3/debug_profile_and_map.md](./brain_v3/debug_profile_and_map.md) | NPC profile panels, debug JSON export, hunt trace overlay, zone_debug_delta |

### [`optimization/`](./optimization/README.md)

Network and CPU optimization architecture.

| Document | Description |
|---|---|
| [optimization/README.md](./optimization/README.md) | Overview of implemented and planned optimizations |
| [optimization/network_and_debug_optimization.md](./optimization/network_and_debug_optimization.md) | Implemented: zone_delta, projections, debug subscriptions, on-demand hunt traces |
| [optimization/planned/README.md](./optimization/planned/README.md) | Planned CPU optimization PRs (not yet implemented) |
| [optimization/planned/cpu_pr1_dirty_runtime_foundation.md](./optimization/planned/cpu_pr1_dirty_runtime_foundation.md) | PR plan: TickProfiler, DirtySet, pathfinding cache |
| [optimization/planned/cpu_pr2_copy_on_write_runtime.md](./optimization/planned/cpu_pr2_copy_on_write_runtime.md) | PR plan: remove full deepcopy, copy-on-write state |
| [optimization/planned/cpu_pr3_event_driven_actions_lazy_needs.md](./optimization/planned/cpu_pr3_event_driven_actions_lazy_needs.md) | PR plan: event-driven long actions, lazy needs model |
| [optimization/planned/cpu_pr4_brain_invalidation_ai_budget.md](./optimization/planned/cpu_pr4_brain_invalidation_ai_budget.md) | PR plan: brain invalidation cache, AI decision budget |

### [`future/`](./future/)

Planned gameplay features not yet implemented.

| Document | Description |
|---|---|
| [future/combat_encounter_system.md](./future/combat_encounter_system.md) | Round-based text combat, wounds, advanced hunt tactics, text quest narrative |

### [`../archive/`](../archive/README.md)

Archived documents whose content has been merged into the main docs above.
