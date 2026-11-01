import uuid

from datetime import date, datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, DateTime, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, ENUM

# The enum types are created by the migration, so the models never try to create them.
PROFILE_TYPE = ENUM("player", "host", "venue", name = "profile_type", create_type = False)
LINK_PLATFORM = ENUM("twitch", "twitter", "youtube", "kick", "discord", "instagram", name = "link_platform", create_type = False)

class UsersParent(Base):
    """
    SQL Alchemy model describing the users table. One row per account: only account-level data
    lives here. Usernames and everything shown on a profile live on that account's profiles.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, unique = True)
    email: Mapped[str] = mapped_column()
    verified: Mapped[bool] = mapped_column(default = False, server_default=text("false"))
    default_profile_id: Mapped[uuid.UUID | None] = mapped_column(nullable = True)

    __table_args__ = (
        # Two-column FK so the default can only be one of this account's own profiles.
        ForeignKeyConstraint(
            ["id", "default_profile_id"],
            ["profiles.account_id", "profiles.id"],
            name = "fk_users_default_profile",
            use_alter = True
        ),
    )

class Profiles(Base):
    """
    SQL Alchemy model describing the profiles table. One row per public profile; an account has
    at most one profile of each type. The type's own details live in players, hosts or venues.
    """

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    public_id: Mapped[str] = mapped_column()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"))
    type: Mapped[str] = mapped_column(PROFILE_TYPE)
    username: Mapped[str] = mapped_column()
    canonical_username: Mapped[str] = mapped_column()
    bio: Mapped[str | None] = mapped_column(nullable = True)
    profile_picture: Mapped[str | None] = mapped_column(nullable = True)
    bracket_hosting: Mapped[bool] = mapped_column(default = True, server_default=text("true"))
    other_roles: Mapped[list[str] | None] = mapped_column(ARRAY(String), default = [], nullable = True)
    other_role_detail: Mapped[str | None] = mapped_column(nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("public_id", name = "uq_profiles_public_id"),
        UniqueConstraint("type", "canonical_username", name = "uq_profiles_type_canonical_username"),
        UniqueConstraint("account_id", "type", name = "uq_profiles_account_type"),
        UniqueConstraint("account_id", "id", name = "uq_profiles_account_id_id"),
    )

class ProfileLinks(Base):
    """
    SQL Alchemy model describing the profile_links table: one social handle per platform per profile.
    """

    __tablename__ = "profile_links"

    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), primary_key = True)
    platform: Mapped[str] = mapped_column(LINK_PLATFORM, primary_key = True)
    handle: Mapped[str] = mapped_column()

class Players(Base):
    """
    SQL Alchemy model describing the players table: the details of a player profile.
    """
    
    __tablename__ = "players"
    
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), primary_key = True)
    first_name: Mapped[str] = mapped_column()
    last_name: Mapped[str] = mapped_column()
    phone_number: Mapped[str | None] = mapped_column(nullable = True)
    gender: Mapped[str | None] = mapped_column(nullable = True)
    date_of_birth: Mapped[date | None] = mapped_column(nullable = True)
    country: Mapped[str | None] = mapped_column(nullable = True)
    battlenet: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    activision: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    steam: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    riot: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    games: Mapped[list[str]] = mapped_column(ARRAY(String), default = [], nullable = True)
    other_games: Mapped[str | None] = mapped_column(nullable = True)

class Hosts(Base):
    """
    SQL Alchemy model describing the hosts table: the details of a host profile.
    """

    __tablename__ = "hosts"

    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), primary_key = True)
    games: Mapped[list[str]] = mapped_column(ARRAY(String), default = [], nullable = True)
    other_games: Mapped[str | None] = mapped_column(nullable = True)
    organization: Mapped[str] = mapped_column()
    host_country: Mapped[str] = mapped_column()
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), default = [], nullable = True)

class Venues(Base):
    """
    SQL Alchemy model describing the venues table: one row per location of a venue profile.
    """

    __tablename__ = "venues"

    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), primary_key = True)
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
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.profile_id", ondelete = "CASCADE"))
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
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"), index = True)
    stat_type: Mapped[str] = mapped_column()

class LanStatistics(Base):
    """
    SQL Alchemy model describing the lan_statistics table.
    """

    __tablename__ = "lan_statistics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    stat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("statistics.id", ondelete = "CASCADE"), index = True)
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
    stat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("statistics.id", ondelete = "CASCADE"), index = True)
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
    stat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("statistics.id", ondelete = "CASCADE"), index = True)
    site_url: Mapped[str | None] = mapped_column(nullable = True)
    elites: Mapped[int | None] = mapped_column(nullable = True)
    golds: Mapped[int | None] = mapped_column(nullable = True)
    silvers: Mapped[int | None] = mapped_column(nullable = True)
    bronzes: Mapped[int | None] = mapped_column(nullable = True)
    status: Mapped[str] = mapped_column(default = "pending")
