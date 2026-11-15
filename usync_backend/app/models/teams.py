import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import CheckConstraint, ForeignKey, DateTime, Index, SmallInteger, UniqueConstraint, text

class Teams(Base):
    """
    SQL Alchemy model describing the teams table. A team is owned by one profile (an org's, or a
    player's own team). The owner manages it (invites players, sets captains) and pays for its
    event entries.
    """

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    public_id: Mapped[str] = mapped_column()
    owner_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), index = True)
    name: Mapped[str] = mapped_column()
    canonical_name: Mapped[str] = mapped_column()
    picture: Mapped[str | None] = mapped_column(nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("public_id", name = "uq_teams_public_id"),
        UniqueConstraint("owner_profile_id", "canonical_name", name = "uq_teams_owner_canonical_name"),
    )

class TeamGames(Base):
    """
    SQL Alchemy model describing the team_games table: the games a team plays (at least one,
    enforced by the API). A team can enter events for any of its games.
    """

    __tablename__ = "team_games"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete = "CASCADE"), primary_key = True)
    game_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("games.id", ondelete = "RESTRICT"), primary_key = True, index = True)

class TeamMembers(Base):
    """
    SQL Alchemy model describing the team_members table: the player profiles on a team. A
    captain can enter the team in events; the owner always can.
    """

    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete = "CASCADE"), primary_key = True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), primary_key = True, index = True)
    role: Mapped[str] = mapped_column(default = "player", server_default = text("'player'"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("role IN ('captain', 'player')", name = "ck_team_members_role"),
    )

class TeamInvites(Base):
    """
    SQL Alchemy model describing the team_invites table: a team's owner inviting a player profile
    onto the team. Only one pending invite per player per team. status: 'pending', 'accepted',
    'declined', or 'cancelled' (the owner withdrew it).
    """

    __tablename__ = "team_invites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete = "CASCADE"), index = True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), index = True)
    # Who sent it (the team's owner profile at the time); null if that profile is later deleted.
    invited_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True)
    status: Mapped[str] = mapped_column(default = "pending", server_default = text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    # When it was accepted, declined or cancelled. Non-pending invites are deleted 90 days after this.
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'declined', 'cancelled')", name = "ck_team_invites_status"),
        Index("uq_team_invites_pending", "team_id", "profile_id", unique = True, postgresql_where = text("status = 'pending'")),
    )
