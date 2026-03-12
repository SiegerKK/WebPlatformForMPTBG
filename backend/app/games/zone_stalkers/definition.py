"""
Zone Stalkers — GameDefinition.

Registers all contexts, entities, actions, rules, generators and UI schema.
"""
from typing import List

from sdk.game_definition import GameDefinition
from sdk.context_definition import ContextDefinition
from sdk.entity_archetype import EntityArchetype, ComponentSchema
from sdk.action_definition import ActionDefinition
from sdk.ui_schema import UISchema, UIPrimitive, UIPrimitiveType
from sdk.bot_policy import BotPolicy
from app.core.turns.models import TurnMode

from app.games.zone_stalkers.ruleset import ZoneStalkerRuleSet
from app.games.zone_stalkers.bots.fallback_player_bot import FallbackPlayerBotPolicy


def _cs(name: str, required: bool = False) -> ComponentSchema:
    return ComponentSchema(name=name, required=required)


class ZoneStalkersGame(GameDefinition):
    game_id = "zone_stalkers"
    game_name = "Zone Stalkers"
    version = "1.0.0"

    # ──────────────────────────────
    # Contexts
    # ──────────────────────────────

    def register_contexts(self) -> List[ContextDefinition]:
        return [
            ContextDefinition(
                context_type="zone_map",
                display_name="Zone Map",
                allowed_actions=[
                    "move_agent",
                    "travel",
                    "explore_location",
                    "sleep",
                    "join_event",
                    "pick_up_artifact",
                    "pick_up_item",
                    "end_turn",
                ],
                turn_mode=TurnMode.SIMULTANEOUS,
                deadline_hours=24,
                child_context_types=[
                    "location_exploration",
                    "encounter_combat",
                    "trade_session",
                    "zone_event",
                ],
                ui_schema_ref="zone_map_ui",
            ),
            ContextDefinition(
                context_type="location_exploration",
                display_name="Location Exploration",
                allowed_actions=[
                    "explore_move",
                    "pick_up_item",
                    "interact",
                    "leave_location",
                    "end_turn",
                ],
                turn_mode=TurnMode.STRICT,
                deadline_hours=1,
                ui_schema_ref="location_exploration_ui",
            ),
            ContextDefinition(
                context_type="encounter_combat",
                display_name="Encounter Combat",
                allowed_actions=[
                    "attack",
                    "use_item",
                    "retreat",
                    "end_turn",
                ],
                turn_mode=TurnMode.STRICT,
                deadline_hours=1,
                ui_schema_ref="encounter_combat_ui",
            ),
            ContextDefinition(
                context_type="trade_session",
                display_name="Trade Session",
                allowed_actions=[
                    "buy_item",
                    "sell_item",
                    "end_trade",
                ],
                turn_mode=TurnMode.STRICT,
                deadline_hours=1,
                ui_schema_ref="trade_session_ui",
            ),
            ContextDefinition(
                context_type="zone_event",
                display_name="Zone Event",
                allowed_actions=[
                    "choose_option",
                    "leave_event",
                ],
                turn_mode=TurnMode.SIMULTANEOUS,
                deadline_hours=24,
                ui_schema_ref="zone_event_ui",
            ),
        ]

    # ──────────────────────────────
    # Entity Archetypes
    # ──────────────────────────────

    def register_entities(self) -> List[EntityArchetype]:
        return [
            EntityArchetype(
                archetype_id="stalker_agent",
                display_name="Stalker",
                allowed_components=[
                    _cs("identity", True),
                    _cs("position", True),
                    _cs("stats", True),
                    _cs("health", True),
                    _cs("inventory"),
                    _cs("equipment"),
                    _cs("economy"),
                    _cs("controller", True),
                    _cs("faction"),
                    _cs("ai"),
                    _cs("status_effects"),
                ],
                default_visibility="private",
            ),
            EntityArchetype(
                archetype_id="mutant_agent",
                display_name="Mutant",
                allowed_components=[
                    _cs("identity", True),
                    _cs("position", True),
                    _cs("stats", True),
                    _cs("health", True),
                    _cs("aggression", True),
                    _cs("ai", True),
                    _cs("loot_table"),
                ],
                default_tags=["hostile", "mutant"],
                default_visibility="public",
            ),
            EntityArchetype(
                archetype_id="trader_npc",
                display_name="Trader",
                allowed_components=[
                    _cs("identity", True),
                    _cs("position", True),
                    _cs("inventory", True),
                    _cs("economy", True),
                    _cs("trade_rules", True),
                    _cs("dialog_stub"),
                    _cs("faction"),
                ],
                default_tags=["npc", "trader", "safe"],
                default_visibility="public",
            ),
            EntityArchetype(
                archetype_id="anomaly_field",
                display_name="Anomaly",
                allowed_components=[
                    _cs("position", True),
                    _cs("anomaly_type", True),
                    _cs("danger_radius", True),
                    _cs("damage_profile", True),
                    _cs("activation_state"),
                    _cs("artifact_spawn_rules"),
                    _cs("visibility_hint"),
                ],
                default_tags=["environmental", "hazard"],
                default_visibility="public",
            ),
            EntityArchetype(
                archetype_id="artifact_item",
                display_name="Artifact",
                allowed_components=[
                    _cs("position", True),
                    _cs("artifact_type", True),
                    _cs("value", True),
                    _cs("effects"),
                    _cs("radiation_profile"),
                    _cs("ownership"),
                ],
                default_tags=["item", "artifact", "valuable"],
                default_visibility="public",
            ),
            EntityArchetype(
                archetype_id="generic_item",
                display_name="Item",
                allowed_components=[
                    _cs("item_type", True),
                    _cs("weight", True),
                    _cs("value", True),
                    _cs("effects"),
                    _cs("durability"),
                ],
                default_tags=["item"],
                default_visibility="public",
            ),
        ]

    # ──────────────────────────────
    # Actions
    # ──────────────────────────────

    def register_actions(self) -> List[ActionDefinition]:
        return [
            # Zone map actions — instant
            ActionDefinition(
                action_type="move_agent",
                display_name="Move (instant)",
                description="Move your stalker to an adjacent location (costs 1 action)",
                payload_schema={"target_location_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map"],
            ),
            # Zone map actions — scheduled (multi-turn)
            ActionDefinition(
                action_type="travel",
                display_name="Travel",
                description="Plan a multi-hour journey to any reachable location",
                payload_schema={"target_location_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map"],
            ),
            ActionDefinition(
                action_type="explore_location",
                display_name="Explore (1 hr)",
                description="Spend 1 hour searching the current location for loot",
                payload_schema={},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map"],
            ),
            ActionDefinition(
                action_type="sleep",
                display_name="Sleep",
                description="Rest for several hours to recover HP and reduce radiation",
                payload_schema={"hours": {"type": "integer", "minimum": 2, "maximum": 10}},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map"],
            ),
            ActionDefinition(
                action_type="join_event",
                display_name="Join Event",
                description="Enter an active zone event (text quest) in your location",
                payload_schema={"event_context_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map"],
            ),
            ActionDefinition(
                action_type="pick_up_artifact",
                display_name="Pick Up Artifact",
                description="Collect an artifact from the current location",
                payload_schema={"artifact_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map"],
            ),
            ActionDefinition(
                action_type="pick_up_item",
                display_name="Pick Up Item",
                description="Collect an item from the current location",
                payload_schema={"item_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map", "location_exploration"],
            ),
            ActionDefinition(
                action_type="end_turn",
                display_name="End Turn",
                description="Submit your action and wait for the next hour",
                payload_schema={},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_map", "location_exploration", "encounter_combat"],
            ),
            # Zone event actions
            ActionDefinition(
                action_type="choose_option",
                display_name="Choose",
                description="Choose one of the GM-provided options in the current event",
                payload_schema={"option_index": {"type": "integer"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_event"],
            ),
            ActionDefinition(
                action_type="leave_event",
                display_name="Leave Event",
                description="Leave the event early",
                payload_schema={},
                applicable_archetypes=["stalker_agent"],
                context_types=["zone_event"],
            ),
            # Exploration actions
            ActionDefinition(
                action_type="explore_move",
                display_name="Move",
                description="Move in a direction within the location",
                payload_schema={"direction": {"type": "string", "enum": ["n", "s", "e", "w", "ne", "nw", "se", "sw"]}},
                applicable_archetypes=["stalker_agent"],
                context_types=["location_exploration"],
            ),
            ActionDefinition(
                action_type="leave_location",
                display_name="Leave Location",
                description="Return to the zone map",
                payload_schema={},
                applicable_archetypes=["stalker_agent"],
                context_types=["location_exploration"],
            ),
            ActionDefinition(
                action_type="interact",
                display_name="Interact",
                description="Interact with a container or object",
                payload_schema={"target_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["location_exploration"],
            ),
            # Combat actions
            ActionDefinition(
                action_type="attack",
                display_name="Attack",
                description="Attack a target in combat",
                payload_schema={"target_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent", "mutant_agent"],
                context_types=["encounter_combat"],
            ),
            ActionDefinition(
                action_type="use_item",
                display_name="Use Item",
                description="Use a consumable item",
                payload_schema={"item_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["encounter_combat"],
            ),
            ActionDefinition(
                action_type="retreat",
                display_name="Retreat",
                description="Retreat from combat",
                payload_schema={},
                applicable_archetypes=["stalker_agent"],
                context_types=["encounter_combat"],
            ),
            # Trade actions
            ActionDefinition(
                action_type="buy_item",
                display_name="Buy",
                description="Buy an item from the trader",
                payload_schema={"item_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["trade_session"],
            ),
            ActionDefinition(
                action_type="sell_item",
                display_name="Sell",
                description="Sell an item to the trader",
                payload_schema={"item_id": {"type": "string"}},
                applicable_archetypes=["stalker_agent"],
                context_types=["trade_session"],
            ),
            ActionDefinition(
                action_type="end_trade",
                display_name="Close Trade",
                description="End the trade session",
                payload_schema={},
                applicable_archetypes=["stalker_agent"],
                context_types=["trade_session"],
            ),
        ]

    # ──────────────────────────────
    # Rules
    # ──────────────────────────────

    def register_rules(self) -> ZoneStalkerRuleSet:
        return ZoneStalkerRuleSet()

    # ──────────────────────────────
    # Generators
    # ──────────────────────────────

    def register_generators(self) -> list:
        return []

    # ──────────────────────────────
    # UI Schema
    # ──────────────────────────────

    def register_ui(self) -> UISchema:
        return UISchema(
            schema_id="zone_stalkers_ui",
            context_type="zone_map",
            primitives=[
                UIPrimitive(
                    primitive_type=UIPrimitiveType.GRAPH_MAP,
                    config={"title": "Zone Map"},
                    position={"x": 0, "y": 0, "w": 8, "h": 6},
                ),
                UIPrimitive(
                    primitive_type=UIPrimitiveType.ENTITY_CARD,
                    config={"title": "Agent Status"},
                    position={"x": 8, "y": 0, "w": 4, "h": 3},
                ),
                UIPrimitive(
                    primitive_type=UIPrimitiveType.INVENTORY,
                    config={"title": "Inventory"},
                    position={"x": 8, "y": 3, "w": 4, "h": 3},
                ),
                UIPrimitive(
                    primitive_type=UIPrimitiveType.EVENT_LOG,
                    config={"title": "Event Log", "max_entries": 50},
                    position={"x": 0, "y": 6, "w": 8, "h": 2},
                ),
                UIPrimitive(
                    primitive_type=UIPrimitiveType.ACTION_LIST,
                    config={"title": "Actions"},
                    position={"x": 8, "y": 6, "w": 4, "h": 2},
                ),
                UIPrimitive(
                    primitive_type=UIPrimitiveType.TIMER_PANEL,
                    config={"title": "Turn Timer"},
                    position={"x": 0, "y": 8, "w": 4, "h": 1},
                ),
            ],
        )

    # ──────────────────────────────
    # Bot policy
    # ──────────────────────────────

    def get_bot_policy(self) -> BotPolicy:
        return FallbackPlayerBotPolicy()
