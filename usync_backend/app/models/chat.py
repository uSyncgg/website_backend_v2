import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, DateTime, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

class MatchChatRooms(Base):
    """
    SQL Alchemy model describing the match_chat_rooms table: the chat room for one match of a
    bracket event. A room is created for every match once the bracket is published.

    side_a / side_b are the entrant ids the room was last used by; when a result is changed and
    a different pairing lands in the match, the old conversation is wiped (see app.services.chat_rooms).
    admin_requested_at is set when a player asks for an admin, or when a dispute or no-show invites
    one automatically (admin_request_reason says which). expires_at is set when the event finishes
    (3 days later) and the sweeper deletes the room, its messages and its admin joins.
    """

    __tablename__ = "match_chat_rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE"), index = True)
    match_id: Mapped[str] = mapped_column()
    side_a: Mapped[str | None] = mapped_column(nullable = True)
    side_b: Mapped[str | None] = mapped_column(nullable = True)
    admin_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    admin_request_reason: Mapped[str | None] = mapped_column(nullable = True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True, index = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("event_id", "match_id", name = "uq_match_chat_rooms_match"),
        CheckConstraint(
            "admin_request_reason IS NULL OR admin_request_reason IN ('requested', 'dispute', 'no_show')",
            name = "ck_match_chat_rooms_reason",
        ),
    )

class MatchChatMessages(Base):
    """
    SQL Alchemy model describing the match_chat_messages table: one line in a match chat.

    kind is 'user' (typed by a player or an admin) or 'system' ("an admin was requested", "X
    joined"). sender_name is kept on the row so the line still reads right after a profile is
    renamed or the account is closed; sender_account_id goes NULL when the account is deleted.
    The bigint id doubles as the polling cursor.

    An admin can hide a player's message (hidden_at): players then see a placeholder, admins still
    see the text. Hiding files a report with uSync staff automatically (see ChatReports).
    """

    __tablename__ = "match_chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key = True, autoincrement = True)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_chat_rooms.id", ondelete = "CASCADE"), index = True)
    kind: Mapped[str] = mapped_column(default = "user", server_default = text("'user'"))
    sender_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True)
    sender_name: Mapped[str | None] = mapped_column(nullable = True)
    sender_role: Mapped[str | None] = mapped_column(nullable = True)
    body: Mapped[str] = mapped_column()
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    hidden_by_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True)
    hidden_by_name: Mapped[str | None] = mapped_column(nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("kind IN ('user', 'system')", name = "ck_match_chat_messages_kind"),
        CheckConstraint("sender_role IS NULL OR sender_role IN ('player', 'admin')", name = "ck_match_chat_messages_role"),
        Index("ix_match_chat_messages_room_id_id", "room_id", "id"),
    )

class MatchChatAdmins(Base):
    """
    SQL Alchemy model describing the match_chat_admins table: the admins (the host and accepted
    event admins) who joined a match chat. Joining is what unlocks the chat history for them.
    """

    __tablename__ = "match_chat_admins"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_chat_rooms.id", ondelete = "CASCADE"), index = True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"))
    name: Mapped[str] = mapped_column()
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("room_id", "account_id", name = "uq_match_chat_admins"),
    )

class ChatReports(Base):
    """
    SQL Alchemy model describing the chat_reports table: a message flagged to uSync staff (never
    to the event's admins). source is 'player' (someone in the chat reported it) or 'admin_hide'
    (an admin hid it, which reports it automatically).

    A report has to outlive the chat, which is deleted 3 days after the event, so it keeps its own
    copy of the message and a few lines around it (context) and plain-text names; the room and
    message links go NULL when the chat is deleted. Staff work the queue in the admin dashboard's
    Moderation tab. Resolved reports are deleted a year after they were reviewed.
    """

    __tablename__ = "chat_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    source: Mapped[str] = mapped_column()
    category: Mapped[str] = mapped_column()
    note: Mapped[str | None] = mapped_column(nullable = True)
    status: Mapped[str] = mapped_column(default = "open", server_default = text("'open'"))
    reporter_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True)
    reporter_name: Mapped[str | None] = mapped_column(nullable = True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("match_chat_messages.id", ondelete = "SET NULL"), nullable = True)
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("match_chat_rooms.id", ondelete = "SET NULL"), nullable = True)
    event_public_id: Mapped[str] = mapped_column()
    event_name: Mapped[str] = mapped_column()
    match_id: Mapped[str] = mapped_column()
    sender_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True)
    sender_name: Mapped[str | None] = mapped_column(nullable = True)
    sender_role: Mapped[str | None] = mapped_column(nullable = True)
    message_body: Mapped[str] = mapped_column()
    context: Mapped[list] = mapped_column(JSONB, default = list, server_default = text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    reviewed_by_email: Mapped[str | None] = mapped_column(nullable = True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    resolution_note: Mapped[str | None] = mapped_column(nullable = True)

    __table_args__ = (
        UniqueConstraint("message_id", "reporter_account_id", name = "uq_chat_reports_reporter"),
        CheckConstraint("source IN ('player', 'admin_hide')", name = "ck_chat_reports_source"),
        CheckConstraint("status IN ('open', 'actioned', 'dismissed')", name = "ck_chat_reports_status"),
        Index("ix_chat_reports_status_created", "status", "created_at"),
    )
