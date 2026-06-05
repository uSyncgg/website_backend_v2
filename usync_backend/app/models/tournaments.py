import uuid

from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY

class TournamentParent(Base):
    __tablename__ = "tournaments_parent"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    game: Mapped[str] = mapped_column()
    url: Mapped[str] = mapped_column()

# See note page right after notes on alembic to see notes about the tournaments table
class CodTournament(Base):
    __tablename__ = "cod_tournaments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    tournament_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tournaments_parent.id"))
    date: Mapped[str] = mapped_column()
    time: Mapped[str] = mapped_column()
    entry: Mapped[str] = mapped_column()
    region: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    platforms: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    requirements: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    skill: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    team_size: Mapped[str] = mapped_column()
    gamemode: Mapped[str] = mapped_column()
    series: Mapped[str] = mapped_column()
    url: Mapped[str] = mapped_column()
