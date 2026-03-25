"""add location_images table

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-25 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'location_images',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('location_id', sa.String(length=128), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index(
        'ix_location_images_context_location',
        'location_images',
        ['context_id', 'location_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_location_images_context_location', table_name='location_images')
    op.drop_table('location_images')
