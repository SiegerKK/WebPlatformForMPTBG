"""add unique constraint for location_images context/location

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-11 18:10:00.000000
"""
from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM location_images
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY context_id, location_id
                        ORDER BY created_at DESC, id DESC
                    ) AS row_num
                FROM location_images
            ) ranked
            WHERE ranked.row_num > 1
        )
        """
    )
    op.create_unique_constraint(
        "uq_location_images_context_location",
        "location_images",
        ["context_id", "location_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_location_images_context_location",
        "location_images",
        type_="unique",
    )
