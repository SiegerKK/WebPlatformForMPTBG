# Optimization Overview

This directory covers Zone Stalkers performance optimizations.

## Implemented

[`network_and_debug_optimization.md`](./network_and_debug_optimization.md) — **Already implemented.** Documents the complete network traffic and debug payload optimization architecture: `zone_delta` WebSocket protocol, projection modes, state revision tracking, debug subscriptions, and on-demand hunt trace loading.

[`cpu_optimization_applied_pr1_pr5.md`](./cpu_optimization_applied_pr1_pr5.md) — **Current canonical CPU optimization document.** Consolidated applied results for PR1–PR5, current architecture, measured impact, remaining bottlenecks, and maintenance rules.

## Archived planning/history

[`archive/`](./archive/) — Historical planning documents and implementation-era PR notes (CPU PR1–PR5), superseded by the consolidated applied document above.
