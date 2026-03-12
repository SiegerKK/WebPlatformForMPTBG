from abc import ABC, abstractmethod
from typing import List, Optional, TYPE_CHECKING

class GameDefinition(ABC):
    game_id: str
    game_name: str
    version: str

    @abstractmethod
    def register_contexts(self) -> List["ContextDefinition"]: ...

    @abstractmethod
    def register_entities(self) -> List["EntityArchetype"]: ...

    @abstractmethod
    def register_actions(self) -> List["ActionDefinition"]: ...

    @abstractmethod
    def register_rules(self) -> "RuleSet": ...

    @abstractmethod
    def register_generators(self) -> List["GeneratorConfig"]: ...

    @abstractmethod
    def register_ui(self) -> "UISchema": ...

    def get_bot_policy(self) -> Optional["BotPolicy"]:
        return None
