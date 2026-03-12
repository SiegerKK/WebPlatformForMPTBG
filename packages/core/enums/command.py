from enum import Enum

class CommandStatus(str, Enum):
    RECEIVED = "received"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    FAILED = "failed"
    CANCELLED = "cancelled"
