# Memory Optimization PR 6 — Debug Trace Separation

## Goal

Separate debug trace from gameplay memory and make trace overhead configurable.

Trace should help debugging but must not pollute:

```text
memory_v3
knowledge_v1
hot world state
performance runs
```

## Dependencies

Can be done after PR 1 or after PR 5.

Recommended after PR 1:

```text
Write policy already classifies trace_only events.
```

## Scope

In scope:

```text
1. brain_trace ring buffer.
2. active_plan_trace ring buffer.
3. trace-only event sink.
4. config flags to disable trace in performance mode.
5. debug endpoint/projection for trace.
6. tests for cap and disable behavior.
```

Out of scope:

```text
changing gameplay memory semantics;
storing trace in memory_v3;
long-term analytics/event warehouse;
frontend redesign beyond basic projection.
```

## Trace model

Before cold store:

```json
"debug_trace": {
  "brain": [],
  "active_plan": [],
  "plan_monitor": [],
  "max_entries": 100
}
```

After cold store:

```text
ctx:agent_trace:<context_id>:<agent_id>
```

## Config

Add state/config:

```json
"debug": {
  "brain_trace_enabled": true,
  "active_plan_trace_enabled": true,
  "memory_debug_trace_enabled": false,
  "trace_max_entries": 100
}
```

For performance mode:

```json
"debug": {
  "brain_trace_enabled": false,
  "active_plan_trace_enabled": false,
  "memory_debug_trace_enabled": false
}
```

## Ring buffer helper

```python
def append_agent_trace(
    agent: dict[str, Any],
    *,
    channel: str,
    entry: dict[str, Any],
    state: dict[str, Any],
) -> None:
    if not trace_enabled(state, channel):
        return

    trace = ensure_debug_trace(agent)
    entries = trace.setdefault(channel, [])
    entries.append(_compact_trace_entry(entry))

    max_entries = int(state.get("debug", {}).get("trace_max_entries", 100))
    if len(entries) > max_entries:
        del entries[: len(entries) - max_entries]
```

## Trace entry shape

```json
{
  "turn": 1234,
  "kind": "active_plan_step_started",
  "objective_key": "FIND_ARTIFACTS",
  "intent_kind": "get_rich",
  "summary": "ActivePlan FIND_ARTIFACTS: start step travel_to_location",
  "details": {
    "step_kind": "travel_to_location",
    "plan_id": "..."
  }
}
```

Apply truncation:

```text
summary <= 240 chars
details string <= 160 chars
lists <= 5 items
```

## Event routing

From PR 1 policy:

```text
trace_only → append_agent_trace(...)
memory_aggregate → aggregate memory + optional trace
memory_critical → memory_v3 + optional trace
discard → nothing
```

Do not write trace_only events into memory_v3.

## Projection

Full debug should include:

```json
"debug_trace": {
  "brain": [],
  "active_plan": [],
  "plan_monitor": [],
  "truncated": true
}
```

Lightweight map projection should not include full trace.

## Optional trace endpoint

Optional endpoint:

```text
GET /zone-stalkers/debug/agents/{agent_id}/trace?channel=brain&limit=100
```

If not adding endpoint, full_debug projection is enough for this PR.

## Tests

Add:

```text
backend/tests/decision/v3/test_debug_trace.py
backend/tests/test_zone_stalkers_projections.py
```

Required tests:

```python
def test_trace_only_event_goes_to_trace_not_memory_v3(): ...
def test_trace_ring_buffer_caps_entries(): ...
def test_trace_disabled_drops_entries(): ...
def test_full_debug_includes_trace_when_enabled(): ...
def test_lightweight_projection_does_not_include_full_trace(): ...
def test_trace_entry_payload_is_compacted(): ...
```

## Manual validation

Run:

```text
10 NPC, trace enabled:
  full_debug shows useful trace.

100 NPC, trace disabled:
  trace entries remain empty,
  memory_v3 not polluted,
  performance metrics improve/reduce payload.
```

## Definition of Done

```text
[ ] Trace-only events no longer enter memory_v3.
[ ] Trace is ring-buffered.
[ ] Trace can be disabled.
[ ] Full debug can show trace.
[ ] Performance mode avoids trace overhead.
```
