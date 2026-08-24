import uuid

from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, DateTime, text
from datetime import datetime

class TournamentParent(Base):
    __tablename__ = "tournaments_parent"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, unique = True)
    game: Mapped[str] = mapped_column()

# See note page right after notes on alembic to see notes about the tournaments table
class CodTournament(Base):
    __tablename__ = "cod_tournaments"

    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tournaments_parent.id"), primary_key = True, unique = True)
    event_id: Mapped[str] = mapped_column()
    date: Mapped[str] = mapped_column()
    time: Mapped[str] = mapped_column()
    entry: Mapped[str] = mapped_column()
    region: Mapped[str] = mapped_column()
    platforms: Mapped[str] = mapped_column()
    requirements: Mapped[str] = mapped_column()
    skill: Mapped[str] = mapped_column()
    team_size: Mapped[str] = mapped_column()
    gamemode: Mapped[str] = mapped_column()
    series: Mapped[str] = mapped_column()
    url: Mapped[str] = mapped_column()
    site: Mapped[str] = mapped_column()
    is_1v1: Mapped[bool] = mapped_column()
    is_2v2: Mapped[bool] = mapped_column()
    is_3v3: Mapped[bool] = mapped_column()
    is_4v4: Mapped[bool] = mapped_column()
    is_na: Mapped[bool] = mapped_column()
    is_eu: Mapped[bool] = mapped_column()
    is_latam: Mapped[bool] = mapped_column()
    is_usa: Mapped[bool] = mapped_column()
    is_apac: Mapped[bool] = mapped_column()
    is_worldwide: Mapped[bool] = mapped_column()
    is_pc: Mapped[bool] = mapped_column()
    is_console: Mapped[bool] = mapped_column()
    is_all_platforms: Mapped[bool] = mapped_column()
    is_novice: Mapped[bool] = mapped_column()
    is_amateur: Mapped[bool] = mapped_column()
    is_expert: Mapped[bool] = mapped_column()
    is_agent: Mapped[bool] = mapped_column()
    is_master: Mapped[bool] = mapped_column()
    is_challenger: Mapped[bool] = mapped_column()
    is_all_skill: Mapped[bool] = mapped_column()
    is_free: Mapped[bool] = mapped_column()
    is_paid: Mapped[bool] = mapped_column()
    is_eco: Mapped[bool] = mapped_column()
    is_elite: Mapped[bool] = mapped_column()
    drop_val: Mapped[bool] = mapped_column(server_default = text("true"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
