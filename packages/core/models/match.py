from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from packages.core.enums.match import MatchStatus, ParticipantKind, ParticipantStatus

@dataclass
class Match:
    id: uuid.UUID
    game_id: str
    created_by_user_id: uuid.UUID
    status: MatchStatus = MatchStatus.DRAFT
    seed: str = field(default_factory=lambda: str(uuid.uuid4()))
    game_version: Optional[str] = None
    title: Optional[str] = None
    mode: Optional[str] = None
    visibility_mode: str = "private"
    is_ranked: bool = False
    max_players: Optional[int] = None
    settings: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

@dataclass
class Participant:
    id: uuid.UUID
    match_id: uuid.UUID
    kind: ParticipantKind = ParticipantKind.HUMAN
    user_id: Optional[uuid.UUID] = None
    role: str = "player"
    status: ParticipantStatus = ParticipantStatus.JOINED
    side_id: Optional[str] = None
    display_name: Optional[str] = None
    is_ready: bool = False
    joined_at: datetime = field(default_factory=datetime.utcnow)
