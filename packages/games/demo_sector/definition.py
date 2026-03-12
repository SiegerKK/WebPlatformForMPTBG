from packages.sdk.game_definition.base import BaseGameDefinition
from packages.games.demo_sector.metadata import (
    GAME_ID, SLUG, VERSION, TITLE, DESCRIPTION, SUPPORTED_MODES, ROOT_CONTEXT_TYPE,
)
from packages.games.demo_sector.contexts import CONTEXT_DEFINITIONS
from packages.games.demo_sector.entities import ENTITY_ARCHETYPES
from packages.games.demo_sector.actions import ACTION_DEFINITIONS
from packages.games.demo_sector.rules import DemoSectorRuleResolver
from packages.games.demo_sector.generators import StrategicMapGenerator, TacticalMapGenerator
from packages.games.demo_sector.bots import StrategicBot, TacticalBot
from packages.games.demo_sector.ui import STRATEGIC_UI_SCHEMA, TACTICAL_UI_SCHEMA


class DemoSectorGame(BaseGameDefinition):
    game_id = GAME_ID
    slug = SLUG
    version = VERSION
    title = TITLE
    description = DESCRIPTION
    supported_modes = SUPPORTED_MODES
    root_context_type = ROOT_CONTEXT_TYPE

    def register_contexts(self):
        return CONTEXT_DEFINITIONS

    def register_entity_archetypes(self):
        return ENTITY_ARCHETYPES

    def register_action_definitions(self):
        return ACTION_DEFINITIONS

    def register_rulesets(self):
        return {
            "sector_map": DemoSectorRuleResolver(context_type="sector_map"),
            "tactical_battle": DemoSectorRuleResolver(context_type="tactical_battle"),
        }

    def register_generators(self):
        return [
            {"id": "strategic_map", "generator": StrategicMapGenerator()},
            {"id": "tactical_map", "generator": TacticalMapGenerator()},
        ]

    def register_ui_schemas(self):
        return {
            "sector_map": STRATEGIC_UI_SCHEMA,
            "tactical_battle": TACTICAL_UI_SCHEMA,
        }

    def register_bots(self):
        return {
            "strategic_bot": StrategicBot(),
            "tactical_bot": TacticalBot(),
        }


demo_sector_game = DemoSectorGame()
