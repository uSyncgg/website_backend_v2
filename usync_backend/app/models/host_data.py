import uuid

from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY

class EventFormParent(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column()

class LanEvents(Base):
    __tablename__ = "lan_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    name: Mapped[str] = mapped_column(unique = True)
    date: Mapped[str] = mapped_column()
    location: Mapped[str] = mapped_column()
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    fee: Mapped[str] = mapped_column()
    fee_details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    banner_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    status: Mapped[str] = mapped_column(default = "pending")

class LeagueEvents(Base):
    __tablename__ = "league_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    name: Mapped[str] = mapped_column(unique = True)
    seasonality: Mapped[str] = mapped_column()
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    region: Mapped[str] = mapped_column()
    team_size: Mapped[str] = mapped_column()
    fee: Mapped[str] = mapped_column()
    fee_details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    banner_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    status: Mapped[str] = mapped_column(default = "pending")

class XpEvents(Base):
    __tablename__ = "xp_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    name: Mapped[str] = mapped_column(unique = True)
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    restrictions: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    banner_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    status: Mapped[str] = mapped_column(default = "pending")

class WagerEvents(Base):
    __tablename__ = "wager_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"))
    name: Mapped[str] = mapped_column(unique = True)
    fees: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    details: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    restrictions: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    url: Mapped[str] = mapped_column()
    header_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    banner_img: Mapped[str] = mapped_column() # Figure out how to do imgs
    status: Mapped[str] = mapped_column(default = "pending")
