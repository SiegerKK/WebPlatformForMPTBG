import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .models import TurnState, TurnMode, TurnStatus
from .schemas import TurnStateCreate

class TurnScheduler:
    def get_current_turn(self, context_id: uuid.UUID, db: Session) -> TurnState:
        turn = db.query(TurnState).filter(
            TurnState.context_id == context_id,
            TurnState.status != TurnStatus.RESOLVED
        ).order_by(TurnState.turn_number.desc()).first()
        if not turn:
            raise HTTPException(status_code=404, detail="No active turn found")
        return turn

    def is_player_turn(self, context_id: uuid.UUID, player_id: uuid.UUID, db: Session) -> bool:
        try:
            turn = self.get_current_turn(context_id, db)
        except HTTPException:
            return False
        if turn.mode == TurnMode.STRICT:
            return str(turn.active_player_id) == str(player_id)
        return True

    def submit_turn(self, context_id: uuid.UUID, player_id: uuid.UUID, db: Session) -> TurnState:
        turn = self.get_current_turn(context_id, db)
        submitted = list(turn.submitted_players or [])
        player_id_str = str(player_id)
        if player_id_str not in submitted:
            submitted.append(player_id_str)
        turn.submitted_players = submitted
        db.commit()
        db.refresh(turn)
        return turn

    def advance_turn(self, context_id: uuid.UUID, db: Session) -> TurnState:
        try:
            current = self.get_current_turn(context_id, db)
            current.status = TurnStatus.RESOLVED
            current.resolved_at = datetime.utcnow()
            turn_number = current.turn_number + 1
        except HTTPException:
            turn_number = 1

        new_turn = TurnState(
            context_id=context_id,
            turn_number=turn_number,
            mode=TurnMode.STRICT,
            status=TurnStatus.WAITING_FOR_PLAYERS,
            submitted_players=[],
        )
        db.add(new_turn)
        db.commit()
        db.refresh(new_turn)
        return new_turn

    def check_deadlines(self, db: Session) -> List[TurnState]:
        now = datetime.utcnow()
        return db.query(TurnState).filter(
            TurnState.deadline <= now,
            TurnState.status == TurnStatus.WAITING_FOR_PLAYERS
        ).all()

    def apply_fallback(self, turn: TurnState, db: Session):
        turn.status = TurnStatus.RESOLVED
        turn.resolved_at = datetime.utcnow()
        db.commit()

turn_scheduler = TurnScheduler()
