"""add performance indexes

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03 00:00:00.000000
"""
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # game_events: context_id is used in SELECT MAX(sequence_no) on every event emission
    op.create_index('ix_game_events_context_id', 'game_events', ['context_id'])
    # game_events: match_id is used in GET /matches/{id}/events
    op.create_index('ix_game_events_match_id', 'game_events', ['match_id'])
    # game_events: created_at is used for the bounded-limit events query (ORDER BY created_at DESC)
    op.create_index('ix_game_events_created_at', 'game_events', ['created_at'])
    # game_contexts: match_id is used in every tick() call to find the zone_map context
    op.create_index('ix_game_contexts_match_id', 'game_contexts', ['match_id'])
    # matches: status is used by the ticker to find all active matches each scheduler fire
    op.create_index('ix_matches_status', 'matches', ['status'])


def downgrade() -> None:
    op.drop_index('ix_matches_status', table_name='matches')
    op.drop_index('ix_game_contexts_match_id', table_name='game_contexts')
    op.drop_index('ix_game_events_created_at', table_name='game_events')
    op.drop_index('ix_game_events_match_id', table_name='game_events')
    op.drop_index('ix_game_events_context_id', table_name='game_events')
