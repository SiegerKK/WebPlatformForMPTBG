from enum import Enum

class ContextStatus(str, Enum):
    CREATED = "created"
    INITIALIZING = "initializing"
    ACTIVE = "active"
    RESOLVING = "resolving"
    SUSPENDED = "suspended"
    FINISHED = "finished"
    FAILED = "failed"
    ARCHIVED = "archived"
