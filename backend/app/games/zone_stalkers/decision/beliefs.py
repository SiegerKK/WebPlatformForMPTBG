"""decision/beliefs.py — BeliefState adapter built from AgentContext + MemoryStore v3.

PR 3: BeliefState is an adapter, NOT a replacement for AgentContext.
AgentContext continues to be the primary context object.  BeliefState
wraps it and enriches lookups with memory_v3 retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.games.zone_stalkers.memory.models import MemoryQuery, MemoryRecord
from app.games.zone_stalkers.memory.retrieval import retrieve_memory
from app.games.zone_stalkers.memory.store import ensure_memory_v3
from .models.agent_context import AgentContext


@dataclass(frozen=True)
class BeliefState:
    """Enriched view of an agent's beliefs built from AgentContext + MemoryStore v3."""

    agent_id: str
    location_id: str
    current_location: dict
    visible_entities: tuple[dict, ...]
    known_traders: tuple[dict, ...]
    known_items: tuple[dict, ...]
    known_threats: tuple[dict, ...]
    relevant_memories: tuple[dict, ...]
    confidence_summary: dict


def build_belief_state(
    ctx: AgentContext,
    agent: dict[str, Any],
    world_turn: int,
) -> BeliefState:
    """Build a BeliefState from AgentContext + memory_v3.

    Parameters
    ----------
    ctx
        Already-built AgentContext for this agent.
    agent
        Raw agent dict (``state["agents"][agent_id]``).
    world_turn
        Current world turn.
    """
    agent_id = ctx.agent_id
    loc_id = ctx.self_state.get("location_id", "")
    current_location = dict(ctx.location_state)

    visible_entities = tuple(dict(e) for e in ctx.visible_entities)

    # ── Known traders (from ctx + memory_v3) ─────────────────────────────────
    known_traders = _build_known_traders(ctx, agent, world_turn)

    # ── Known items (from memory_v3) ─────────────────────────────────────────
    known_items = _build_known_items(agent, world_turn)

    # ── Known threats (from memory_v3 threat layer + ctx hazards) ────────────
    known_threats = _build_known_threats(ctx, agent, world_turn)

    # ── Relevant memories (general retrieval — recent important records) ──────
    relevant_memories = _build_relevant_memories(agent, world_turn)

    # ── Confidence summary ────────────────────────────────────────────────────
    mem_v3 = ensure_memory_v3(agent)
    records = mem_v3.get("records", {})
    active_count  = sum(1 for d in records.values() if d.get("status") == "active")
    stale_count   = sum(1 for d in records.values() if d.get("status") == "stale")
    archived_count = sum(1 for d in records.values() if d.get("status") == "archived")
    confidence_summary = {
        "records_total": len(records),
        "active": active_count,
        "stale": stale_count,
        "archived": archived_count,
    }

    return BeliefState(
        agent_id=agent_id,
        location_id=loc_id,
        current_location=current_location,
        visible_entities=visible_entities,
        known_traders=known_traders,
        known_items=known_items,
        known_threats=known_threats,
        relevant_memories=relevant_memories,
        confidence_summary=confidence_summary,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_known_traders(
    ctx: AgentContext,
    agent: dict[str, Any],
    world_turn: int,
) -> tuple[dict, ...]:
    """Collect traders from AgentContext.known_traders + memory_v3 semantic/spatial."""
    result: list[dict] = []
    seen_ids: set[str] = set()

    # From ctx (already includes visible + legacy memory traders).
    for t in ctx.known_traders:
        tid = t.get("agent_id", "")
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            result.append(dict(t))

    # From memory_v3 (semantic/spatial/episodic layers with "trader" tag).
    mem_records = retrieve_memory(
        agent,
        MemoryQuery(
            purpose="find_trader",
            layers=("semantic", "spatial", "episodic"),
            tags=("trader",),
            max_results=5,
        ),
        world_turn,
    )
    for rec in mem_records:
        trader_id = rec.details.get("trader_id") or rec.details.get("agent_id", "")
        if trader_id and trader_id not in seen_ids:
            seen_ids.add(trader_id)
            result.append({
                "agent_id": trader_id,
                "name": rec.details.get("trader_name", rec.details.get("name", trader_id)),
                "location_id": rec.location_id,
                "source": "memory_v3",
                "memory_id": rec.id,
                "confidence": rec.confidence,
            })

    return tuple(result)


def _build_known_items(
    agent: dict[str, Any],
    world_turn: int,
) -> tuple[dict, ...]:
    """Return known item locations from memory_v3 spatial/episodic layers."""
    records = retrieve_memory(
        agent,
        MemoryQuery(
            purpose="find_items",
            layers=("spatial", "episodic"),
            tags=("item",),
            max_results=10,
        ),
        world_turn,
    )
    return tuple(
        {
            "item_types": list(rec.item_types),
            "location_id": rec.location_id,
            "summary": rec.summary,
            "confidence": rec.confidence,
            "memory_id": rec.id,
        }
        for rec in records
    )


def _build_known_threats(
    ctx: AgentContext,
    agent: dict[str, Any],
    world_turn: int,
) -> tuple[dict, ...]:
    """Combine ctx.known_hazards with memory_v3 threat layer."""
    result: list[dict] = []
    for h in ctx.known_hazards:
        result.append(dict(h))

    records = retrieve_memory(
        agent,
        MemoryQuery(
            purpose="avoid_threat",
            layers=("threat", "spatial"),
            tags=("danger",),
            max_results=10,
        ),
        world_turn,
    )
    for rec in records:
        result.append({
            "kind": rec.kind,
            "location_id": rec.location_id,
            "summary": rec.summary,
            "confidence": rec.confidence,
            "memory_id": rec.id,
        })

    return tuple(result)


def _build_relevant_memories(
    agent: dict[str, Any],
    world_turn: int,
) -> tuple[dict, ...]:
    """Return a handful of high-value recent memories for general context."""
    records = retrieve_memory(
        agent,
        MemoryQuery(
            purpose="general_context",
            max_results=5,
        ),
        world_turn,
    )
    return tuple(
        {
            "id": rec.id,
            "kind": rec.kind,
            "summary": rec.summary,
            "confidence": rec.confidence,
            "layer": rec.layer,
        }
        for rec in records
    )


def find_trader_memory_candidate_from_beliefs(
    belief: BeliefState,
    agent: dict[str, Any],
    world_turn: int,
) -> dict[str, Any] | None:
    """Return best memory-backed trader candidate with metadata for brain_trace."""
    records = retrieve_memory(
        agent,
        MemoryQuery(
            purpose="find_trader",
            layers=("semantic", "spatial", "episodic"),
            tags=("trader", "trade"),
            max_results=5,
        ),
        world_turn,
    )
    for rec in records:
        if rec.location_id:
            return {
                "location_id": rec.location_id,
                "memory_id": rec.id,
                "kind": rec.kind,
                "summary": rec.summary,
                "confidence": rec.confidence,
                "used_for": "find_trader",
            }
    return None


def find_trader_location_from_beliefs(
    belief: BeliefState,
    agent: dict[str, Any],
    world_turn: int,
) -> str | None:
    """Return the location_id of the best-known trader, or None."""
    candidate = find_trader_memory_candidate_from_beliefs(belief, agent, world_turn)
    if candidate:
        return candidate.get("location_id")

    if not belief.known_traders:
        return None
    sorted_traders = sorted(
        belief.known_traders,
        key=lambda t: float(t.get("confidence", 0.0)),
        reverse=True,
    )
    for trader in sorted_traders:
        loc = trader.get("location_id")
        if loc:
            return loc
    return None


def find_water_memory_candidate_from_beliefs(
    belief: BeliefState,
    agent: dict[str, Any],
    world_turn: int,
) -> dict[str, Any] | None:
    """Return memory-backed water source candidate with metadata."""
    from app.games.zone_stalkers.balance.items import DRINK_ITEM_TYPES
    records = retrieve_memory(
        agent,
        MemoryQuery(
            purpose="find_water",
            layers=("spatial", "episodic"),
            tags=("drink", "water", "item"),
            item_types=tuple(DRINK_ITEM_TYPES),
            max_results=10,
        ),
        world_turn,
    )
    for rec in records:
        if rec.location_id:
            return {
                "location_id": rec.location_id,
                "memory_id": rec.id,
                "kind": rec.kind,
                "summary": rec.summary,
                "confidence": rec.confidence,
                "used_for": "find_water",
            }
    return None


def find_water_source_from_beliefs(
    belief: BeliefState,
    agent: dict[str, Any],
    world_turn: int,
) -> str | None:
    """Return location_id with known water/drink from memory_v3, or None."""
    candidate = find_water_memory_candidate_from_beliefs(belief, agent, world_turn)
    return candidate.get("location_id") if candidate else None


def find_food_memory_candidate_from_beliefs(
    belief: BeliefState,
    agent: dict[str, Any],
    world_turn: int,
) -> dict[str, Any] | None:
    """Return memory-backed food source candidate with metadata."""
    from app.games.zone_stalkers.balance.items import FOOD_ITEM_TYPES
    records = retrieve_memory(
        agent,
        MemoryQuery(
            purpose="find_food",
            layers=("spatial", "episodic"),
            tags=("food", "item"),
            item_types=tuple(FOOD_ITEM_TYPES),
            max_results=10,
        ),
        world_turn,
    )
    for rec in records:
        if rec.location_id:
            return {
                "location_id": rec.location_id,
                "memory_id": rec.id,
                "kind": rec.kind,
                "summary": rec.summary,
                "confidence": rec.confidence,
                "used_for": "find_food",
            }
    return None


def find_food_source_from_beliefs(
    belief: BeliefState,
    agent: dict[str, Any],
    world_turn: int,
) -> str | None:
    """Return location_id with known food from memory_v3, or None."""
    candidate = find_food_memory_candidate_from_beliefs(belief, agent, world_turn)
    return candidate.get("location_id") if candidate else None
