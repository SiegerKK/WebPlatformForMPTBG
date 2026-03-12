"""
Seed script — creates the default admin user if it does not exist yet.

Reads credentials from environment variables with sensible defaults:
  ADMIN_USERNAME  (default: admin)
  ADMIN_EMAIL     (default: admin@example.com)
  ADMIN_PASSWORD  (default: admin)

Run manually:
  cd backend && python -m app.seed

Or via Make:
  make seed-admin
"""
import os
import sys

from app.database import SessionLocal
from app.core.auth.models import User
from app.core.auth.service import hash_password

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL",    "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")


def seed() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == ADMIN_USERNAME).first()
        if existing:
            print(f"[seed] Admin user '{ADMIN_USERNAME}' already exists — skipping.")
            return

        admin = User(
            username=ADMIN_USERNAME,
            email=ADMIN_EMAIL,
            hashed_password=hash_password(ADMIN_PASSWORD),
            is_active=True,
            is_bot=False,
        )
        db.add(admin)
        db.commit()
        print(f"[seed] Created admin user '{ADMIN_USERNAME}' ({ADMIN_EMAIL}).")
        print(f"[seed] ⚠  Change the default password before exposing to the internet!")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    sys.exit(0)
