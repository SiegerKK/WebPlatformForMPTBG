from enum import Enum

class TurnMode(str, Enum):
    STRICT = "strict"
    SIMULTANEOUS = "simultaneous"
    WEGO = "wego"
    HYBRID = "hybrid"

class TurnPhase(str, Enum):
    OPENING = "opening"
    COLLECTING = "collecting"
    RESOLVING = "resolving"
    CLOSED = "closed"

class TurnStatus(str, Enum):
    WAITING_FOR_PLAYERS = "waiting_for_players"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
