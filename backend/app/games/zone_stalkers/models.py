"""
SQLAlchemy models for Zone Stalkers game-specific data that is stored
outside the main game-state JSON blob.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint
from app.database import Base, UUIDType


class LocationImage(Base):
    """Stores metadata about an image attached to a zone-map location.

    The actual file lives on disk at ``file_path`` (relative to the
    application media root).  The location's ``image_url`` in the game-state
    blob points at the publicly-accessible HTTP path
    ``/media/<file_path>``.

    The ``slot`` column identifies the weather/time-of-day slot:
    "clear", "fog", "rain", "night_clear", "night_rain".
    Each (context_id, location_id, slot) triple is unique.
    """

    __tablename__ = "location_images"
    __table_args__ = (
        UniqueConstraint(
            "context_id", "location_id", "slot",
            name="uq_location_images_context_location_slot",
        ),
    )

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    context_id = Column(UUIDType, ForeignKey("game_contexts.id"), nullable=False)
    location_id = Column(String, nullable=False)
    slot = Column(String, nullable=False, server_default="clear")
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    # Path relative to the media root, e.g. "locations/<ctx_id>/<loc_id>/<slot>/<uuid>.jpg"
    file_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
