from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, SmallInteger, UniqueConstraint

# Lookup tables for what an event (or a team) plays. They're maintained by migrations only - no
# API writes to them - so rows change only with a deploy.
#
#   games             Call of Duty, Halo, ...
#   game_titles       a game's titles (Black Ops 7, Halo Infinite). Optional: a game with no
#                     titles has no rows, and its events leave the title empty
#   game_modes        a game's modes (Search and Destroy, Slayer, ...). Every game has at least
#                     one, and every event picks one
#   game_title_modes  which modes each title offers (many to many: titles share modes); only
#                     used for games that have titles
#   rulesets          official rulesets (GLOSSARY.md) a host picks from: general to a game, or
#                     for one of its titles. Once a game has any, every event picks one

class Games(Base):
    """
    SQL Alchemy model describing the games table: one row per game uSync supports.
    `key` is the stable identifier the API and frontend use ("call_of_duty").
    """

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key = True)
    key: Mapped[str] = mapped_column(unique = True)
    name: Mapped[str] = mapped_column()
    sort_order: Mapped[int] = mapped_column(SmallInteger, default = 0, server_default = "0")

class GameTitles(Base):
    """
    SQL Alchemy model describing the game_titles table: the titles in a game's series.
    """

    __tablename__ = "game_titles"

    id: Mapped[int] = mapped_column(primary_key = True)
    game_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("games.id", ondelete = "RESTRICT"), index = True)
    key: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    release_year: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)

    __table_args__ = (
        UniqueConstraint("game_id", "key", name = "uq_game_titles_game_key"),
    )

class GameModes(Base):
    """
    SQL Alchemy model describing the game_modes table: the modes a game has.
    """

    __tablename__ = "game_modes"

    id: Mapped[int] = mapped_column(primary_key = True)
    game_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("games.id", ondelete = "RESTRICT"), index = True)
    key: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()

    __table_args__ = (
        UniqueConstraint("game_id", "key", name = "uq_game_modes_game_key"),
    )

class GameTitleModes(Base):
    """
    SQL Alchemy model describing the game_title_modes table: which modes a title offers.
    """

    __tablename__ = "game_title_modes"

    title_id: Mapped[int] = mapped_column(ForeignKey("game_titles.id", ondelete = "CASCADE"), primary_key = True)
    mode_id: Mapped[int] = mapped_column(ForeignKey("game_modes.id", ondelete = "CASCADE"), primary_key = True, index = True)

class Rulesets(Base):
    """
    SQL Alchemy model describing the rulesets table: the official rulesets hosts pick from. A
    ruleset with no title is general, offered on every title of its game; one with a title is
    offered only for that title. uSync only links to the rules (url), it doesn't copy them.
    """

    __tablename__ = "rulesets"

    id: Mapped[int] = mapped_column(primary_key = True)
    game_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("games.id", ondelete = "RESTRICT"), index = True)
    game_title_id: Mapped[int | None] = mapped_column(ForeignKey("game_titles.id", ondelete = "RESTRICT"), nullable = True)
    key: Mapped[str] = mapped_column()
    name: Mapped[str] = mapped_column()
    url: Mapped[str] = mapped_column()
    sort_order: Mapped[int] = mapped_column(SmallInteger, default = 0, server_default = "0")

    __table_args__ = (
        UniqueConstraint("game_id", "key", name = "uq_rulesets_game_key"),
    )
