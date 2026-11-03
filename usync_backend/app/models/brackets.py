import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, DateTime, Index, CheckConstraint, SmallInteger, Uuid, text, func
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB

# The enum types are created by the migration, so the models never try to create them.
BRACKET_FORMAT = ENUM("single_elim", "double_elim", "round_robin", name = "bracket_format", create_type = False)
BRACKET_LOCATION = ENUM("online", "in_person", name = "bracket_location", create_type = False)
BRACKET_DECISION = ENUM("host", "players", "forfeit", "cleared", name = "bracket_decision", create_type = False)

class BracketEvents(Base):
    """
    SQL Alchemy model describing the bracket_events table: an event a host or venue runs on uSync
    with its own sign-ups and bracket. A child of events (event_type "bracket_events"), so paid
    events reuse event_pass_tiers / event_registrations and the existing Stripe checkout.

    Where the event is in its life cycle isn't stored: it's derived from registration_open and
    the bracket (registration -> closed -> seeding -> live -> complete), as the frontend does.
    """

    __tablename__ = "bracket_events"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete = "CASCADE"), primary_key = True)
    # The account that owns it (also events.user_id) and the host or venue profile it was posted
    # as, which lists it. Two-column FK so the profile can only be one of the owner's own.
    account_id: Mapped[uuid.UUID] = mapped_column(index = True)
    host_profile_id: Mapped[uuid.UUID] = mapped_column(index = True)
    name: Mapped[str] = mapped_column(String(80))
    game: Mapped[str] = mapped_column()
    format: Mapped[str] = mapped_column(BRACKET_FORMAT)
    # Players per side: 1 for 1v1 up to 5 for 5v5. Team events register a team name and captain.
    team_size: Mapped[int] = mapped_column(SmallInteger)
    best_of: Mapped[int] = mapped_column(SmallInteger)
    # None means no cap.
    max_entrants: Mapped[int | None] = mapped_column(nullable = True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
    location_type: Mapped[str] = mapped_column(BRACKET_LOCATION, server_default = text("'online'"))
    venue: Mapped[str | None] = mapped_column(nullable = True)
    description: Mapped[str] = mapped_column(String(1000), server_default = text("''"))
    rules: Mapped[str] = mapped_column(String(3000), server_default = text("''"))
    prize: Mapped[str | None] = mapped_column(nullable = True)
    check_in_required: Mapped[bool] = mapped_column(server_default = text("true"))
    # Minutes after the start before players can report a no-show. None turns the button off.
    no_show_minutes: Mapped[int | None] = mapped_column(nullable = True)
    registration_open: Mapped[bool] = mapped_column(server_default = text("true"))
    # Sign-up form every entrant fills in (free or paid): player fields from a fixed list
    # ("Discord", "Riot ID", ...) and extra questions [{ label, type: text|select, options[], required }].
    signup_player_fields: Mapped[list[str]] = mapped_column(ARRAY(String), server_default = text("'{}'"))
    signup_questions: Mapped[list[dict]] = mapped_column(JSONB, server_default = text("'[]'::jsonb"))
    # Paid events: the pass tiers live in event_pass_tiers (event_id = this event), each carrying
    # payout_email as its host_email, like the existing pass portal.
    payments_enabled: Mapped[bool] = mapped_column(server_default = text("false"))
    payout_email: Mapped[str | None] = mapped_column(nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now(), onupdate = func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "host_profile_id"],
            ["profiles.account_id", "profiles.id"],
            name = "bracket_events_host_profile_fkey",
            ondelete = "CASCADE"
        ),
        CheckConstraint("team_size BETWEEN 1 AND 5", name = "ck_bracket_events_team_size"),
        CheckConstraint("best_of IN (1, 3, 5, 7)", name = "ck_bracket_events_best_of"),
        CheckConstraint("max_entrants IS NULL OR max_entrants >= 2", name = "ck_bracket_events_max_entrants"),
        CheckConstraint("no_show_minutes IS NULL OR no_show_minutes >= 0", name = "ck_bracket_events_no_show_minutes"),
        CheckConstraint("location_type = 'online' OR venue IS NOT NULL", name = "ck_bracket_events_venue"),
        CheckConstraint("NOT payments_enabled OR payout_email IS NOT NULL", name = "ck_bracket_events_payout_email"),
    )

class BracketEventDelays(Base):
    """
    SQL Alchemy model describing the bracket_event_delays table: each time the host moved the
    start, so players see why (the latest one shows as a banner on the event page).
    """

    __tablename__ = "bracket_event_delays"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.event_id", ondelete = "CASCADE"), index = True)
    from_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
    to_starts_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
    reason: Mapped[str] = mapped_column(server_default = text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())

class BracketEntrants(Base):
    """
    SQL Alchemy model describing the bracket_entrants table: one player (1v1) or team per row.

    A signed-in player registering sets account_id, which is how they report their own scores.
    Entrants the host adds by name have no account. Paid sign-ups point at their
    event_registrations row; they only count as entered once it's paid (see the service).
    """

    __tablename__ = "bracket_entrants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.event_id", ondelete = "CASCADE"), index = True)
    # Gamertag (1v1) or team name, shown in the bracket; unique per event ignoring case.
    name: Mapped[str] = mapped_column(String(40))
    name_normalized: Mapped[str] = mapped_column()
    # The captain's gamertag (the same as name for 1v1).
    captain: Mapped[str] = mapped_column(String(40))
    teammates: Mapped[list[str]] = mapped_column(ARRAY(String), server_default = text("'{}'"))
    email: Mapped[str | None] = mapped_column(nullable = True)
    # Sign-up form answers keyed by player field or question label.
    answers: Mapped[dict] = mapped_column(JSONB, server_default = text("'{}'::jsonb"))
    # Used by the "By skill rating" seeding method.
    rating: Mapped[int] = mapped_column(server_default = text("1200"))
    checked_in: Mapped[bool] = mapped_column(server_default = text("false"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True, index = True)
    registration_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_registrations.id", ondelete = "SET NULL"), nullable = True, unique = True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())

    __table_args__ = (
        Index("uq_bracket_entrants_event_name", "event_id", "name_normalized", unique = True),
        Index(
            "uq_bracket_entrants_event_account", "event_id", "account_id",
            unique = True, postgresql_where = text("account_id IS NOT NULL"),
        ),
    )

class Brackets(Base):
    """
    SQL Alchemy model describing the brackets table: at most one per event. The bracket itself
    (matches, who plays whom, who advances) is never stored - it's rebuilt from the seeds and
    results by app/services/bracket_engine.py, the same logic the frontend runs.
    """

    __tablename__ = "brackets"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.event_id", ondelete = "CASCADE"), primary_key = True)
    # The format it was generated with (the event's format can't change once a bracket exists).
    format: Mapped[str] = mapped_column(BRACKET_FORMAT)
    # bracket_entrants ids in seed order (index 0 is seed 1). Postgres can't foreign-key array
    # items, so the service checks them and stops entrants being removed once a bracket exists.
    seeds: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid))
    published: Mapped[bool] = mapped_column(server_default = text("false"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())

class BracketMatchResults(Base):
    """
    SQL Alchemy model describing the bracket_match_results table: the result that stands for a
    match. a/b are the entrants it was decided for; if an earlier result changes who's in the
    match, this one no longer applies (it's ignored, not deleted, so a revert can bring it back).
    """

    __tablename__ = "bracket_match_results"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brackets.event_id", ondelete = "CASCADE"), primary_key = True)
    match_id: Mapped[str] = mapped_column(String(16), primary_key = True)
    entrant_a: Mapped[uuid.UUID] = mapped_column()
    entrant_b: Mapped[uuid.UUID] = mapped_column()
    score_a: Mapped[int] = mapped_column(SmallInteger)
    score_b: Mapped[int] = mapped_column(SmallInteger)
    forfeit: Mapped[bool] = mapped_column(server_default = text("false"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())

    __table_args__ = (
        CheckConstraint("score_a <> score_b", name = "ck_bracket_match_results_winner"),
        CheckConstraint("score_a >= 0 AND score_b >= 0", name = "ck_bracket_match_results_scores"),
    )

class BracketMatchReports(Base):
    """
    SQL Alchemy model describing the bracket_match_reports table: one side's reported score for a
    match it's playing. When both sides agree on the winner the result is confirmed and these are
    cleared; when they disagree the match is disputed and the host decides.
    """

    __tablename__ = "bracket_match_reports"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brackets.event_id", ondelete = "CASCADE"), primary_key = True)
    match_id: Mapped[str] = mapped_column(String(16), primary_key = True)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_entrants.id", ondelete = "CASCADE"), primary_key = True)
    entrant_a: Mapped[uuid.UUID] = mapped_column()
    entrant_b: Mapped[uuid.UUID] = mapped_column()
    score_a: Mapped[int] = mapped_column(SmallInteger)
    score_b: Mapped[int] = mapped_column(SmallInteger)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())

    __table_args__ = (
        CheckConstraint("score_a <> score_b", name = "ck_bracket_match_reports_winner"),
        CheckConstraint("score_a >= 0 AND score_b >= 0", name = "ck_bracket_match_reports_scores"),
    )

class BracketNoShows(Base):
    """
    SQL Alchemy model describing the bracket_no_shows table: a player's report that their opponent
    didn't show, waiting on the host (who can forfeit the match or dismiss it).
    """

    __tablename__ = "bracket_no_shows"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brackets.event_id", ondelete = "CASCADE"), primary_key = True)
    match_id: Mapped[str] = mapped_column(String(16), primary_key = True)
    entrant_a: Mapped[uuid.UUID] = mapped_column()
    entrant_b: Mapped[uuid.UUID] = mapped_column()
    reported_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_entrants.id", ondelete = "CASCADE"))
    against: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_entrants.id", ondelete = "CASCADE"))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())

class BracketDecisionLog(Base):
    """
    SQL Alchemy model describing the bracket_decision_log table: every result that was set or
    cleared, by whom, and what the match looked like before (prev_result / prev_reports /
    prev_no_show, as JSON) so the host can revert it.
    """

    __tablename__ = "bracket_decision_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brackets.event_id", ondelete = "CASCADE"), index = True)
    match_id: Mapped[str] = mapped_column(String(16))
    entrant_a: Mapped[uuid.UUID] = mapped_column()
    entrant_b: Mapped[uuid.UUID] = mapped_column()
    # None for 'cleared'.
    score_a: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    score_b: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    source: Mapped[str] = mapped_column(BRACKET_DECISION)
    prev_result: Mapped[dict | None] = mapped_column(JSONB, nullable = True)
    prev_reports: Mapped[dict | None] = mapped_column(JSONB, nullable = True)
    prev_no_show: Mapped[dict | None] = mapped_column(JSONB, nullable = True)
    # The account that made the call (the host, or the player whose report confirmed it).
    actor_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = func.now())
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
