import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, String, text, func, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

class EventFormParent(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, unique = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), default = None)
    event_type: Mapped[str] = mapped_column()

class LanEvents(Base):
    __tablename__ = "lan_events"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key = True, unique = True, default = uuid.uuid4)
    name: Mapped[str] = mapped_column()
    date: Mapped[str] = mapped_column()
    location: Mapped[str] = mapped_column()
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    fee_details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column(default = "pending")
    banner_img: Mapped[str] = mapped_column(default = "pending")
    status: Mapped[str] = mapped_column(default = "pending")
    verified: Mapped[bool] = mapped_column(default = False)
    archived: Mapped[bool] = mapped_column(default = False)
    game: Mapped[str] = mapped_column()
    drop_val: Mapped[bool] = mapped_column(server_default = text("true"))
    path: Mapped[str] = mapped_column(unique = True)
    lat: Mapped[float] = mapped_column(default = 0.0, server_default = text("0.0"))
    long: Mapped[float] = mapped_column(default = 0.0, server_default = text("0.0"))
    updated_at: Mapped[datetime] = mapped_column(server_default = func.now(), onupdate = func.now())

class LeagueParentEvents(Base):
    __tablename__ = "league_parent_events"
    __table_args__ = (
        UniqueConstraint("game", "path", name="uq_league_parent_events_game_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key = True, unique = True, default = uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    banner_img: Mapped[str] = mapped_column(default = "pending")
    header_img: Mapped[str] = mapped_column(default = "pending", server_default = text("'pending'"))
    status: Mapped[str] = mapped_column(default = "pending")
    verified: Mapped[bool] = mapped_column(default = False)
    game: Mapped[str] = mapped_column()
    path: Mapped[str] = mapped_column()
    is_hs: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    is_college: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    drop_val: Mapped[bool] = mapped_column(server_default = text("true"))
    updated_at: Mapped[datetime] = mapped_column(server_default = func.now(), onupdate = func.now())

class LeagueEvents(Base):
    __tablename__ = "league_events"
    __table_args__ = (
        UniqueConstraint("game", "path", name="uq_league_events_game_path"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key = True, unique = True, default = uuid.uuid4)
    name: Mapped[str] = mapped_column()
    seasonality: Mapped[str] = mapped_column()
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    region: Mapped[str] = mapped_column()
    team_size: Mapped[str] = mapped_column()
    fee_details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column(default = "pending")
    banner_img: Mapped[str] = mapped_column(default = "pending")
    status: Mapped[str] = mapped_column(default = "pending")
    verified: Mapped[bool] = mapped_column(default = False)
    game: Mapped[str] = mapped_column()
    drop_val: Mapped[bool] = mapped_column(server_default = text("true"))
    path: Mapped[str] = mapped_column()
    group: Mapped[str | None] = mapped_column(ForeignKey("league_parent_events.name"), default = None)
    is_hs: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    is_college: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    updated_at: Mapped[datetime] = mapped_column(server_default = func.now(), onupdate = func.now())

class XpEvents(Base):
    __tablename__ = "xp_events"
    __table_args__ = (
        UniqueConstraint("game", "path", name="uq_xp_events_game_path"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key = True, unique = True, default = uuid.uuid4)
    name: Mapped[str] = mapped_column()
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    restrictions: Mapped[dict] = mapped_column(JSONB, default = dict)
    fee_details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [], server_default="'{}'")
    availability: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column(default = "pending")
    banner_img: Mapped[str] = mapped_column(default = "pending")
    status: Mapped[str] = mapped_column(default = "pending")
    verified: Mapped[bool] = mapped_column(default = False)
    game: Mapped[str] = mapped_column()
    drop_val: Mapped[bool] = mapped_column(server_default = text("true"))
    path: Mapped[str] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(server_default = func.now(), onupdate = func.now())

class WagerEvents(Base):
    __tablename__ = "wager_events"
    __table_args__ = (
        UniqueConstraint("game", "path", name="uq_wager_events_game_path"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key = True, unique = True, default = uuid.uuid4)
    name: Mapped[str] = mapped_column()
    fee_details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    restrictions: Mapped[dict] = mapped_column(JSONB, default = dict)
    availability: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column(default = "pending")
    banner_img: Mapped[str] = mapped_column(default = "pending") 
    status: Mapped[str] = mapped_column(default = "pending")
    verified: Mapped[bool] = mapped_column(default = False)
    game: Mapped[str] = mapped_column()
    drop_val: Mapped[bool] = mapped_column(server_default = text("true"))
    path: Mapped[str] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(server_default = func.now(), onupdate = func.now())
