"""bracket event options (platform, restrictions, finals best of, minimum entrants, game title, swiss) and event admins

Revision ID: e7c1a4b92d3f
Revises: d5b9f3a28c1e
Create Date: 2026-11-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e7c1a4b92d3f'
down_revision: Union[str, Sequence[str], None] = 'd5b9f3a28c1e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bracket_events', sa.Column('game_title', sa.String(), server_default=sa.text("''"), nullable=False))
    op.add_column('bracket_events', sa.Column('final_best_of', sa.Integer(), nullable=True))
    op.add_column('bracket_events', sa.Column('min_entrants', sa.Integer(), nullable=True))
    op.add_column('bracket_events', sa.Column('platform', sa.String(), server_default=sa.text("'pc_allowed'"), nullable=False))
    op.add_column('bracket_events', sa.Column('restrictions', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.drop_constraint('ck_bracket_events_format', 'bracket_events', type_='check')
    op.create_check_constraint('ck_bracket_events_format', 'bracket_events', "format IN ('single_elim', 'double_elim', 'round_robin', 'swiss')")
    op.create_check_constraint('ck_bracket_events_platform', 'bracket_events', "platform IN ('pc_allowed', 'console_only')")

    op.create_table(
        'bracket_event_admins',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'accepted')", name='ck_bracket_event_admins_status'),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'profile_id', name='uq_bracket_event_admins'),
    )
    op.create_index('ix_bracket_event_admins_event_id', 'bracket_event_admins', ['event_id'])
    op.create_index('ix_bracket_event_admins_profile_id', 'bracket_event_admins', ['profile_id'])


def downgrade() -> None:
    op.drop_index('ix_bracket_event_admins_profile_id', table_name='bracket_event_admins')
    op.drop_index('ix_bracket_event_admins_event_id', table_name='bracket_event_admins')
    op.drop_table('bracket_event_admins')
    op.execute("DELETE FROM bracket_events WHERE format = 'swiss'")
    op.drop_constraint('ck_bracket_events_platform', 'bracket_events', type_='check')
    op.drop_constraint('ck_bracket_events_format', 'bracket_events', type_='check')
    op.create_check_constraint('ck_bracket_events_format', 'bracket_events', "format IN ('single_elim', 'double_elim', 'round_robin')")
    for col in ('restrictions', 'platform', 'min_entrants', 'final_best_of', 'game_title'):
        op.drop_column('bracket_events', col)
