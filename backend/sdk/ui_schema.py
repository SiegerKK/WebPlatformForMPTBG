from pydantic import BaseModel
from typing import List
from enum import Enum

class UIPrimitiveType(str, Enum):
    TILE_GRID = "tile_grid"
    HEX_GRID = "hex_grid"
    GRAPH_MAP = "graph_map"
    TABLE = "table"
    ENTITY_CARD = "entity_card"
    EVENT_LOG = "event_log"
    ACTION_LIST = "action_list"
    CONTEXT_TREE = "context_tree"
    INVENTORY = "inventory"
    DIALOG = "dialog"
    MODAL_RESULT = "modal_result"
    RESOURCE_PANEL = "resource_panel"
    TIMER_PANEL = "timer_panel"

class UIPrimitive(BaseModel):
    primitive_type: UIPrimitiveType
    config: dict = {}
    position: dict = {}

class UISchema(BaseModel):
    schema_id: str
    context_type: str
    primitives: List[UIPrimitive] = []
    visibility_rules: dict = {}
