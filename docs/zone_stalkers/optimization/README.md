# Optimization Overview

This directory covers Zone Stalkers performance optimizations.

## Implemented

[`network_and_debug_optimization.md`](./network_and_debug_optimization.md) — **Already implemented.** Documents the complete network traffic and debug payload optimization architecture: `zone_delta` WebSocket protocol, projection modes, state revision tracking, debug subscriptions, and on-demand hunt trace loading.

[`implemented/cpu_pr1_dirty_runtime_foundation.md`](./implemented/cpu_pr1_dirty_runtime_foundation.md) — **Implemented.** TickProfiler with CPU section/counter breakdown, TickRuntime / DirtySet tracking, dirty-set based zone delta builder (feature-flagged off by default for safety), brain trace gating for selected agents, and pathfinding/nearest-object cache keyed by map_revision.

## Planned

[`planned/`](./planned/) — CPU reduction PRs (PR2–PR4) that have not yet been implemented. These build on CPU PR1.
