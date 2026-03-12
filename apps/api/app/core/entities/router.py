import uuid
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .schemas import EntityCreate, EntityUpdate, EntityRead
from .service import create_entity, get_entity, get_entities_in_context, update_entity, delete_entity
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.database import get_db

router = APIRouter(tags=["entities"])

@router.post("/entities", response_model=EntityRead)
def create(data: EntityCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return create_entity(data, db)

@router.get("/entities/{entity_id}", response_model=EntityRead)
def get(entity_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_entity(entity_id, db)

@router.get("/contexts/{context_id}/entities", response_model=List[EntityRead])
def list_in_context(context_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_entities_in_context(context_id, db)

@router.patch("/entities/{entity_id}", response_model=EntityRead)
def update(entity_id: uuid.UUID, data: EntityUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return update_entity(entity_id, data, db)

@router.delete("/entities/{entity_id}", status_code=204)
def delete(entity_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    delete_entity(entity_id, db)
