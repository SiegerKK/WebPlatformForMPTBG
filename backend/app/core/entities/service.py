import uuid
from typing import List
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import Entity
from .schemas import EntityCreate, EntityUpdate

def create_entity(data: EntityCreate, db: Session) -> Entity:
    match_id = data.match_id
    if match_id is None:
        from app.core.contexts.models import GameContext
        ctx = db.query(GameContext).filter(GameContext.id == data.context_id).first()
        if ctx:
            match_id = ctx.match_id
    entity = Entity(
        context_id=data.context_id,
        match_id=match_id,
        archetype_id=data.archetype_id,
        owner_participant_id=data.owner_participant_id,
        components=data.components,
        tags=data.tags,
        visibility_scope=data.visibility_scope,
        display_name=data.display_name,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity

def get_entity(entity_id: uuid.UUID, db: Session) -> Entity:
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity

def get_entities_in_context(context_id: uuid.UUID, db: Session) -> List[Entity]:
    return db.query(Entity).filter(Entity.context_id == context_id, Entity.alive == True).all()

def update_entity(entity_id: uuid.UUID, data: EntityUpdate, db: Session) -> Entity:
    entity = get_entity(entity_id, db)
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(entity, key, value)
    entity.state_version += 1
    db.commit()
    db.refresh(entity)
    return entity

def delete_entity(entity_id: uuid.UUID, db: Session):
    entity = get_entity(entity_id, db)
    entity.alive = False
    db.commit()
