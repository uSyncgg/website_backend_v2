import uuid

from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY

class UsersParent(Base):
    """
    SQL Alchemy model describing the users table.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True)
    username: Mapped[str | None] = mapped_column(nullable = True)
    email: Mapped[str] = mapped_column()
    status: Mapped[str | None] = mapped_column(nullable = True)
    
class Players(Base):
    """
    SQL Alchemy model describing the players table.    
    """
    
    __tablename__ = "players"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(unique = True)
    username: Mapped[str] = mapped_column(unique = True)
    battlenet: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    activision: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    steam: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    riot: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    games: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    # Don't forget profile pic

class Hosts(Base):
    """
    SQL Alchemy model describing the hosts table.
    """

    __tablename__ = "hosts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    email: Mapped[str] = mapped_column(unique = True)
    username: Mapped[str] = mapped_column(unique = True)
    games: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    event_site: Mapped[str | None] = mapped_column(nullable = True)
    # Don't forget profile pic

class Socials(Base):
    """
    SQL Alchemy model describing the socials table.
    """

    __tablename__ = "socials"

    # Wont need user id since we will have this table be generated when a new user is generated
    id: Mapped[uuid.UUID] = mapped_column(primary_key = True)
    instagram: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    twitter: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    discord: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    youtube: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    twitch: Mapped[str | None] = mapped_column(unique = True, nullable = True)
    # Ask about other socials

class EventSitesParent(Base):
    """
    SQL Alchemy model describing the event_site_usernames table.
    """
    __tablename__ = "event_site_usernames"

    # Wont need user id since we will have this table be generated when a new player is generated
    # We want to make sure this is filled out after the user selects what games they play
    # If they ever remove a game from their acct we delete the entry, if they add another we add a new entry, etc.
    id: Mapped[uuid.UUID] = mapped_column(primary_key = True)
    game: Mapped[str] = mapped_column()

class CodEventSite(Base):
    """
    SQL Alchemy model describing the cod_site_usernames table.
    """

    __tablename__ = "cod_site_usernames"

    # When these are linked is when we will want to create entries in site + tourney stats
    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_site_usernames.id"))
    cmg: Mapped[uuid.UUID | None] = mapped_column(unique = True, nullable = True)
    gb: Mapped[uuid.UUID | None] = mapped_column(unique = True, nullable = True)
    
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
