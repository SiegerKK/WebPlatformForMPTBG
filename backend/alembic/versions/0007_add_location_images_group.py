"""Add group column to location_images and update unique constraint.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-15 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('location_images') as batch_op:
        batch_op.add_column(sa.Column('group', sa.String(), nullable=False, server_default='normal'))
        batch_op.drop_constraint('uq_location_images_context_location_slot', type_='unique')
        batch_op.create_unique_constraint(
            'uq_location_images_context_location_group_slot',
            ['context_id', 'location_id', 'group', 'slot'],
        )


def downgrade() -> None:
    with op.batch_alter_table('location_images') as batch_op:
        batch_op.drop_constraint('uq_location_images_context_location_group_slot', type_='unique')
        batch_op.create_unique_constraint(
            'uq_location_images_context_location_slot',
            ['context_id', 'location_id', 'slot'],
        )
        batch_op.drop_column('group')
