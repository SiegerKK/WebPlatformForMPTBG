"""initial

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('username', sa.String(), nullable=False, unique=True),
        sa.Column('email', sa.String(), nullable=False, unique=True),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('is_bot', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'matches',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('game_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), default='waiting'),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('config', sa.JSON()),
        sa.Column('seed', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'match_participants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('role', sa.String(), default='player'),
        sa.Column('faction', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('joined_at', sa.DateTime()),
    )
    op.create_table(
        'game_contexts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('parent_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=True),
        sa.Column('context_type', sa.String(), nullable=False),
        sa.Column('status', sa.String(), default='pending'),
        sa.Column('state', sa.JSON()),
        sa.Column('state_version', sa.Integer(), default=0),
        sa.Column('config', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'entities',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('archetype', sa.String(), nullable=False),
        sa.Column('components', sa.JSON()),
        sa.Column('tags', sa.JSON()),
        sa.Column('visibility', sa.String(), default='public'),
        sa.Column('version', sa.Integer(), default=0),
        sa.Column('is_active', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'commands',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('player_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('command_type', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON()),
        sa.Column('status', sa.String(), default='pending'),
        sa.Column('error_message', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'game_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON()),
        sa.Column('caused_by_command_id', sa.String(36), sa.ForeignKey('commands.id'), nullable=True),
        sa.Column('sequence_number', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'turn_states',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('turn_number', sa.Integer(), default=1),
        sa.Column('mode', sa.String(), default='strict'),
        sa.Column('status', sa.String(), default='waiting_for_players'),
        sa.Column('active_player_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('deadline', sa.DateTime(), nullable=True),
        sa.Column('submitted_players', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
    )

def downgrade() -> None:
    op.drop_table('turn_states')
    op.drop_table('game_events')
    op.drop_table('commands')
    op.drop_table('entities')
    op.drop_table('game_contexts')
    op.drop_table('match_participants')
    op.drop_table('matches')
    op.drop_table('users')
