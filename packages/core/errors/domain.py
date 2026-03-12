from typing import Optional

class DomainError(Exception):
    """Base class for domain errors."""
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__

class MatchNotFoundError(DomainError):
    pass

class ContextNotFoundError(DomainError):
    pass

class EntityNotFoundError(DomainError):
    pass

class CommandRejectedError(DomainError):
    pass

class NotParticipantError(DomainError):
    pass

class InvalidMatchStateError(DomainError):
    pass

class InvalidContextStateError(DomainError):
    pass

class TurnViolationError(DomainError):
    pass

class VisibilityViolationError(DomainError):
    pass
