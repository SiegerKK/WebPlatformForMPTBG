# PR2 — Needs, Liquidity, Resupply

## NeedEvaluationResult contract

`NeedEvaluationResult` is the primary evaluation output for decision/planner integration.

Includes:

- `scores` (survival/material/goal pressures),
- `ImmediateNeed`,
- `ItemNeed`,
- `liquidity_summary`,
- `combat_readiness`.

## ImmediateNeed and ItemNeed

- `ImmediateNeed` handles urgent safety/survival contexts.
- `ItemNeed` models stock/equipment deficits and resupply pressure.
- `affordability_hint` must reflect whether immediate purchase is feasible.

## Liquidity model

Sale candidates are classified as:

- `safe`,
- `risky`,
- `emergency_only`,
- `forbidden`.

Protection rules:

- Do not liquidate equipped weapon/armor.
- Protect compatible ammo required for equipped weapon.
- Protect last food/drink/medicine stock needed for survival.

## Purchase/resupply rules

- Use **cheapest viable survival buy** for urgent restoration.
- Use `reserve_basic` buy mode for food/drink stock refill.
- Prevent unaffordable buy loops.
- If purchase is impossible, use **GET_MONEY fallback**.

## Regression policy rules

- Minor hunger/thirst below soft threshold must not degrade into wait-loop behavior.
- Critical hunger/thirst must override non-critical strategic/economic actions.
- Resupply must not sell protected survival-critical resources for non-critical upgrades.
