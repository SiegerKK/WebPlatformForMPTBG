from typing import List, Optional
from app.core.turns.models import TurnMode

class ContextDefinition:
    def __init__(
        self,
        context_type: str,
        display_name: str,
        allowed_actions: List[str],
        turn_mode: TurnMode = TurnMode.STRICT,
        deadline_hours: Optional[int] = None,
        child_context_types: Optional[List[str]] = None,
        ui_schema_ref: str = "",
    ):
        self.context_type = context_type
        self.display_name = display_name
        self.allowed_actions = allowed_actions
        self.turn_mode = turn_mode
        self.deadline_hours = deadline_hours
        self.child_context_types = child_context_types or []
        self.ui_schema_ref = ui_schema_ref
