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
        sa.Column('game_version', sa.String(), nullable=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('status', sa.String(), default='draft'),
        sa.Column('created_by_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('root_context_id', sa.String(36), nullable=True),
        sa.Column('mode', sa.String(), nullable=True),
        sa.Column('visibility_mode', sa.String(), default='private'),
        sa.Column('seed', sa.String(), nullable=False),
        sa.Column('is_ranked', sa.Boolean(), default=False),
        sa.Column('max_players', sa.Integer(), nullable=True),
        sa.Column('current_phase', sa.String(), nullable=True),
        sa.Column('winner_side_id', sa.String(), nullable=True),
        sa.Column('settings', sa.JSON()),
        sa.Column('metadata', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'participants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('kind', sa.String(), default='human'),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('side_id', sa.String(), nullable=True),
        sa.Column('role', sa.String(), default='player'),
        sa.Column('status', sa.String(), default='joined'),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('is_ready', sa.Boolean(), default=False),
        sa.Column('color', sa.String(), nullable=True),
        sa.Column('fallback_policy_id', sa.String(36), nullable=True),
        sa.Column('bot_policy_id', sa.String(36), nullable=True),
        sa.Column('joined_at', sa.DateTime()),
        sa.Column('meta', sa.JSON()),
    )
    op.create_table(
        'game_contexts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('parent_context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=True),
        sa.Column('context_type', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=True),
        sa.Column('status', sa.String(), default='created'),
        sa.Column('state_blob', sa.JSON()),
        sa.Column('state_version', sa.Integer(), default=0),
        sa.Column('depth', sa.Integer(), default=0),
        sa.Column('sequence_in_parent', sa.Integer(), nullable=True),
        sa.Column('turn_policy_id', sa.String(36), nullable=True),
        sa.Column('time_policy_id', sa.String(36), nullable=True),
        sa.Column('visibility_policy_id', sa.String(36), nullable=True),
        sa.Column('generator_meta', sa.JSON()),
        sa.Column('resolution_state', sa.JSON()),
        sa.Column('result_blob', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'entities',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('archetype_id', sa.String(), nullable=False),
        sa.Column('owner_participant_id', sa.String(36), nullable=True),
        sa.Column('controller_participant_id', sa.String(36), nullable=True),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('components', sa.JSON()),
        sa.Column('tags', sa.JSON()),
        sa.Column('visibility_scope', sa.String(), default='public'),
        sa.Column('spawn_source', sa.String(), nullable=True),
        sa.Column('parent_entity_id', sa.String(36), sa.ForeignKey('entities.id'), nullable=True),
        sa.Column('alive', sa.Boolean(), default=True),
        sa.Column('state_version', sa.Integer(), default=0),
        sa.Column('meta', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'turn_policies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('mode', sa.String(), nullable=False, default='strict'),
        sa.Column('deadline_seconds', sa.Integer(), nullable=True),
        sa.Column('auto_advance', sa.Boolean(), default=True),
        sa.Column('require_all_players_ready', sa.Boolean(), default=False),
        sa.Column('fallback_on_timeout', sa.Boolean(), default=True),
        sa.Column('resolution_order', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'fallback_policies',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('strategy', sa.String(), default='end_turn'),
        sa.Column('bot_policy_id', sa.String(36), nullable=True),
        sa.Column('config', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'commands',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('participant_id', sa.String(36), nullable=False),
        sa.Column('command_type', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON()),
        sa.Column('client_request_id', sa.String(), nullable=True, index=True),
        sa.Column('status', sa.String(), default='received'),
        sa.Column('error_code', sa.String(), nullable=True),
        sa.Column('error_message', sa.String(), nullable=True),
        sa.Column('submitted_via', sa.String(), nullable=True),
        sa.Column('expected_context_version', sa.Integer(), nullable=True),
        sa.Column('causation_ui_action', sa.String(), nullable=True),
        sa.Column('debug_meta', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'game_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('sequence_no', sa.Integer(), default=0),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('payload', sa.JSON()),
        sa.Column('causation_command_id', sa.String(36), sa.ForeignKey('commands.id'), nullable=True),
        sa.Column('correlation_id', sa.String(), nullable=True),
        sa.Column('visibility_scope', sa.String(), default='public'),
        sa.Column('aggregate_version', sa.Integer(), nullable=True),
        sa.Column('producer', sa.String(), nullable=True),
        sa.Column('tags', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'turn_states',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('context_id', sa.String(36), sa.ForeignKey('game_contexts.id'), nullable=False),
        sa.Column('turn_number', sa.Integer(), default=1),
        sa.Column('mode', sa.String(), default='strict'),
        sa.Column('phase', sa.String(), default='collecting'),
        sa.Column('status', sa.String(), default='waiting_for_players'),
        sa.Column('active_side_id', sa.String(), nullable=True),
        sa.Column('deadline_at', sa.DateTime(), nullable=True),
        sa.Column('fallback_policy_id', sa.String(36), nullable=True),
        sa.Column('resolution_mode', sa.String(), nullable=True),
        sa.Column('submitted_players', sa.JSON()),
        sa.Column('opened_at', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'projections',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('projection_type', sa.String(), nullable=False),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('context_id', sa.String(36), nullable=True),
        sa.Column('participant_id', sa.String(36), nullable=True),
        sa.Column('source_event_sequence', sa.Integer(), default=0),
        sa.Column('version', sa.Integer(), default=0),
        sa.Column('payload', sa.JSON()),
        sa.Column('generated_at', sa.DateTime()),
    )
    op.create_table(
        'snapshots',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=False),
        sa.Column('context_id', sa.String(36), nullable=True),
        sa.Column('event_sequence_up_to', sa.Integer(), nullable=False),
        sa.Column('payload', sa.JSON()),
        sa.Column('created_at', sa.DateTime()),
    )
    op.create_table(
        'notifications',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('match_id', sa.String(36), sa.ForeignKey('matches.id'), nullable=True),
        sa.Column('context_id', sa.String(36), nullable=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('body', sa.String(), nullable=True),
        sa.Column('is_read', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime()),
    )

def downgrade() -> None:
    op.drop_table('notifications')
    op.drop_table('snapshots')
    op.drop_table('projections')
    op.drop_table('turn_states')
    op.drop_table('game_events')
    op.drop_table('commands')
    op.drop_table('fallback_policies')
    op.drop_table('turn_policies')
    op.drop_table('entities')
    op.drop_table('game_contexts')
    op.drop_table('participants')
    op.drop_table('matches')
    op.drop_table('users')
