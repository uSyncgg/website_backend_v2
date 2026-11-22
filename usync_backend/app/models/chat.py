import uuid

from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base

# Chats (see GLOSSARY.md "Chat" and docs/adr/0002-chat-realtime-via-broadcast.md). `chats` holds
# only what every kind of chat has; each kind gets its own subtype table keyed by the chat's id
# (match_chats for now), so `chats` never grows a column per kind. The browser never reads these
# tables: RLS is on with no policies. Messages reach browsers as Realtime broadcasts on the
# private channel chat:{id}, which only open chat members may listen to (migration a7c3e9f1b2d4).

CHAT_KINDS = ("match",)

class Chats(Base):
    """
    SQL Alchemy model describing the chats table: one conversation. closes_at is when it will
    close (null while that isn't known yet, or for a chat that never closes); closing_warned_at
    is when its members were told it's about to close; closed_at is when it closed, after which
    nobody can see or write in it again.
    """

    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, server_default = text("gen_random_uuid()"))
    kind: Mapped[str] = mapped_column()
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    closing_warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        CheckConstraint("kind IN ('match')", name = "ck_chats_kind"),
        Index("ix_chats_open", "closes_at", postgresql_where = text("closed_at IS NULL")),
    )

class MatchChats(Base):
    """
    SQL Alchemy model describing the match_chats table: the match chat subtype of a chat. One per
    bracket match, opened once both sides are known. Deleting the match (its event or bracket
    deleted) deletes this row, and a trigger then deletes the chat with its members and messages.
    """

    __tablename__ = "match_chats"

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete = "CASCADE"), primary_key = True)
    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_matches.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), unique = True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), index = True)

class ChatMembers(Base):
    """
    SQL Alchemy model describing the chat_members table: a profile that can read and write in a
    chat. Match chat players are added when the chat opens; event admins when they join.
    account_id is the profile's account, kept here so the Realtime policy can check auth.uid()
    without a join. last_read_message_id drives the "new messages" highlight.
    """

    __tablename__ = "chat_members"

    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete = "CASCADE"), primary_key = True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), primary_key = True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"))
    role: Mapped[str] = mapped_column()
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    last_read_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable = True)

    __table_args__ = (
        CheckConstraint("role IN ('player', 'admin')", name = "ck_chat_members_role"),
        Index("ix_chat_members_account", "account_id"),
    )

class ChatMessages(Base):
    """
    SQL Alchemy model describing the chat_messages table: one message, never edited (a trigger
    refuses updates). body is what members see, with profanity masked; original_body is what was
    typed, only when masking changed it, for uSync staff. A system message (joins, admin calls,
    closing warnings) has no sender. The id is the order and the "after" cursor.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always = True), primary_key = True)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete = "CASCADE"))
    sender_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), nullable = True)
    sender_role: Mapped[str] = mapped_column()
    body: Mapped[str] = mapped_column(Text)
    original_body: Mapped[str | None] = mapped_column(Text, nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("sender_role IN ('player', 'admin', 'system')", name = "ck_chat_messages_sender_role"),
        CheckConstraint("(sender_role = 'system') = (sender_profile_id IS NULL)", name = "ck_chat_messages_system_sender"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 500", name = "ck_chat_messages_body_length"),
        Index("ix_chat_messages_chat", "chat_id", "id"),
    )
