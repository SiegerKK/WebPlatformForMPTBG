from abc import ABC, abstractmethod
from typing import List, Tuple, Optional
from pydantic import BaseModel

class RuleCheckResult(BaseModel):
    valid: bool
    error: Optional[str] = None
    events: List[dict] = []

class RuleSet(ABC):
    @abstractmethod
    def validate_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> RuleCheckResult: ...

    @abstractmethod
    def resolve_command(
        self,
        command_type: str,
        payload: dict,
        context_state: dict,
        entities: List[dict],
        player_id: str,
    ) -> Tuple[dict, List[dict]]: ...
