from enum import Enum

class MatchStatus(str, Enum):
    DRAFT = "draft"
    WAITING_FOR_PLAYERS = "waiting_for_players"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    PAUSED = "paused"
    FINISHED = "finished"
    ARCHIVED = "archived"
    FAILED = "failed"

class ParticipantKind(str, Enum):
    HUMAN = "human"
    BOT = "bot"
    NEUTRAL = "neutral"
    SYSTEM = "system"

class ParticipantStatus(str, Enum):
    INVITED = "invited"
    JOINED = "joined"
    READY = "ready"
    ACTIVE = "active"
    ELIMINATED = "eliminated"
    LEFT = "left"
    TIMED_OUT = "timed_out"
