import uuid
from typing import List
from sqlalchemy.orm import Session
from .models import Command
from .schemas import CommandEnvelope, CommandResult
from .pipeline import CommandPipeline
from app.core.auth.models import User

pipeline = CommandPipeline()

def process_command(envelope: CommandEnvelope, player: User, db: Session) -> CommandResult:
    return pipeline.process(envelope, player, db)

def get_match_commands(match_id: uuid.UUID, db: Session) -> List[Command]:
    return db.query(Command).filter(Command.match_id == match_id).all()
