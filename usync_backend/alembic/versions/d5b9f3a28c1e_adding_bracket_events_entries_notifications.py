"""Adding bracket events, bracket entries and notifications.

Revision ID: d5b9f3a28c1e
Revises: c4a8e2f17b9d
Create Date: 2026-11-07 12:00:00.000000

Written by hand. Moves bracket hosting (events, sign-ups, brackets) off the frontend's
localStorage mock and adds account notifications.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd5b9f3a28c1e'
down_revision: Union[str, Sequence[str], None] = 'c4a8e2f17b9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type = sa.Text())


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bracket_events',
        sa.Column('id', sa.Uuid(), nullable = False),
        sa.Column('public_id', sa.String(), nullable = False),
        sa.Column('host_profile_id', sa.Uuid(), nullable = False),
        sa.Column('name', sa.String(), nullable = False),
        sa.Column('event_type', sa.String(), nullable = False),
        sa.Column('game', sa.String(), nullable = False),
        sa.Column('format', sa.String(), nullable = False),
        sa.Column('team_size', sa.Integer(), nullable = False),
        sa.Column('best_of', sa.Integer(), nullable = False),
        sa.Column('max_entrants', sa.Integer(), nullable = True),
        sa.Column('starts_at', sa.DateTime(timezone = True), nullable = False),
        sa.Column('location_type', sa.String(), server_default = sa.text("'online'"), nullable = False),
        sa.Column('venue', sa.String(), server_default = sa.text("''"), nullable = False),
        sa.Column('entry_fee', sa.String(), server_default = sa.text("'Free'"), nullable = False),
        sa.Column('prize', sa.String(), server_default = sa.text("''"), nullable = False),
        sa.Column('description', sa.String(), server_default = sa.text("''"), nullable = False),
        sa.Column('rules', sa.String(), server_default = sa.text("''"), nullable = False),
        sa.Column('registration_open', sa.Boolean(), server_default = sa.text('true'), nullable = False),
        sa.Column('no_show_minutes', sa.Integer(), nullable = True),
        sa.Column('payments', JSONB, server_default = sa.text("'{}'::jsonb"), nullable = False),
        sa.Column('signup_form', JSONB, server_default = sa.text("'{}'::jsonb"), nullable = False),
        sa.Column('delays', JSONB, server_default = sa.text("'[]'::jsonb"), nullable = False),
        sa.Column('bracket', JSONB, nullable = True),
        sa.Column('created_at', sa.DateTime(timezone = True), server_default = sa.text('now()'), nullable = False),
        sa.ForeignKeyConstraint(['host_profile_id'], ['profiles.id'], ondelete = 'CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('public_id', name = 'uq_bracket_events_public_id'),
        sa.CheckConstraint("event_type IN ('league', 'lan', 'tournament')", name = 'ck_bracket_events_type'),
        sa.CheckConstraint("format IN ('single_elim', 'double_elim', 'round_robin')", name = 'ck_bracket_events_format'),
        sa.CheckConstraint('team_size BETWEEN 1 AND 10', name = 'ck_bracket_events_team_size'),
    )
    op.create_index('ix_bracket_events_host_profile_id', 'bracket_events', ['host_profile_id'])

    op.create_table(
        'bracket_entries',
        sa.Column('id', sa.Uuid(), nullable = False),
        sa.Column('event_id', sa.Uuid(), nullable = False),
        sa.Column('profile_id', sa.Uuid(), nullable = False),
        sa.Column('team_id', sa.Uuid(), nullable = True),
        sa.Column('name', sa.String(), nullable = False),
        sa.Column('status', sa.String(), server_default = sa.text("'signed_up'"), nullable = False),
        sa.Column('rating', sa.Integer(), server_default = sa.text('1200'), nullable = False),
        sa.Column('email', sa.String(), server_default = sa.text("''"), nullable = False),
        sa.Column('answers', JSONB, server_default = sa.text("'{}'::jsonb"), nullable = False),
        sa.Column('pass_name', sa.String(), nullable = True),
        sa.Column('roster', JSONB, server_default = sa.text("'[]'::jsonb"), nullable = False),
        sa.Column('registered_at', sa.DateTime(timezone = True), server_default = sa.text('now()'), nullable = False),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete = 'CASCADE'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete = 'CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete = 'SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("status IN ('signed_up', 'pending')", name = 'ck_bracket_entries_status'),
    )
    op.create_index('ix_bracket_entries_event_id', 'bracket_entries', ['event_id'])
    op.create_index('ix_bracket_entries_profile_id', 'bracket_entries', ['profile_id'])
    op.create_index('ix_bracket_entries_team_id', 'bracket_entries', ['team_id'])
    op.create_index('uq_bracket_entries_team', 'bracket_entries', ['event_id', 'team_id'], unique = True, postgresql_where = sa.text('team_id IS NOT NULL'))
    op.create_index('uq_bracket_entries_solo', 'bracket_entries', ['event_id', 'profile_id'], unique = True, postgresql_where = sa.text('team_id IS NULL'))

    op.create_table(
        'notifications',
        sa.Column('id', sa.Uuid(), nullable = False),
        sa.Column('account_id', sa.Uuid(), nullable = False),
        sa.Column('type', sa.String(), nullable = False),
        sa.Column('data', JSONB, server_default = sa.text("'{}'::jsonb"), nullable = False),
        sa.Column('read', sa.Boolean(), server_default = sa.text('false'), nullable = False),
        sa.Column('created_at', sa.DateTime(timezone = True), server_default = sa.text('now()'), nullable = False),
        sa.ForeignKeyConstraint(['account_id'], ['users.id'], ondelete = 'CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notifications_account_created', 'notifications', ['account_id', 'created_at'])

    # Same as the other tables: only the backend (service role) reads and writes these.
    for table in ('bracket_events', 'bracket_entries', 'notifications'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('notifications')
    op.drop_table('bracket_entries')
    op.drop_table('bracket_events')
