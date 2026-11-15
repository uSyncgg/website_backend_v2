"""match chat moderation: hidden messages and staff reports (also merges the chat and admin heads)

Revision ID: a1c7e3f9d205
Revises: f4a9c2d7b6e1, b4f1a7d2c963
Create Date: 2026-11-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a1c7e3f9d205'
down_revision: Union[str, Sequence[str], None] = ('f4a9c2d7b6e1', 'b4f1a7d2c963')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('match_chat_messages', sa.Column('hidden_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('match_chat_messages', sa.Column('hidden_by_account_id', sa.Uuid(), nullable=True))
    op.add_column('match_chat_messages', sa.Column('hidden_by_name', sa.String(), nullable=True))
    op.create_foreign_key('fk_match_chat_messages_hidden_by', 'match_chat_messages', 'users', ['hidden_by_account_id'], ['id'], ondelete='SET NULL')

    op.create_table(
        'chat_reports',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('status', sa.String(), server_default=sa.text("'open'"), nullable=False),
        sa.Column('reporter_account_id', sa.Uuid(), nullable=True),
        sa.Column('reporter_name', sa.String(), nullable=True),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('room_id', sa.Uuid(), nullable=True),
        sa.Column('event_public_id', sa.String(), nullable=False),
        sa.Column('event_name', sa.String(), nullable=False),
        sa.Column('match_id', sa.String(), nullable=False),
        sa.Column('sender_account_id', sa.Uuid(), nullable=True),
        sa.Column('sender_name', sa.String(), nullable=True),
        sa.Column('sender_role', sa.String(), nullable=True),
        sa.Column('message_body', sa.String(), nullable=False),
        sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('reviewed_by_email', sa.String(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.String(), nullable=True),
        sa.CheckConstraint("source IN ('player', 'admin_hide')", name='ck_chat_reports_source'),
        sa.CheckConstraint("status IN ('open', 'actioned', 'dismissed')", name='ck_chat_reports_status'),
        sa.ForeignKeyConstraint(['reporter_account_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['message_id'], ['match_chat_messages.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['room_id'], ['match_chat_rooms.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['sender_account_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', 'reporter_account_id', name='uq_chat_reports_reporter'),
    )
    op.create_index('ix_chat_reports_status_created', 'chat_reports', ['status', 'created_at'])


def downgrade() -> None:
    op.drop_table('chat_reports')
    op.drop_constraint('fk_match_chat_messages_hidden_by', 'match_chat_messages', type_='foreignkey')
    op.drop_column('match_chat_messages', 'hidden_by_name')
    op.drop_column('match_chat_messages', 'hidden_by_account_id')
    op.drop_column('match_chat_messages', 'hidden_at')
