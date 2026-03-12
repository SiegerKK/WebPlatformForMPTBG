"""
Ticker router — exposes the manual tick endpoint for testing and administration.

POST /api/matches/{match_id}/tick   — advance world turn by one hour
POST /api/tick/all                  — tick all active zone_stalkers matches (admin)
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.auth.service import get_current_user
from app.core.auth.models import User
from app.core.ticker.service import tick_match, tick_all_active_matches

router = APIRouter(tags=["ticker"])


@router.post("/matches/{match_id}/tick")
def manual_tick(
    match_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually advance the world by one game-turn.
    Any authenticated user can tick their own match; admins can tick any match.
    """
    from app.core.matches.models import Match
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    if not current_user.is_superuser and str(match.created_by_user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorised to tick this match")

    result = tick_match(str(match_id), db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/tick/all")
def tick_all(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Tick all active zone_stalkers matches. Admin only."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin only")
    return tick_all_active_matches(db)
