import uuid

from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, String, Index, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY

class UsersParent(Base):
    """
    SQL Alchemy model describing the users table.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, unique = True)
    username: Mapped[str] = mapped_column()
    canonical_username: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column()
    is_player: Mapped[bool] = mapped_column()
    is_host: Mapped[bool] = mapped_column()
    twitch: Mapped[str | None] = mapped_column(nullable = True)
    twitter: Mapped[str | None] = mapped_column(nullable = True)
    youtube: Mapped[str | None] = mapped_column(nullable = True)
    kick: Mapped[str | None] = mapped_column(nullable = True)
    discord: Mapped[str | None] = mapped_column(nullable = True)
    instagram: Mapped[str | None] = mapped_column(nullable = True)
    profile_picture: Mapped[str | None] = mapped_column(nullable = True)
    bio: Mapped[str | None] = mapped_column(nullable = True)
    bracket_hosting: Mapped[bool] = mapped_column(default = True, server_default=text("true"))
    other_roles: Mapped[list[str] | None] = mapped_column(ARRAY(String), default = [], nullable = True)
    other_role_detail: Mapped[str | None] = mapped_column(nullable = True)
    verified: Mapped[bool] = mapped_column(default = False, server_default=text("false"))

    __table_args__ = (
        Index("ix_users_username_player_unique", "canonical_username", unique=True,
            postgresql_where=text("is_player = true")),
        Index("ix_users_username_host_unique", "canonical_username", unique=True,
            postgresql_where=text("is_host = true")),
    )
    
class Players(Base):
    """
    SQL Alchemy model describing the players table.    
    """
    
    __tablename__ = "players"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    first_name: Mapped[str] = mapped_column()
    last_name: Mapped[str] = mapped_column()
    phone_number: Mapped[str | None] = mapped_column(nullable = True)
    gender: Mapped[str | None] = mapped_column(nullable = True)
    date_of_birth: Mapped[str | None] = mapped_column(nullable = True)
    country: Mapped[str | None] = mapped_column(nullable = True)
    battlenet: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    activision: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    steam: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    riot: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    games: Mapped[list[str]] = mapped_column(ARRAY(String), default = [], nullable = True)
    other_games: Mapped[str | None] = mapped_column(nullable = True)
    # Don't forget profile pic

class Hosts(Base):
    """
    SQL Alchemy model describing the hosts table.
    """

    __tablename__ = "hosts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    games: Mapped[list[str]] = mapped_column(ARRAY(String), default = [], nullable = True)
    other_games: Mapped[str | None] = mapped_column(nullable = True)
    organization: Mapped[str] = mapped_column()
    host_country: Mapped[str] = mapped_column()
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), default = [], nullable = True)
    # Don't forget profile pic

class Venues(Base):
    """
    
    """

    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key = True, default = uuid.uuid4)
    venue_name: Mapped[str] = mapped_column(primary_key = True)
    location: Mapped[str] = mapped_column()

class CompetitiveSites(Base):
    """
    
    """

    __tablename__ = "competitive_sites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, unique = True, default = uuid.uuid4)
    name: Mapped[str] = mapped_column()

class CompetitiveSiteGames(Base):
    """
    
    """

    __tablename__ = "competitive_site_games"

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitive_sites.id"), primary_key = True)
    game: Mapped[str] = mapped_column(primary_key = True)

class PlayerCompSiteAccounts(Base):
    """
    
    """

    __tablename__ = "player_comp_site_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, unique = True, default = uuid.uuid4)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"))
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitive_sites.id"))
    username: Mapped[str] = mapped_column()

    __table_args__ = (
        UniqueConstraint("player_id", "site_id", name = "uq_player_id_comp_site_id"),
    )
    
class StatisticsParent(Base):
    """
    SQL Alchemy model describing the statistics table.
    """

    __tablename__ = "statistics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    stat_type: Mapped[str] = mapped_column()

class LanStatistics(Base):
    """
    SQL Alchemy model describing the lan_statistics table.
    """

    __tablename__ = "lan_statistics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    stat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("statistics.id"))
    placing: Mapped[str | None] = mapped_column(nullable = True)
    event_name: Mapped[str | None] = mapped_column(nullable = True)
    proof: Mapped[str | None] = mapped_column(nullable = True)
    status: Mapped[str] = mapped_column(default = "pending")

class SiteStatistics(Base):
    """
    SQL Alchemy model describing the site_statistics table.
    """

    __tablename__ = "site_statistics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    stat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("statistics.id"))
    earnings: Mapped[str | None] = mapped_column(nullable = True)
    wins: Mapped[str | None] = mapped_column(nullable = True)
    losses: Mapped[str | None] = mapped_column(nullable = True)
    status: Mapped[str] = mapped_column(default = "pending")
    site_url: Mapped[str | None] = mapped_column(nullable = True)

class TournamentStatistics(Base):
    """
    SQL Alchemy model describing the tournament_statistics table.
    """

    __tablename__ = "tournament_statistics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    stat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("statistics.id"))
    site_url: Mapped[str | None] = mapped_column(nullable = True)
    elites: Mapped[int | None] = mapped_column(nullable = True)
    golds: Mapped[int | None] = mapped_column(nullable = True)
    silvers: Mapped[int | None] = mapped_column(nullable = True)
    bronzes: Mapped[int | None] = mapped_column(nullable = True)
    status: Mapped[str] = mapped_column(default = "pending")
