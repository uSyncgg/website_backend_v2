import uuid

from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import ARRAY

class FormParent(Base):
    """
    SQL Alchemy model describing the form_parent table.
    """

    __tablename__ = "form_parent"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    form_type: Mapped[str] = mapped_column()

class GeneralForm(Base):
    """
    SQL Alchemy model describing the general_form table.
    """

    __tablename__ = "general_form"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    form_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("form_parent.id"))
    team_name: Mapped[str] = mapped_column()
    captain_username: Mapped[str] = mapped_column()
    player_usernames: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    player_socials: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    team_email: Mapped[str] = mapped_column()
    event_name: Mapped[str] = mapped_column()
    games: Mapped[list[str]] = mapped_column(ARRAY(String), default = [])
    