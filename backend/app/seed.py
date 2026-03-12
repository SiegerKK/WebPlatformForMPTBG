"""
Seed script — creates default users if they do not exist yet.

Users seeded:
  • admin   — platform administrator
  • gamer1  — test player 1 (username: gamer1, password: gamer1)
  • gamer2  — test player 2 (username: gamer2, password: gamer2)

Credentials are overridable via environment variables:
  ADMIN_USERNAME / ADMIN_EMAIL / ADMIN_PASSWORD
  GAMER1_USERNAME / GAMER1_EMAIL / GAMER1_PASSWORD
  GAMER2_USERNAME / GAMER2_EMAIL / GAMER2_PASSWORD

Run manually:
  cd backend && python -m app.seed

Or via Make:
  make seed
"""
import os
import sys

from app.database import SessionLocal
from app.core.auth.models import User
from app.core.auth.service import hash_password

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL",    "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

GAMER1_USERNAME = os.environ.get("GAMER1_USERNAME", "gamer1")
GAMER1_EMAIL    = os.environ.get("GAMER1_EMAIL",    "gamer1@example.com")
GAMER1_PASSWORD = os.environ.get("GAMER1_PASSWORD", "gamer1")

GAMER2_USERNAME = os.environ.get("GAMER2_USERNAME", "gamer2")
GAMER2_EMAIL    = os.environ.get("GAMER2_EMAIL",    "gamer2@example.com")
GAMER2_PASSWORD = os.environ.get("GAMER2_PASSWORD", "gamer2")


def _ensure_user(db, username: str, email: str, password: str) -> None:
    """Create a user if one with the given username does not exist yet."""
    if db.query(User).filter(User.username == username).first():
        print(f"[seed] User '{username}' already exists — skipping.")
        return
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        is_active=True,
        is_bot=False,
    )
    db.add(user)
    db.commit()
    print(f"[seed] Created user '{username}' ({email}).")


def seed() -> None:
    db = SessionLocal()
    try:
        _ensure_user(db, ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD)
        _ensure_user(db, GAMER1_USERNAME, GAMER1_EMAIL, GAMER1_PASSWORD)
        _ensure_user(db, GAMER2_USERNAME, GAMER2_EMAIL, GAMER2_PASSWORD)
        print("[seed] ⚠  Change default passwords before exposing to the internet!")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    sys.exit(0)
