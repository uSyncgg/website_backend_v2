"""Match chats: chats with a match subtype, members, messages, and who may listen on Realtime.

Revision ID: a7c3e9f1b2d4
Revises: f9b1d3e5a7c9
Create Date: 2026-11-22 15:00:00.000000

Written by hand. See GLOSSARY.md (Chat) and docs/adr/0002-chat-realtime-via-broadcast.md.

1. chats (what every chat has), match_chats (the match subtype), chat_members, chat_messages.
   RLS on all four with no policies: only the backend reads or writes them.
2. A trigger so deleting a match_chats row (its match, bracket or event was deleted) deletes
   the chat too, with its members and messages. A cascade can't go from child to parent.
3. A trigger that refuses any UPDATE of a chat message: messages are never edited.
4. event_issues type 'admin_requested' ("Call an admin" in a match chat).
5. Realtime: is_open_chat_member(topic) and a SELECT policy on realtime.messages, so a signed-in
   user can listen on the private channel chat:{id} only while they're a member and the chat is
   open. No INSERT policy: browsers can never broadcast; the backend sends every message.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3e9f1b2d4'
down_revision: Union[str, Sequence[str], None] = 'f9b1d3e5a7c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ISSUE_TYPES_BEFORE = "'dispute', 'one_sided_report', 'no_show', 'post_advancement_dispute', 'stalled_match'"
CHAT_TABLES = ("chats", "match_chats", "chat_members", "chat_messages")


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Tables
    op.create_table(
        'chats',
        sa.Column('id', sa.Uuid(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('closes_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closing_warned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('match')", name='ck_chats_kind'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chats_open', 'chats', ['closes_at'], postgresql_where=sa.text('closed_at IS NULL'))

    op.create_table(
        'match_chats',
        sa.Column('chat_id', sa.Uuid(), nullable=False),
        sa.Column('match_id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['match_id'], ['bracket_matches.id'], ondelete='CASCADE', deferrable=True, initially='DEFERRED'),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE', deferrable=True, initially='DEFERRED'),
        sa.PrimaryKeyConstraint('chat_id'),
        sa.UniqueConstraint('match_id'),
    )
    op.create_index('ix_match_chats_event_id', 'match_chats', ['event_id'])

    op.create_table(
        'chat_members',
        sa.Column('chat_id', sa.Uuid(), nullable=False),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_read_message_id', sa.BigInteger(), nullable=True),
        sa.CheckConstraint("role IN ('player', 'admin')", name='ck_chat_members_role'),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('chat_id', 'profile_id'),
    )
    op.create_index('ix_chat_members_account', 'chat_members', ['account_id'])

    op.create_table(
        'chat_messages',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column('chat_id', sa.Uuid(), nullable=False),
        sa.Column('sender_profile_id', sa.Uuid(), nullable=True),
        sa.Column('sender_role', sa.String(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('original_body', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("sender_role IN ('player', 'admin', 'system')", name='ck_chat_messages_sender_role'),
        sa.CheckConstraint("(sender_role = 'system') = (sender_profile_id IS NULL)", name='ck_chat_messages_system_sender'),
        sa.CheckConstraint('char_length(body) BETWEEN 1 AND 500', name='ck_chat_messages_body_length'),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_profile_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chat_messages_chat', 'chat_messages', ['chat_id', 'id'])

    for table in CHAT_TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')

    # 2. A deleted match chat takes its chat with it
    op.execute("""
        CREATE FUNCTION delete_subtype_chat() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            DELETE FROM chats WHERE id = OLD.chat_id;
            RETURN NULL;
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER match_chats_delete_chat AFTER DELETE ON match_chats
            FOR EACH ROW EXECUTE FUNCTION delete_subtype_chat()
    """)

    # 3. Messages are never edited
    op.execute("""
        CREATE FUNCTION chat_messages_no_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Chat messages are never edited';
        END
        $$
    """)
    op.execute("""
        CREATE TRIGGER chat_messages_no_update BEFORE UPDATE ON chat_messages
            FOR EACH ROW EXECUTE FUNCTION chat_messages_no_update()
    """)

    # 4. "Call an admin" issues
    op.drop_constraint('ck_event_issues_type', 'event_issues', type_='check')
    op.create_check_constraint('ck_event_issues_type', 'event_issues', f"type IN ({ISSUE_TYPES_BEFORE}, 'admin_requested')")

    # 5. Who may listen on chat:{id}
    op.execute("""
        CREATE FUNCTION public.is_open_chat_member(topic text) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
            SELECT EXISTS (
                SELECT 1
                FROM public.chat_members m
                JOIN public.chats c ON c.id = m.chat_id
                WHERE m.account_id = (SELECT auth.uid())
                  AND c.closed_at IS NULL
                  AND 'chat:' || c.id::text = topic
            )
        $$
    """)
    op.execute('REVOKE EXECUTE ON FUNCTION public.is_open_chat_member(text) FROM PUBLIC, anon')
    op.execute('GRANT EXECUTE ON FUNCTION public.is_open_chat_member(text) TO authenticated')
    op.execute("""
        CREATE POLICY chat_members_receive ON realtime.messages
            FOR SELECT
            TO authenticated
            USING (
                realtime.messages.extension = 'broadcast'
                AND public.is_open_chat_member((SELECT realtime.topic()))
            )
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('DROP POLICY chat_members_receive ON realtime.messages')
    op.execute('DROP FUNCTION public.is_open_chat_member(text)')
    op.execute("DELETE FROM event_issues WHERE type = 'admin_requested'")
    op.drop_constraint('ck_event_issues_type', 'event_issues', type_='check')
    op.create_check_constraint('ck_event_issues_type', 'event_issues', f"type IN ({ISSUE_TYPES_BEFORE})")
    op.drop_table('chat_messages')
    op.drop_table('chat_members')
    op.execute('DROP TRIGGER match_chats_delete_chat ON match_chats')
    op.drop_table('match_chats')
    op.drop_table('chats')
    op.execute('DROP FUNCTION chat_messages_no_update()')
    op.execute('DROP FUNCTION delete_subtype_chat()')
