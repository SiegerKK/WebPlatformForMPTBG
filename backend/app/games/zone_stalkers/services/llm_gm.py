"""
Zone Stalkers — LLM Game Master service.

Uses an OpenAI-compatible API to generate narrative text and choice options
for zone_event contexts. Falls back to pre-written content when no API key
is configured.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Fallback narrative tables used when the LLM is unavailable
# ─────────────────────────────────────────────────────────────────

_FALLBACK_EVENTS = [
    {
        "title": "Ambush at the Crossroads",
        "rounds": [
            {
                "narration": (
                    "A group of heavily armed marauders blocks the road ahead. "
                    "Their leader steps forward, rifle raised. "
                    "\"Wallets out, stalkers — or we take 'em ourselves.\""
                ),
                "options": ["Fight back", "Negotiate a toll", "Create a diversion and run"],
            },
            {
                "narration": (
                    "The tension is electric. One of your group slowly reaches for their weapon. "
                    "The marauders tighten their formation. "
                    "You have seconds to act."
                ),
                "options": ["Open fire first", "Raise hands and stall for time", "Throw a grenade"],
            },
            {
                "narration": (
                    "The standoff breaks. Shots ring out across the clearing. "
                    "When the smoke clears, the marauders are retreating — but not without cost."
                ),
                "options": ["Pursue the survivors", "Loot the fallen", "Patch up and move on"],
            },
        ],
        "outcome_good": "You drove off the marauders and secured the road. +200 RU loot.",
        "outcome_bad": "You barely escaped, but the road is clear for now.",
    },
    {
        "title": "The Wounded Stalker",
        "rounds": [
            {
                "narration": (
                    "A stalker sits slumped against a rusted car, clutching a bleeding wound. "
                    "He barely looks up as you approach. "
                    "\"Please... they took everything. Just need a bandage.\""
                ),
                "options": ["Help him with a medkit", "Give him directions and move on", "Search him for loot"],
            },
            {
                "narration": (
                    "He whispers coordinates to a stash he buried last season. "
                    "\"It's yours if you get me to the Bar.\""
                ),
                "options": ["Escort him to safety", "Take the coordinates and leave", "Agree but demand his weapon as payment"],
            },
            {
                "narration": (
                    "Near the Bar checkpoint, a military patrol stops you. "
                    "The wounded stalker freezes. \"Don't tell them who I am.\""
                ),
                "options": ["Cover for him", "Turn him in for a reward", "Create a distraction"],
            },
        ],
        "outcome_good": "The stalker is safe, and you have the stash coordinates.",
        "outcome_bad": "The encounter ended badly. Trust is scarce in the Zone.",
    },
    {
        "title": "Strange Signal",
        "rounds": [
            {
                "narration": (
                    "Your detector begins emitting an unusual rhythmic pulse — "
                    "not an anomaly signature, but something structured. "
                    "The signal is emanating from an old bunker entrance nearby."
                ),
                "options": ["Investigate the bunker", "Ignore it and keep moving", "Try to decode the signal first"],
            },
            {
                "narration": (
                    "Inside the bunker: an ancient terminal still running. "
                    "On screen: fragmented research data and a single blinking question — "
                    "\"AUTHORISE ZONE PURGE? Y/N\""
                ),
                "options": ["Press Y", "Press N", "Destroy the terminal", "Copy the data first"],
            },
            {
                "narration": (
                    "Whatever you chose, the bunker starts shaking. "
                    "Emergency vents blast cold air. You need to leave — NOW."
                ),
                "options": ["Sprint for the exit", "Grab what you can first", "Look for another way out"],
            },
        ],
        "outcome_good": "You escaped with rare data. Scientists at Yantar will pay well.",
        "outcome_bad": "You escaped with your life. That's enough.",
    },
]


class LLMGameMaster:
    """
    Generates GM narration using an LLM API (OpenAI-compatible).
    Falls back to pre-written static content when unavailable.
    """

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.openai.com/v1", model: str = "gpt-4o-mini"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def has_llm(self) -> bool:
        return bool(self._api_key)

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    def generate_opening(self, event_title: str, event_description: str, participant_names: List[str]) -> Tuple[str, List[str]]:
        """Generate the opening narration and initial options for an event."""
        if self.has_llm:
            try:
                return self._llm_opening(event_title, event_description, participant_names)
            except Exception as exc:
                logger.warning("LLM GM error (opening): %s — using fallback", exc)
        return self._fallback_opening(event_title)

    def generate_next_round(self, event_state: Dict[str, Any], choices_summary: str) -> Tuple[str, List[str]]:
        """
        Generate narration for the next round based on participants' choices.
        Returns (narration_text, options_list).
        """
        if self.has_llm:
            try:
                return self._llm_next_round(event_state, choices_summary)
            except Exception as exc:
                logger.warning("LLM GM error (next round): %s — using fallback", exc)
        return self._fallback_next_round(event_state)

    def generate_outcome(self, event_state: Dict[str, Any]) -> str:
        """Generate the final outcome narration for the event."""
        if self.has_llm:
            try:
                return self._llm_outcome(event_state)
            except Exception as exc:
                logger.warning("LLM GM error (outcome): %s — using fallback", exc)
        return self._fallback_outcome(event_state)

    # ─────────────────────────────────────────────────────────────
    # LLM calls (httpx, synchronous)
    # ─────────────────────────────────────────────────────────────

    def _chat(self, messages: List[Dict[str, str]]) -> str:
        import httpx
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": messages,
            "max_tokens": 400,
            "temperature": 0.85,
        }
        resp = httpx.post(f"{self._base_url}/chat/completions", headers=headers, json=body, timeout=20)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    def _system_prompt(self) -> str:
        return (
            "You are the Game Master for a post-apocalyptic text-quest set in the Zone "
            "(inspired by S.T.A.L.K.E.R.). Your style is gritty, atmospheric, and concise. "
            "Always end your response with exactly 3 short action options on separate lines "
            "prefixed with '1. ', '2. ', '3. '. Keep narration under 100 words."
        )

    def _parse_options(self, text: str) -> Tuple[str, List[str]]:
        """Split LLM response into narration + options list."""
        lines = text.strip().split("\n")
        options = []
        narration_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("1. ", "2. ", "3. ", "4. ")):
                options.append(stripped[3:].strip())
            else:
                narration_lines.append(stripped)
        narration = " ".join(l for l in narration_lines if l)
        if not options:
            options = ["Continue", "Be cautious", "Look for another way"]
        return narration, options[:4]

    def _llm_opening(self, title: str, description: str, participant_names: List[str]) -> Tuple[str, List[str]]:
        names = ", ".join(participant_names) if participant_names else "the stalkers"
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": f"Event: {title}\nContext: {description}\nParticipants: {names}\nDescribe the opening scene and offer 3 choices."},
        ]
        raw = self._chat(messages)
        return self._parse_options(raw)

    def _llm_next_round(self, event_state: Dict[str, Any], choices_summary: str) -> Tuple[str, List[str]]:
        history = event_state.get("narration_history", [])
        last_narration = history[-1]["narration"] if history else ""
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": (
                f"Previous scene: {last_narration}\n"
                f"Participants chose: {choices_summary}\n"
                f"Continue the story and offer 3 new choices."
            )},
        ]
        raw = self._chat(messages)
        return self._parse_options(raw)

    def _llm_outcome(self, event_state: Dict[str, Any]) -> str:
        history = event_state.get("narration_history", [])
        summary_lines = [h["narration"][:80] for h in history[-3:]]
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": (
                f"The event is over. Summarise the outcome in 2-3 sentences.\n"
                f"Story so far: {' | '.join(summary_lines)}"
            )},
        ]
        return self._chat(messages)

    # ─────────────────────────────────────────────────────────────
    # Static fallback
    # ─────────────────────────────────────────────────────────────

    def _get_fallback(self, event_state: Dict[str, Any]) -> Dict[str, Any]:
        """Pick or reuse a fallback event template based on event_id seed."""
        event_id = event_state.get("event_id", "")
        idx = abs(hash(event_id)) % len(_FALLBACK_EVENTS)
        return _FALLBACK_EVENTS[idx]

    def _fallback_opening(self, title: str) -> Tuple[str, List[str]]:
        # Find matching or random fallback
        for fe in _FALLBACK_EVENTS:
            if fe["title"] == title:
                r0 = fe["rounds"][0]
                return r0["narration"], r0["options"]
        fe = random.choice(_FALLBACK_EVENTS)
        r0 = fe["rounds"][0]
        return r0["narration"], r0["options"]

    def _fallback_next_round(self, event_state: Dict[str, Any]) -> Tuple[str, List[str]]:
        fe = self._get_fallback(event_state)
        current_round = event_state.get("current_turn", 1)
        rounds = fe["rounds"]
        idx = min(current_round, len(rounds) - 1)
        r = rounds[idx]
        return r["narration"], r["options"]

    def _fallback_outcome(self, event_state: Dict[str, Any]) -> str:
        fe = self._get_fallback(event_state)
        # Simple heuristic: majority chose "fight" → good outcome
        history = event_state.get("narration_history", [])
        total_choices = sum(len(h.get("choices", {})) for h in history)
        if total_choices > 0:
            return fe.get("outcome_good", "The event concluded.")
        return fe.get("outcome_bad", "The event concluded.")


# Module-level singleton, initialised lazily from config
_gm_instance: Optional[LLMGameMaster] = None


def get_gm() -> LLMGameMaster:
    global _gm_instance
    if _gm_instance is None:
        from app.config import settings
        _gm_instance = LLMGameMaster(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model=settings.OPENAI_MODEL,
        )
    return _gm_instance
