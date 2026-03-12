import uuid
from typing import Optional
from sqlalchemy.orm import Session
from app.core.visibility.service import VisibilityPolicy, FogProjection

class ProjectionService:
    """Materialized state views per player"""
    def __init__(self):
        self.fog = FogProjection()
        self.policy = VisibilityPolicy()

    def get_player_projection(self, context_id: uuid.UUID, player_id: uuid.UUID, db: Session) -> dict:
        from app.core.contexts.models import GameContext
        context = db.query(GameContext).filter(GameContext.id == context_id).first()
        if not context:
            return {}
        return self.fog.project(context, player_id, self.policy, db)

projection_service = ProjectionService()
