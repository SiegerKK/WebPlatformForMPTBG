import uuid
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from .schemas import CommandEnvelope, CommandResult, CommandRead
from .service import process_command, get_match_commands
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.database import get_db

router = APIRouter(tags=["commands"])

@router.post("/commands", response_model=CommandResult)
def submit_command(envelope: CommandEnvelope, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return process_command(envelope, current_user, db)

@router.get("/matches/{match_id}/commands", response_model=List[CommandRead])
def list_commands(match_id: uuid.UUID, db: Session = Depends(get_db)):
    return get_match_commands(match_id, db)
