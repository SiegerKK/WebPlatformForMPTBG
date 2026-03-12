import uuid
from typing import List
from sqlalchemy.orm import Session

class VisibilityPolicy:
    """Base class for visibility rules"""
    def can_see_entity(self, entity, viewer_id: uuid.UUID) -> bool:
        if entity.visibility_scope == "public":
            return True
        if entity.visibility_scope == "owner_only":
            return str(entity.owner_participant_id) == str(viewer_id)
        return True

    def get_visible_entities(self, context_id: uuid.UUID, viewer_id: uuid.UUID, db: Session) -> list:
        from app.core.entities.models import Entity
        entities = db.query(Entity).filter(Entity.context_id == context_id, Entity.alive == True).all()
        return [e for e in entities if self.can_see_entity(e, viewer_id)]


class FogProjection:
    """Creates player-specific view of context state"""
    def project(self, context, viewer_id: uuid.UUID, policy: VisibilityPolicy, db: Session) -> dict:
        visible_entities = policy.get_visible_entities(context.id, viewer_id, db)
        return {
            "context_id": str(context.id),
            "context_type": context.context_type,
            "state_blob": context.state_blob,
            "state_version": context.state_version,
            "visible_entities": [
                {
                    "id": str(e.id),
                    "archetype_id": e.archetype_id,
                    "components": e.components,
                    "tags": e.tags,
                    "owner_participant_id": str(e.owner_participant_id) if e.owner_participant_id else None,
                }
                for e in visible_entities
            ],
        }
