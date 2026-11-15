"""match chat rooms: rooms, messages and the admins who joined them

Revision ID: f4a9c2d7b6e1
Revises: e7c1a4b92d3f
Create Date: 2026-11-08 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f4a9c2d7b6e1'
down_revision: Union[str, Sequence[str], None] = 'e7c1a4b92d3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'match_chat_rooms',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('match_id', sa.String(), nullable=False),
        sa.Column('side_a', sa.String(), nullable=True),
        sa.Column('side_b', sa.String(), nullable=True),
        sa.Column('admin_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('admin_request_reason', sa.String(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("admin_request_reason IS NULL OR admin_request_reason IN ('requested', 'dispute', 'no_show')", name='ck_match_chat_rooms_reason'),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'match_id', name='uq_match_chat_rooms_match'),
    )
    op.create_index('ix_match_chat_rooms_event_id', 'match_chat_rooms', ['event_id'])
    op.create_index('ix_match_chat_rooms_expires_at', 'match_chat_rooms', ['expires_at'])

    op.create_table(
        'match_chat_messages',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('room_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.String(), server_default=sa.text("'user'"), nullable=False),
        sa.Column('sender_account_id', sa.Uuid(), nullable=True),
        sa.Column('sender_name', sa.String(), nullable=True),
        sa.Column('sender_role', sa.String(), nullable=True),
        sa.Column('body', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind IN ('user', 'system')", name='ck_match_chat_messages_kind'),
        sa.CheckConstraint("sender_role IS NULL OR sender_role IN ('player', 'admin')", name='ck_match_chat_messages_role'),
        sa.ForeignKeyConstraint(['room_id'], ['match_chat_rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_account_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_match_chat_messages_room_id', 'match_chat_messages', ['room_id'])
    op.create_index('ix_match_chat_messages_room_id_id', 'match_chat_messages', ['room_id', 'id'])

    op.create_table(
        'match_chat_admins',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('room_id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['match_chat_rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('room_id', 'account_id', name='uq_match_chat_admins'),
    )
    op.create_index('ix_match_chat_admins_room_id', 'match_chat_admins', ['room_id'])


def downgrade() -> None:
    op.drop_table('match_chat_admins')
    op.drop_table('match_chat_messages')
    op.drop_table('match_chat_rooms')
