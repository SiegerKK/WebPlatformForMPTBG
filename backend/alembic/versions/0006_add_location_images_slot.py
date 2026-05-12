"""Add slot column to location_images and update unique constraint.

Revision ID: 0006
Revises: 0005
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("location_images") as batch_op:
        batch_op.add_column(sa.Column("slot", sa.String(), nullable=False, server_default="clear"))
        batch_op.drop_constraint("uq_location_images_context_location", type_="unique")
        batch_op.create_unique_constraint(
            "uq_location_images_context_location_slot",
            ["context_id", "location_id", "slot"],
        )


def downgrade() -> None:
    with op.batch_alter_table("location_images") as batch_op:
        batch_op.drop_constraint("uq_location_images_context_location_slot", type_="unique")
        batch_op.create_unique_constraint(
            "uq_location_images_context_location",
            ["context_id", "location_id"],
        )
        batch_op.drop_column("slot")
