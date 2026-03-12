import uuid
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.auth.models import User
from app.core.auth.schemas import UserRead, UserUpdate
from app.core.auth.service import require_admin, get_current_user
from app.database import get_db
from .service import list_users, get_user_profile, update_user, delete_user
from .schemas import UserProfileRead

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=List[UserRead])
def admin_list_users(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return list_users(db)


@router.get("/users/{user_id}", response_model=UserProfileRead)
def admin_get_user_profile(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return get_user_profile(user_id, db)


@router.patch("/users/{user_id}", response_model=UserRead)
def admin_update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return update_user(user_id, data, admin, db)


@router.delete("/users/{user_id}", status_code=204)
def admin_delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    delete_user(user_id, admin, db)


# Public profile endpoint — accessible to any authenticated user
@router.get("/profile/{user_id}", response_model=UserProfileRead)
def get_public_profile(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_user_profile(user_id, db)
