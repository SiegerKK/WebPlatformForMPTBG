"""
event_rules.py — Rules for the zone_event context (text-quest events).

A zone_event is a child context of zone_map. Multiple players and bots can
participate simultaneously. A Game Master (LLM or fallback) narrates the story
and provides options. Players choose an option; bots choose randomly. After the
event ends every participant receives a memory entry.

Supported commands:
- choose_option(option_index: int)
- leave_event()
"""
import copy
import random
from typing import Any, Dict, List, Tuple

from sdk.rule_set import RuleCheckResult

# Maximum rounds before an event auto-concludes
_DEFAULT_MAX_TURNS = 5


def create_zone_event_state(
    event_id: str,
    title: str,
    description: str,
    location_id: str,
    participant_ids: List[str],  # player / bot IDs (participant_id in zone_map player_agents)
    max_turns: int = _DEFAULT_MAX_TURNS,
) -> Dict[str, Any]:
    """Build the initial state_blob for a new zone_event context."""
    return {
        "context_type": "zone_event",
        "event_id": event_id,
        "title": title,
        "description": description,
        "location_id": location_id,
        "phase": "waiting",          # waiting → active → ended
        "current_turn": 0,
        "max_turns": max_turns,
        "participants": {
            pid: {"player_id": pid, "status": "active", "choice": None}
            for pid in participant_ids
        },
        "narration_history": [],
        "current_narration": "",
        "current_options": [],
        "outcome": None,
        "memory_template": None,     # set by GM after event ends
    }


def validate_event_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> RuleCheckResult:
    if state.get("phase") == "ended":
        return RuleCheckResult(valid=False, error="This event has already ended")

    if player_id not in state.get("participants", {}):
        return RuleCheckResult(valid=False, error="You are not a participant in this event")

    participant = state["participants"][player_id]
    if participant.get("status") == "left":
        return RuleCheckResult(valid=False, error="You have already left this event")

    if command_type == "choose_option":
        if state.get("phase") != "active":
            return RuleCheckResult(valid=False, error="Event is not in the active phase")
        if participant.get("choice") is not None:
            return RuleCheckResult(valid=False, error="You have already made your choice this round")
        option_index = payload.get("option_index")
        if option_index is None:
            return RuleCheckResult(valid=False, error="option_index is required")
        options = state.get("current_options", [])
        if not (0 <= int(option_index) < len(options)):
            return RuleCheckResult(valid=False, error=f"option_index must be 0–{len(options) - 1}")
        return RuleCheckResult(valid=True)

    if command_type == "leave_event":
        return RuleCheckResult(valid=True)

    return RuleCheckResult(valid=False, error=f"Unknown event command: {command_type}")


def resolve_event_command(
    command_type: str,
    payload: Dict[str, Any],
    state: Dict[str, Any],
    player_id: str,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    state = copy.deepcopy(state)
    events: List[Dict[str, Any]] = []

    if command_type == "leave_event":
        state["participants"][player_id]["status"] = "left"
        events.append({
            "event_type": "participant_left_event",
            "payload": {"player_id": player_id, "event_id": state.get("event_id")},
        })
        # If everyone left, end the event
        if _all_left(state):
            state["phase"] = "ended"
            state["outcome"] = "abandoned"
            events.append({"event_type": "event_ended", "payload": {"event_id": state["event_id"], "outcome": "abandoned"}})
        return state, events

    if command_type == "choose_option":
        option_index = int(payload["option_index"])
        options = state.get("current_options", [])
        chosen_text = options[option_index] if option_index < len(options) else f"Option {option_index}"
        state["participants"][player_id]["choice"] = option_index
        events.append({
            "event_type": "option_chosen",
            "payload": {
                "player_id": player_id,
                "event_id": state.get("event_id"),
                "option_index": option_index,
                "option_text": chosen_text,
            },
        })

        # Check if all active participants have chosen
        if _all_active_chose(state):
            advance_evs = _advance_event_turn(state)
            events.extend(advance_evs)

    return state, events


# ─────────────────────────────────────────────────────────────────
# Event phase management
# ─────────────────────────────────────────────────────────────────

def start_event(state: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Transition event from 'waiting' → 'active' and generate opening narration.
    Called by the ticker when conditions are met (enough participants joined).
    """
    state = copy.deepcopy(state)
    events: List[Dict[str, Any]] = []

    from app.games.zone_stalkers.services.llm_gm import get_gm
    gm = get_gm()

    participant_names = [f"Stalker-{pid[:4]}" for pid in state.get("participants", {})]
    narration, options = gm.generate_opening(
        state.get("title", "Zone Event"),
        state.get("description", ""),
        participant_names,
    )

    state["phase"] = "active"
    state["current_turn"] = 1
    state["current_narration"] = narration
    state["current_options"] = options
    state["narration_history"].append({
        "turn": 1,
        "narration": narration,
        "options": options,
        "choices": {},
    })
    # Reset all choices
    for p in state["participants"].values():
        p["choice"] = None

    events.append({
        "event_type": "event_started",
        "payload": {
            "event_id": state.get("event_id"),
            "title": state.get("title"),
            "narration": narration,
            "options": options,
        },
    })
    return state, events


def bot_choose_option(state: Dict[str, Any], bot_player_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Make a random choice for a bot participant."""
    options = state.get("current_options", [])
    if not options:
        return state, []
    idx = random.randrange(len(options))
    return resolve_event_command("choose_option", {"option_index": idx}, state, bot_player_id)


def _advance_event_turn(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """All active participants chose — collect results and generate next round."""
    events: List[Dict[str, Any]] = []

    # Record choices in history
    current_turn = state.get("current_turn", 1)
    hist_entry = next((h for h in state.get("narration_history", []) if h["turn"] == current_turn), None)
    choices_map: Dict[str, str] = {}
    options = state.get("current_options", [])
    for pid, p in state["participants"].items():
        if p.get("status") == "active" and p.get("choice") is not None:
            idx = p["choice"]
            choices_map[pid] = options[idx] if idx < len(options) else f"Option {idx}"
    if hist_entry:
        hist_entry["choices"] = choices_map

    # Build summary string for GM
    choices_summary = "; ".join(f"{pid[:4]}: {txt}" for pid, txt in choices_map.items())

    # Check if max turns reached
    if current_turn >= state.get("max_turns", _DEFAULT_MAX_TURNS):
        _end_event(state, events)
        return events

    # Generate next round narration
    from app.games.zone_stalkers.services.llm_gm import get_gm
    gm = get_gm()
    narration, new_options = gm.generate_next_round(state, choices_summary)

    next_turn = current_turn + 1
    state["current_turn"] = next_turn
    state["current_narration"] = narration
    state["current_options"] = new_options
    state["narration_history"].append({
        "turn": next_turn,
        "narration": narration,
        "options": new_options,
        "choices": {},
    })
    # Reset choices
    for p in state["participants"].values():
        if p.get("status") == "active":
            p["choice"] = None

    events.append({
        "event_type": "event_turn_advanced",
        "payload": {
            "event_id": state.get("event_id"),
            "turn": next_turn,
            "narration": narration,
            "options": new_options,
        },
    })
    return events


def _end_event(state: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    """Conclude the event and build memory entries for all participants."""
    from app.games.zone_stalkers.services.llm_gm import get_gm
    gm = get_gm()

    outcome_text = gm.generate_outcome(state)
    state["phase"] = "ended"
    state["outcome"] = outcome_text
    state["memory_template"] = {
        "type": "event",
        "title": state.get("title", "Zone Event"),
        "summary": outcome_text,
        "effects": {},
    }
    events.append({
        "event_type": "event_ended",
        "payload": {
            "event_id": state.get("event_id"),
            "title": state.get("title"),
            "outcome": outcome_text,
            "participants": list(state["participants"].keys()),
        },
    })


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _all_active_chose(state: Dict[str, Any]) -> bool:
    for p in state.get("participants", {}).values():
        if p.get("status") == "active" and p.get("choice") is None:
            return False
    return True


def _all_left(state: Dict[str, Any]) -> bool:
    for p in state.get("participants", {}).values():
        if p.get("status") != "left":
            return False
    return True
