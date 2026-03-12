from __future__ import annotations
from typing import Dict, Optional, Type
from packages.core.contracts.game_definition import GameDefinition

class GameRegistry:
    """Registry for game definitions. Game authors register their games here."""

    _instance: Optional["GameRegistry"] = None
    _games: Dict[str, GameDefinition] = {}

    @classmethod
    def get_instance(cls) -> "GameRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, game: GameDefinition) -> None:
        self._games[game.game_id] = game

    def get(self, game_id: str) -> Optional[GameDefinition]:
        return self._games.get(game_id)

    def list_games(self) -> Dict[str, str]:
        return {gid: g.game_version for gid, g in self._games.items()}

    def has(self, game_id: str) -> bool:
        return game_id in self._games


game_registry = GameRegistry.get_instance()
