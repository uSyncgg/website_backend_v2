import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import CheckConstraint, ForeignKey, DateTime, Index, UniqueConstraint, text

class BracketEvents(Base):
    """
    SQL Alchemy model describing the bracket_events table: an event a host or venue profile runs
    on uSync (a league, LAN or tournament) with its sign-ups and bracket.

    The bracket itself is one JSONB document ({ format, seeds, results, reports, noShows, log,
    published, generatedAt }) - the same shape the frontend builds from - and is rebuilt into
    matches by app.services.bracket_engine whenever it's read or changed.
    """

    __tablename__ = "bracket_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    public_id: Mapped[str] = mapped_column()
    host_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), index = True)
    name: Mapped[str] = mapped_column()
    event_type: Mapped[str] = mapped_column()
    game: Mapped[str] = mapped_column()
    format: Mapped[str] = mapped_column()
    team_size: Mapped[int] = mapped_column()
    best_of: Mapped[int] = mapped_column()
    max_entrants: Mapped[int | None] = mapped_column(nullable = True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
    location_type: Mapped[str] = mapped_column(default = "online", server_default = text("'online'"))
    venue: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    entry_fee: Mapped[str] = mapped_column(default = "Free", server_default = text("'Free'"))
    prize: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    description: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    rules: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    registration_open: Mapped[bool] = mapped_column(default = True, server_default = text("true"))
    no_show_minutes: Mapped[int | None] = mapped_column(nullable = True)
    payments: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    signup_form: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    delays: Mapped[list] = mapped_column(JSONB, default = list, server_default = text("'[]'::jsonb"))
    bracket: Mapped[dict | None] = mapped_column(JSONB, nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("public_id", name = "uq_bracket_events_public_id"),
        CheckConstraint("event_type IN ('league', 'lan', 'tournament')", name = "ck_bracket_events_type"),
        CheckConstraint("format IN ('single_elim', 'double_elim', 'round_robin')", name = "ck_bracket_events_format"),
        CheckConstraint("team_size BETWEEN 1 AND 10", name = "ck_bracket_events_team_size"),
    )

class BracketEntries(Base):
    """
    SQL Alchemy model describing the bracket_entries table: one entrant in an event, either a
    player on their own (1v1) or a team.

    status is 'signed_up' or 'pending'. A team is pending while its roster is smaller than the
    event's team size, or while the event is full; pending entries don't take a spot and aren't
    seeded. `roster` is the team's usernames, kept in step with the team until the bracket is
    published, when rosters freeze.
    """

    __tablename__ = "bracket_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE"), index = True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), index = True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete = "SET NULL"), nullable = True, index = True)
    name: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default = "signed_up", server_default = text("'signed_up'"))
    rating: Mapped[int] = mapped_column(default = 1200, server_default = text("1200"))
    email: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    answers: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    pass_name: Mapped[str | None] = mapped_column(nullable = True)
    roster: Mapped[list] = mapped_column(JSONB, default = list, server_default = text("'[]'::jsonb"))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("status IN ('signed_up', 'pending')", name = "ck_bracket_entries_status"),
        Index("uq_bracket_entries_team", "event_id", "team_id", unique = True, postgresql_where = text("team_id IS NOT NULL")),
        Index("uq_bracket_entries_solo", "event_id", "profile_id", unique = True, postgresql_where = text("team_id IS NULL")),
    )

class Notifications(Base):
    """
    SQL Alchemy model describing the notifications table: things that need an account's attention
    (team invites are their own table; these are everything else - delays, matches, disputes, ...).
    `data` holds what the frontend needs to render and link it (event id and name, team, who).
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"))
    type: Mapped[str] = mapped_column()
    data: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    read: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        Index("ix_notifications_account_created", "account_id", "created_at"),
    )
