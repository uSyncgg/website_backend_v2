import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, DateTime, Index, SmallInteger, UniqueConstraint, text

class BracketFormats(Base):
    """
    SQL Alchemy model describing the bracket_formats table: the formats an event can use, as
    configuration (label, blurb, whether there's a single deciding final, the smallest field it
    can run with). How a format pairs entrants is code - one builder per key in
    app.services.bracket_engine - so adding a format is one row here plus one function there.
    Maintained by migrations only.
    """

    __tablename__ = "bracket_formats"

    key: Mapped[str] = mapped_column(primary_key = True)
    label: Mapped[str] = mapped_column()
    description: Mapped[str] = mapped_column()
    has_final: Mapped[bool] = mapped_column()
    min_entrants: Mapped[int] = mapped_column(SmallInteger, default = 2, server_default = "2")
    enabled: Mapped[bool] = mapped_column(default = True, server_default = text("true"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, default = 0, server_default = "0")

class BracketEvents(Base):
    """
    SQL Alchemy model describing the bracket_events table: an event a host or venue profile runs
    on uSync (a league, LAN or tournament) with its sign-ups and bracket.

    Money (see GLOSSARY.md): entry_fee_cents is what one player pays (null = free); prize_funding
    is 'guaranteed' (the host pays guarantee_cents when sign-ups open) or 'entry_funded' (the prize
    pool is the event pool after the platform fee and host_cut_bps); payout_structure splits the
    prize pool by placement in basis points. Postgres checks the combination
    (ck_bracket_events_money) and the payout structure (payout_structure_valid). An event that
    ever held money is cancelled (cancelled_at), never deleted; host_profile_id is nulled if its
    host profile is deleted afterwards.

    The game is a lookup (games / game_titles / game_modes). Its bracket, seeds and matches are
    their own tables (brackets, bracket_seeds, bracket_matches).
    """

    __tablename__ = "bracket_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    public_id: Mapped[str] = mapped_column()
    host_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True, index = True)
    name: Mapped[str] = mapped_column()
    event_type: Mapped[str] = mapped_column()
    # A title only exists for some games. The mode becomes required once modes are seeded.
    game_id: Mapped[int] = mapped_column(SmallInteger, ForeignKey("games.id", ondelete = "RESTRICT"), index = True)
    game_title_id: Mapped[int | None] = mapped_column(ForeignKey("game_titles.id", ondelete = "RESTRICT"), nullable = True)
    game_mode_id: Mapped[int | None] = mapped_column(ForeignKey("game_modes.id", ondelete = "RESTRICT"), nullable = True)
    # Required (by the API) once the game has rulesets; null for games without any.
    ruleset_id: Mapped[int | None] = mapped_column(ForeignKey("rulesets.id", ondelete = "RESTRICT"), nullable = True)
    format: Mapped[str] = mapped_column(ForeignKey("bracket_formats.key", ondelete = "RESTRICT"))
    team_size: Mapped[int] = mapped_column()
    best_of: Mapped[int] = mapped_column()
    final_best_of: Mapped[int | None] = mapped_column(nullable = True)
    max_entrants: Mapped[int | None] = mapped_column(nullable = True)
    min_entrants: Mapped[int | None] = mapped_column(nullable = True)
    platform: Mapped[str] = mapped_column(default = "pc_allowed", server_default = text("'pc_allowed'"))
    restrictions: Mapped[list] = mapped_column(JSONB, default = list, server_default = text("'[]'::jsonb"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone = True))
    location_type: Mapped[str] = mapped_column(default = "online", server_default = text("'online'"))
    venue: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    entry_fee: Mapped[str] = mapped_column(default = "Free", server_default = text("'Free'"))
    prize: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    description: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    # Free-text rules from before rulesets. No longer written or shown; kept until a cleanup drops it.
    rules: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    registration_open: Mapped[bool] = mapped_column(default = True, server_default = text("true"))
    no_show_minutes: Mapped[int | None] = mapped_column(nullable = True)
    payments: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    signup_form: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    delays: Mapped[list] = mapped_column(JSONB, default = list, server_default = text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    entry_fee_cents: Mapped[int | None] = mapped_column(nullable = True)
    prize_funding: Mapped[str | None] = mapped_column(nullable = True)
    guarantee_cents: Mapped[int | None] = mapped_column(nullable = True)
    host_cut_bps: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    # none_as_null: Python None is SQL NULL here, not the JSON value null (a free event has none).
    payout_structure: Mapped[list | None] = mapped_column(JSONB(none_as_null = True), nullable = True)
    platform_fee_bps: Mapped[int] = mapped_column(SmallInteger, default = 500, server_default = text("500"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    cancel_reason: Mapped[str | None] = mapped_column(nullable = True)
    # Payout: due 24 hours after the standings last changed; paid_out_at once it's done (only
    # events with money pay out). A paid-out event is read-only, like a cancelled one.
    payout_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    paid_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        UniqueConstraint("public_id", name = "uq_bracket_events_public_id"),
        CheckConstraint("event_type IN ('league', 'lan', 'tournament')", name = "ck_bracket_events_type"),
        CheckConstraint("platform IN ('pc_allowed', 'console_only')", name = "ck_bracket_events_platform"),
        CheckConstraint("team_size BETWEEN 1 AND 10", name = "ck_bracket_events_team_size"),
        CheckConstraint("entry_fee_cents IS NULL OR entry_fee_cents BETWEEN 100 AND 50000", name = "ck_bracket_events_entry_fee"),
        CheckConstraint("guarantee_cents IS NULL OR (guarantee_cents > 0 AND guarantee_cents % 100 = 0)", name = "ck_bracket_events_guarantee"),
        CheckConstraint("host_cut_bps IS NULL OR host_cut_bps BETWEEN 0 AND 5000", name = "ck_bracket_events_host_cut"),
        CheckConstraint("platform_fee_bps BETWEEN 0 AND 10000", name = "ck_bracket_events_platform_fee"),
        CheckConstraint("payout_structure_valid(payout_structure)", name = "ck_bracket_events_payout_structure"),
        CheckConstraint(
            "(prize_funding IS NULL AND entry_fee_cents IS NULL AND guarantee_cents IS NULL AND host_cut_bps IS NULL AND payout_structure IS NULL)"
            " OR (prize_funding = 'entry_funded' AND entry_fee_cents IS NOT NULL AND guarantee_cents IS NULL AND host_cut_bps IS NOT NULL AND payout_structure IS NOT NULL)"
            " OR (prize_funding = 'guaranteed' AND guarantee_cents IS NOT NULL AND host_cut_bps IS NULL AND payout_structure IS NOT NULL)",
            name = "ck_bracket_events_money",
        ),
        CheckConstraint("entry_fee_cents IS NULL OR payments->'enabled' IS DISTINCT FROM 'true'::jsonb", name = "ck_bracket_events_no_passes_with_fee"),
        CheckConstraint(
            "(cancelled_at IS NULL) = (cancel_reason IS NULL) AND (cancel_reason IS NULL OR cancel_reason IN ('host', 'min_entrants', 'admin'))",
            name = "ck_bracket_events_cancelled",
        ),
        CheckConstraint("paid_out_at IS NULL OR prize_funding IS NOT NULL", name = "ck_bracket_events_paid_out"),
        Index("ix_bracket_events_payout_due", "payout_due_at", postgresql_where = text("payout_due_at IS NOT NULL AND paid_out_at IS NULL")),
    )

class BracketEntries(Base):
    """
    SQL Alchemy model describing the bracket_entries table: one entrant in an event. Every entry is
    entered by a team's owner or captain, who picks the event's team size in players
    (bracket_entry_players). In a solo event (team size 1) a team can enter several of its players,
    one entry each, named after the player; in a team event a team enters once, named after it.
    "One entry per team" is checked by the API under the event's row lock, because a unique index
    can't see the event's team size. "One entry per player per event" is a unique index on
    bracket_entry_players.

    status is 'signed_up', or 'needs_players' once a picked player leaves the team before the
    bracket is published. Both hold a spot (every entry counts towards max_entrants; there's no
    waitlist). Entries still short when the bracket is published are dropped.

    team_id is never null for new entries. It's nulled (not cascaded) when the team is deleted
    after the event started, so the entry - and any bracket it's in - survives. profile_id is the
    profile that entered it, nulled the same way.
    """

    __tablename__ = "bracket_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), index = True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True, index = True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete = "SET NULL"), nullable = True, index = True)
    name: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default = "signed_up", server_default = text("'signed_up'"))
    email: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    answers: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    pass_name: Mapped[str | None] = mapped_column(nullable = True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    # How its entry fees are paid: 'team' (the captain, one payment) or 'each' (every picked
    # player, one payment each). Null in free events.
    pay_mode: Mapped[str | None] = mapped_column(nullable = True)

    __table_args__ = (
        CheckConstraint("status IN ('signed_up', 'needs_players')", name = "ck_bracket_entries_status"),
        CheckConstraint("pay_mode IS NULL OR pay_mode IN ('team', 'each')", name = "ck_bracket_entries_pay_mode"),
    )

class EventStandings(Base):
    """
    SQL Alchemy model describing the event_standings table: every entrant's final placement once
    the bracket is complete (entrants can share one). Rewritten whenever a correction changes the
    standings, removed if the bracket reopens. prize_cents is what the entry won, filled in when
    the event pays out (null before that, and for free events).
    """

    __tablename__ = "event_standings"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), primary_key = True)
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), primary_key = True)
    placement: Mapped[int] = mapped_column(SmallInteger)
    prize_cents: Mapped[int | None] = mapped_column(BigInteger, nullable = True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("placement >= 1", name = "ck_event_standings_placement"),
        CheckConstraint("prize_cents IS NULL OR prize_cents >= 0", name = "ck_event_standings_prize"),
        Index("ix_event_standings_event_placement", "event_id", "placement"),
        Index("ix_event_standings_entry", "entry_id"),
    )

class BracketEntryPlayers(Base):
    """
    SQL Alchemy model describing the bracket_entry_players table: who plays for an entry in one
    event. For a team, the players its captain picked (team members when picked; checked by the
    API, not a foreign key, so a player leaving the team later doesn't rewrite the event's
    history). For a solo entry, the player themself.

    One row per player per event, whichever entry: a player can't play for two entrants in the
    same event. The captain can change the picks until the bracket is published; after that the
    event's roster is locked (the team itself never freezes).
    """

    __tablename__ = "bracket_entry_players"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"))
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), index = True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id", ondelete = "SET NULL"), nullable = True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True, index = True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("event_id", "profile_id", name = "uq_bracket_entry_players_event_profile"),
    )

class Brackets(Base):
    """
    SQL Alchemy model describing the brackets table: an event's bracket. One per event; reseeding
    a draft replaces it (its seeds and matches go with it).

    status: 'draft' (only the host and admins see it) -> 'published' (live: matches are played and
    the entrants' event rosters are locked) -> 'complete' (every match decided).
    """

    __tablename__ = "brackets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), unique = True)
    format: Mapped[str] = mapped_column(ForeignKey("bracket_formats.key", ondelete = "RESTRICT"))
    status: Mapped[str] = mapped_column(default = "draft", server_default = text("'draft'"))
    created_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published', 'complete')", name = "ck_brackets_status"),
    )

class BracketSeeds(Base):
    """
    SQL Alchemy model describing the bracket_seeds table: each entrant's seed in a bracket (1 = top).
    """

    __tablename__ = "bracket_seeds"

    bracket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brackets.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), primary_key = True)
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), primary_key = True)
    seed: Mapped[int] = mapped_column(SmallInteger)

    __table_args__ = (
        UniqueConstraint("bracket_id", "seed", name = "uq_bracket_seeds_bracket_seed"),
        CheckConstraint("seed >= 1", name = "ck_bracket_seeds_seed"),
    )

class BracketMatches(Base):
    """
    SQL Alchemy model describing the bracket_matches table: one row per match, created when the
    bracket is published (elimination formats: every match up front, later ones waiting on
    earlier results; round robin: every round; swiss: each round once the one before is played).

    - code: the match's stable, readable id in its bracket ("r0m0", "l1m2", "gf1", "sw2m3"),
      used in URLs and notifications.
    - stage: 'main' (single elim, round robin, swiss), 'winners' / 'losers' / 'grand' (double elim).
    - entry_a_id / entry_b_id: null until an earlier match decides who plays (status 'pending'),
      or for a bye (status 'bye').
    - score_a / score_b: games won by side A and side B, two integers rather than a "2:1" string
      so the database can check them and the API can validate best of without parsing.
    - next_match_id / next_slot: where the winner goes; loser_next_match_id / loser_next_slot:
      where the loser goes (double elim's losers bracket). Both are null when there's nowhere to
      go - a final, or any match in single elim (loser) / round robin / swiss (both).
    - status: 'pending' (waiting on earlier matches), 'ready' (both sides known, play it),
      'complete', 'bye' (one side advances unplayed), 'empty' (bye vs bye in a sparse bracket).
      Reports, disputes and no-shows are their own tables; locked = the result above can't be
      reported while a post-advancement dispute on the match that fed it is open.
    - ready_at: when both sides became known - the match's start, which the no-show timer
      counts from. completed_at: when it was decided - the 5 minute dispute window counts from it.
    """

    __tablename__ = "bracket_matches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    bracket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brackets.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), index = True)
    code: Mapped[str] = mapped_column()
    stage: Mapped[str] = mapped_column(default = "main", server_default = text("'main'"))
    round: Mapped[int] = mapped_column(SmallInteger)
    slot: Mapped[int] = mapped_column(SmallInteger)
    best_of: Mapped[int] = mapped_column(SmallInteger)
    entry_a_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True, index = True)
    entry_b_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True, index = True)
    status: Mapped[str] = mapped_column(default = "pending", server_default = text("'pending'"))
    score_a: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    score_b: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    winner_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True)
    forfeit: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    locked: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    # The current result came from a host / admin ruling. Rulings are final: no post-advancement dispute.
    ruled: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    next_match_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_matches.id", ondelete = "SET NULL"), nullable = True)
    next_slot: Mapped[str | None] = mapped_column(nullable = True)
    loser_next_match_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_matches.id", ondelete = "SET NULL"), nullable = True)
    loser_next_slot: Mapped[str | None] = mapped_column(nullable = True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        UniqueConstraint("bracket_id", "code", name = "uq_bracket_matches_bracket_code"),
        CheckConstraint("stage IN ('main', 'winners', 'losers', 'grand')", name = "ck_bracket_matches_stage"),
        CheckConstraint("status IN ('pending', 'ready', 'complete', 'bye', 'empty')", name = "ck_bracket_matches_status"),
        # A link to the next match always says which side of it ('a' or 'b') this match's winner /
        # loser fills, and a side is never given without a match.
        CheckConstraint("(next_match_id IS NULL) = (next_slot IS NULL) AND (next_slot IS NULL OR next_slot IN ('a', 'b'))", name = "ck_bracket_matches_next_slot"),
        CheckConstraint("(loser_next_match_id IS NULL) = (loser_next_slot IS NULL) AND (loser_next_slot IS NULL OR loser_next_slot IN ('a', 'b'))", name = "ck_bracket_matches_loser_next_slot"),
        CheckConstraint("score_a >= 0 AND score_b >= 0", name = "ck_bracket_matches_scores"),
        CheckConstraint("best_of BETWEEN 1 AND 9 AND best_of % 2 = 1", name = "ck_bracket_matches_best_of"),
    )

class MatchReports(Base):
    """
    SQL Alchemy model describing the match_reports table: a side's report of a match's score.
    One per side per match; clearing a match deletes its reports so both sides can report again.
    Two reports with the same winner decide the match; different winners open a dispute.
    """

    __tablename__ = "match_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_matches.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"))
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"))
    reported_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True)
    score_a: Mapped[int] = mapped_column(SmallInteger)
    score_b: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("match_id", "entry_id", name = "uq_match_reports_match_entry"),
        CheckConstraint("score_a >= 0 AND score_b >= 0 AND score_a <> score_b", name = "ck_match_reports_scores"),
    )

class MatchDecisions(Base):
    """
    SQL Alchemy model describing the match_decisions table: every host or admin ruling on a match,
    for the audit trail and so a ruling can be reverted. prev_* is the match's result just before
    the ruling and new_* just after - e.g. awarding Bravo a 2-1 win over a disputed match records
    prev (ready, no scores, no winner) and new (complete, 2-1, Bravo). Reverting puts prev_* back.

    action: 'award' (set the result), 'forfeit' (award after a no-show), 'clear' (reset the match
    so it's played or reported again), 'uphold' (a post-advancement dispute was wrong: unlock),
    'revert' (undo an earlier decision; reverts_decision_id points at it).
    """

    __tablename__ = "match_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_matches.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), index = True)
    decided_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True)
    action: Mapped[str] = mapped_column()
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("event_issues.id", ondelete = "SET NULL"), nullable = True)
    reverts_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("match_decisions.id", ondelete = "SET NULL"), nullable = True)
    prev_status: Mapped[str] = mapped_column()
    prev_score_a: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    prev_score_b: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    prev_winner_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True)
    new_status: Mapped[str] = mapped_column()
    new_score_a: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    new_score_b: Mapped[int | None] = mapped_column(SmallInteger, nullable = True)
    new_winner_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("action IN ('award', 'forfeit', 'clear', 'uphold', 'revert')", name = "ck_match_decisions_action"),
    )

class EventIssues(Base):
    """
    SQL Alchemy model describing the event_issues table: the per-event admin queue. Anything that
    needs the host or an admin to step in - a disputed score, a one-sided report left 5 minutes,
    a no-show, a post-advancement dispute, a stalled match nobody has reported - is a row here, and the host and admins are notified.
    Only they can see an event's queue. Oldest first.

    status: 'open' (in the queue) -> 'claimed' (someone pressed "go to issue"; shown as claimed
    and back to 'open' if not closed within 10 minutes) -> 'dismissed' or 'resolved'. Closing
    always beats claiming: a claim only applies to an open issue, a dismissal to an open or
    claimed one, each as one atomic UPDATE, so two admins can't both win. An issue whose match
    has since been decided is resolved automatically (closed_by is null for the system).

    raised_by_entry_id: the side that raised it (null for ones the system raises). A side can
    raise one post-advancement dispute per match (partial unique index below).
    """

    __tablename__ = "event_issues"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"))
    match_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_matches.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), nullable = True, index = True)
    type: Mapped[str] = mapped_column()
    raised_by_entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True)
    data: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(default = "open", server_default = text("'open'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    claimed_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    closed_by_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        CheckConstraint("type IN ('dispute', 'one_sided_report', 'no_show', 'post_advancement_dispute', 'stalled_match', 'admin_requested')", name = "ck_event_issues_type"),
        CheckConstraint("status IN ('open', 'claimed', 'dismissed', 'resolved')", name = "ck_event_issues_status"),
        Index("ix_event_issues_queue", "event_id", "status", "created_at"),
        Index("uq_event_issues_one_dispute_per_side", "match_id", "raised_by_entry_id", unique = True,
              postgresql_where = text("type = 'post_advancement_dispute'")),
        Index("uq_event_issues_open_per_match_type", "match_id", "type", unique = True,
              postgresql_where = text("status IN ('open', 'claimed') AND type <> 'post_advancement_dispute'")),
    )

class EventIssueActions(Base):
    """
    SQL Alchemy model describing the event_issue_actions table: who did what to a queue item and
    when ('claim', 'release' - the 10 minutes ran out, 'dismiss', 'resolve', 'auto_resolve').
    profile_id is null for the system.
    """

    __tablename__ = "event_issue_actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("event_issues.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), index = True)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("profiles.id", ondelete = "SET NULL"), nullable = True)
    action: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("action IN ('claim', 'release', 'dismiss', 'resolve', 'auto_resolve')", name = "ck_event_issue_actions_action"),
    )

class BracketEventAdmins(Base):
    """
    SQL Alchemy model describing the bracket_event_admins table: profiles the host invited to help
    run an event. Once accepted, an admin can do everything the host can except delete the event
    or change its admins.
    """

    __tablename__ = "bracket_event_admins"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE", deferrable = True, initially = "DEFERRED"), index = True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), index = True)
    status: Mapped[str] = mapped_column(default = "pending", server_default = text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("event_id", "profile_id", name = "uq_bracket_event_admins"),
        CheckConstraint("status IN ('pending', 'accepted')", name = "ck_bracket_event_admins_status"),
    )

class Notifications(Base):
    """
    SQL Alchemy model describing the notifications table: things that need an account's attention
    (team invites are their own table; these are everything else - delays, matches, disputes, ...).
    `data` holds what the frontend needs to render and link it (event id and name, team, who).
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"))
    type: Mapped[str] = mapped_column()
    data: Mapped[dict] = mapped_column(JSONB, default = dict, server_default = text("'{}'::jsonb"))
    read: Mapped[bool] = mapped_column(default = False, server_default = text("false"))
    # When it was read (or dismissed). Read notifications are deleted 90 days after this.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        Index("ix_notifications_account_created", "account_id", "created_at"),
    )
