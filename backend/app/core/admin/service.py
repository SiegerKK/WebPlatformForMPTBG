import uuid
from typing import List
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.core.auth.models import User
from app.core.auth.schemas import UserUpdate
from app.core.matches.models import Match, Participant


def list_users(db: Session) -> List[User]:
    return db.query(User).order_by(User.created_at).all()


def get_user(user_id: uuid.UUID, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def get_user_profile(user_id: uuid.UUID, db: Session) -> dict:
    user = get_user(user_id, db)
    matches_created = db.query(Match).filter(Match.created_by_user_id == user_id).count()
    matches_played = (
        db.query(Participant)
        .filter(Participant.user_id == user_id)
        .count()
    )
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "is_active": user.is_active,
        "is_bot": user.is_bot,
        "is_superuser": user.is_superuser,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "matches_created": matches_created,
        "matches_played": matches_played,
    }


def update_user(user_id: uuid.UUID, data: UserUpdate, admin: User, db: Session) -> User:
    user = get_user(user_id, db)
    # Prevent removing the last superuser's own superuser flag
    if data.is_superuser is False and str(user.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="Cannot remove your own admin privileges")
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.is_superuser is not None:
        user.is_superuser = data.is_superuser
    db.commit()
    db.refresh(user)
    return user


def delete_user(user_id: uuid.UUID, admin: User, db: Session) -> None:
    user = get_user(user_id, db)
    if str(user.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="Cannot delete your own account via admin panel")
    db.delete(user)
    db.commit()
