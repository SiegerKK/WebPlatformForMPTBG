import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .schemas import TurnStateRead
from .service import turn_scheduler
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.database import get_db

router = APIRouter(tags=["turns"])

@router.get("/contexts/{context_id}/turn", response_model=TurnStateRead)
def get_turn(context_id: uuid.UUID, db: Session = Depends(get_db)):
    return turn_scheduler.get_current_turn(context_id, db)

@router.post("/contexts/{context_id}/turn/submit", response_model=TurnStateRead)
def submit_turn(context_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return turn_scheduler.submit_turn(context_id, current_user.id, db)

@router.post("/contexts/{context_id}/turn/advance", response_model=TurnStateRead)
def advance_turn(context_id: uuid.UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return turn_scheduler.advance_turn(context_id, db)
