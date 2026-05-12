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
    # Add the slot column with server default 'clear' so existing rows get it
    op.add_column(
        'location_images',
        sa.Column('slot', sa.String(), nullable=False, server_default='clear'),
    )
    # Drop the old unique constraint
    op.drop_constraint(
        'uq_location_images_context_location',
        'location_images',
        type_='unique',
    )
    # Add new unique constraint including slot
    op.create_unique_constraint(
        'uq_location_images_context_location_slot',
        'location_images',
        ['context_id', 'location_id', 'slot'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_location_images_context_location_slot',
        'location_images',
        type_='unique',
    )
    op.drop_column('location_images', 'slot')
    op.create_unique_constraint(
        'uq_location_images_context_location',
        'location_images',
        ['context_id', 'location_id'],
    )
