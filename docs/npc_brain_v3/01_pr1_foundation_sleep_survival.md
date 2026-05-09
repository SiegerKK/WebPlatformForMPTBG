# PR1 — Foundation, Sleep, Survival

## Scope

PR1 is the stabilization baseline of NPC behavior under survival and environment pressure.

## Implemented behavior

- Base stabilization of decision/runtime transitions.
- Sleep system with **30-minute intervals**.
- Partial effect for interrupted sleep (progress is not fully lost).
- Sleep completion when `sleepiness = 0`.
- Cleanup of `scheduled_action` / `action_queue` after death.
- Emission warning reaction and safety-first interruption logic.
- Prohibition of sleeping/continuing unsafe actions during emission risk.

## Runtime safety rules

- Critical survival/context conditions can interrupt normal continuation.
- Unsafe sleep continuation is rejected.
- Post-death action state is not allowed to persist.

## Testing focus (PR1)

- Sleep tick progression and completion.
- Interrupted sleep behavior.
- Emission interruption and shelter/wait safety behavior.
- Death cleanup for queued/scheduled runtime actions.

## Explicitly out of PR1

- `memory_v3` architecture and retrieval semantics.
- Objective scoring/selection contracts.
- ActivePlan lifecycle/repair model.
