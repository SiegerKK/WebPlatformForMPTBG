"""
decision — NPC Decision Architecture v2 for Zone Stalkers.

Layers:
    Perceive  → context_builder.py    (AgentContext)
    Evaluate  → needs.py              (NeedScores)
    Intend    → intents.py            (Intent)
    Plan      → planner.py            (Plan / PlanStep)
    Act       → executors.py          (execute_plan_step)

Supporting modules:
    bridges.py                  — compatibility bridge to legacy scheduled_action
    debug/explain_intent.py     — human-readable decision explanation
    social/                     — RelationState, DialogueSession (Phase 6)
    groups/                     — GroupState, group needs / planner (Phase 7)
    combat/                     — combat intents / target selection (Phase 8)
"""
