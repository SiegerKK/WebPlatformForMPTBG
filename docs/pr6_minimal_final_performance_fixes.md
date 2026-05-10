# PR 6 — Minimal Final Fixes Before Closing

> Goal:
>
> Do **not** start full optimization work in PR6.
>
> PR6 should be closed as a gameplay/debug PR:
>
> ```text
> HuntLead / TargetBelief search mechanics
> Hunt Traces debug map
> target_found correctness fixes
> basic projection/metrics diagnostics
> ```
>
> Before closing, fix only the most obvious performance/maintenance issue:
>
> ```text
> Do not run heavy state-size/projection diagnostics on every tick.
> ```

---

# 1. Main problem to fix

Currently `tick_match()` records performance metrics and, for `zone_stalkers`, calls:

```python
build_zone_state_size_report(state)
```

on every successful tick.

This is too heavy because `build_zone_state_size_report()` calculates several projections and sizes:

```text
full
game
zone-lite
debug-map
hunt_search bytes
location_hunt_traces bytes
```

Internally this uses deepcopy/json serialization. It is useful for manual diagnostics, but it should not run every tick by default.

This can make performance worse while measuring performance.

---

# 2. Required change A — keep tick metrics cheap

## File

```text
backend/app/core/ticker/service.py
```

## Required behavior

Always record only cheap metrics per tick:

```python
metrics_payload = {
    "tick_total_ms": ...,
    "events_emitted": ...,
    "response_size_bytes": ...,
}
```

Do **not** call `build_zone_state_size_report(state)` on every tick by default.

## Remove/guard this block

The current block that loads the zone context and calls:

```python
metrics_payload.update(build_zone_state_size_report(state))
```

must be guarded or removed from normal tick path.

## Acceptable options

### Option 1 — safest: remove from tick path

Only keep:

```text
tick_total_ms
events_emitted
response_size_bytes
context_id if cheap/easy
```

Full state-size reports remain available via:

```text
GET /zone-stalkers/debug/state-size/{context_id}
```

### Option 2 — debug flag

Only run full report if context state has:

```python
state.get("debug_perf_deep_metrics") is True
```

But this still requires loading state to check the flag, so Option 1 is preferred unless the flag can be read cheaply.

### Option 3 — sample every N ticks

Only run full report every 50 or 100 world turns.

This is acceptable, but for PR6 minimal close, Option 1 is cleaner.

## Recommended

Use Option 1.

---

# 3. Required change B — keep `/debug/state-size` as the heavy manual endpoint

## File

```text
backend/app/games/zone_stalkers/router.py
```

Keep this endpoint:

```text
GET /zone-stalkers/debug/state-size/{context_id}
```

It is the correct place to run:

```python
build_zone_state_size_report(state)
```

This endpoint is manual/debug-only, so heavy projection/deepcopy/json-size work is acceptable there.

---

# 4. Required change C — make projection endpoint size calculation cheap

## Current issue

In:

```text
GET /zone-stalkers/contexts/{context_id}/projection
```

the endpoint currently does:

```python
"projection_size_bytes": build_zone_state_size_report(projected)["state_size_bytes"]
```

This is unnecessarily heavy because it builds a full report for an already projected state.

## Required fix

Add a small helper or reuse internal size helper.

For example, expose from `projections.py`:

```python
def json_size_bytes(payload: Any) -> int:
    return _json_size_bytes(payload)
```

Then in router:

```python
"projection_size_bytes": json_size_bytes(projected)
```

Do not call full `build_zone_state_size_report(projected)` just to get one number.

---

# 5. Required change D — do not implement more optimization in PR6

Do not implement now:

```text
WebSocket delta
static/dynamic map split
frontend migration to projection endpoints
lazy endpoints
copy-on-write state
pathfinding cache
decision invalidation
memory pruning
trace-on-demand
```

Those belong to the next optimization PR.

PR6 should remain focused and closable.

---

# 6. Required change E — update the optimization document status

## File

```text
docs/zone_stalkers_hunt_combat/zone_stalkers_performance_optimization_plan_v2_after_pr6.md
```

Update it so it does not describe already-fixed correctness issues as still open.

Add a short section:

```text
Status after final PR6 fixes:

Completed:
- search_target produces target_found outcome;
- VERIFY_LEAD/TRACK_TARGET/PURSUE_TARGET stop after target_found;
- recently_seen supports ENGAGE_TARGET after fresh contact;
- no_witnesses creates witness_source_exhausted cooldown;
- zero-confidence possible_locations are filtered;
- route_hints ignore exhausted destinations;
- witness_source_exhausted appears in Hunt Traces.

Still deferred to optimization PR:
- move frontend normal flow to game projection;
- debug-map projection adoption;
- static/dynamic split;
- WebSocket delta;
- CPU profiling and deeper runtime optimization.
```

Also clarify:

```text
PR6 contains only diagnostics/projection groundwork, not full optimization.
```

---

# 7. Tests to adjust/add

## 7.1. Projection endpoint should not use full report

If route tests exist, add a small test for helper sizing.

If endpoint tests are inconvenient, add unit test in:

```text
backend/tests/test_zone_stalkers_projections.py
```

Test:

```python
def test_json_size_bytes_returns_positive_size():
    ...
```

And update router code to use that helper.

## 7.2. Full state-size report remains available

Existing tests for `build_zone_state_size_report()` are enough.

## 7.3. Performance metrics remain cheap

Optional test if easy:

```python
def test_tick_metrics_can_record_without_state_size_report():
    ...
```

Not necessary to overbuild.

---

# 8. Acceptance criteria

PR6 can be closed when:

```text
[ ] tick_match no longer calls build_zone_state_size_report on every tick by default.
[ ] /debug/state-size still returns full size report.
[ ] /projection endpoint computes projection_size_bytes without full state-size report.
[ ] Optimization document says correctness gate is completed.
[ ] No large optimization scope was added to PR6.
[ ] Existing backend tests pass.
[ ] Frontend build passes.
```

---

# 9. Expected final state

After this fix:

```text
PR6 remains focused:
  - hunt search mechanics;
  - Hunt Traces debug map;
  - correctness fixes;
  - lightweight projection/metrics groundwork.

The actual optimization starts in the next PR:
  - measure baseline;
  - move frontend to projections;
  - split static/dynamic map;
  - add deltas;
  - optimize CPU.
```

Do not delay PR6 further with large architecture work.
