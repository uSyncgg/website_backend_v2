"""Bracket hosting: host-run events with sign-ups, seeding, live brackets and player reporting

Revision ID: b8d4f2a6c1e3
Revises: a7c3e1b9d2f4
Create Date: 2026-11-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b8d4f2a6c1e3'
down_revision: Union[str, Sequence[str], None] = 'a7c3e1b9d2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BRACKET_EVENT_TYPE = postgresql.ENUM("league", "lan", "tournament", name = "bracket_event_type", create_type = False)
BRACKET_FORMAT = postgresql.ENUM("single_elim", "double_elim", "round_robin", name = "bracket_format", create_type = False)
BRACKET_LOCATION = postgresql.ENUM("online", "in_person", name = "bracket_location", create_type = False)
BRACKET_DECISION = postgresql.ENUM("host", "players", "forfeit", "cleared", name = "bracket_decision", create_type = False)

def _now():
    return sa.text("now()")

def upgrade() -> None:
    """Upgrade schema."""
    for enum in (BRACKET_EVENT_TYPE, BRACKET_FORMAT, BRACKET_LOCATION, BRACKET_DECISION):
        enum.create(op.get_bind(), checkfirst = True)

    op.create_table(
        "bracket_events",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("events.id", ondelete = "CASCADE"), primary_key = True),
        sa.Column("account_id", sa.Uuid(), nullable = False),
        sa.Column("host_profile_id", sa.Uuid(), nullable = False),
        sa.Column("name", sa.String(80), nullable = False),
        sa.Column("event_type", BRACKET_EVENT_TYPE, nullable = False),
        sa.Column("game", sa.String(), nullable = False),
        sa.Column("format", BRACKET_FORMAT, nullable = False),
        sa.Column("team_size", sa.SmallInteger(), nullable = False),
        sa.Column("best_of", sa.SmallInteger(), nullable = False),
        sa.Column("max_entrants", sa.Integer(), nullable = True),
        sa.Column("starts_at", sa.DateTime(timezone = True), nullable = False),
        sa.Column("location_type", BRACKET_LOCATION, server_default = sa.text("'online'"), nullable = False),
        sa.Column("venue", sa.String(), nullable = True),
        sa.Column("description", sa.String(1000), server_default = sa.text("''"), nullable = False),
        sa.Column("rules", sa.String(3000), server_default = sa.text("''"), nullable = False),
        sa.Column("prize", sa.String(), nullable = True),
        sa.Column("check_in_required", sa.Boolean(), server_default = sa.text("true"), nullable = False),
        sa.Column("no_show_minutes", sa.Integer(), nullable = True),
        sa.Column("registration_open", sa.Boolean(), server_default = sa.text("true"), nullable = False),
        sa.Column("signup_player_fields", postgresql.ARRAY(sa.String()), server_default = sa.text("'{}'"), nullable = False),
        sa.Column("signup_questions", postgresql.JSONB(), server_default = sa.text("'[]'::jsonb"), nullable = False),
        sa.Column("payments_enabled", sa.Boolean(), server_default = sa.text("false"), nullable = False),
        sa.Column("notify_email", sa.String(), nullable = True),
        sa.Column("created_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
        sa.Column("updated_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
        sa.ForeignKeyConstraint(
            ["account_id", "host_profile_id"], ["profiles.account_id", "profiles.id"],
            name = "bracket_events_host_profile_fkey", ondelete = "CASCADE"
        ),
        sa.CheckConstraint("team_size BETWEEN 1 AND 5", name = "ck_bracket_events_team_size"),
        sa.CheckConstraint("best_of IN (1, 3, 5, 7)", name = "ck_bracket_events_best_of"),
        sa.CheckConstraint("max_entrants IS NULL OR max_entrants >= 2", name = "ck_bracket_events_max_entrants"),
        sa.CheckConstraint("no_show_minutes IS NULL OR no_show_minutes >= 0", name = "ck_bracket_events_no_show_minutes"),
        sa.CheckConstraint("location_type = 'online' OR venue IS NOT NULL", name = "ck_bracket_events_venue"),
        sa.CheckConstraint("event_type <> 'lan' OR location_type = 'in_person'", name = "ck_bracket_events_lan_in_person"),
        sa.CheckConstraint("NOT payments_enabled OR notify_email IS NOT NULL", name = "ck_bracket_events_notify_email"),
    )
    op.create_index("ix_bracket_events_host_profile_id", "bracket_events", ["host_profile_id"])
    op.create_index("ix_bracket_events_account_id", "bracket_events", ["account_id"])

    op.create_table(
        "bracket_event_delays",
        sa.Column("id", sa.Uuid(), primary_key = True),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("bracket_events.event_id", ondelete = "CASCADE"), nullable = False),
        sa.Column("from_starts_at", sa.DateTime(timezone = True), nullable = False),
        sa.Column("to_starts_at", sa.DateTime(timezone = True), nullable = False),
        sa.Column("reason", sa.String(), server_default = sa.text("''"), nullable = False),
        sa.Column("created_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
    )
    op.create_index("ix_bracket_event_delays_event_id", "bracket_event_delays", ["event_id"])

    op.create_table(
        "bracket_entrants",
        sa.Column("id", sa.Uuid(), primary_key = True),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("bracket_events.event_id", ondelete = "CASCADE"), nullable = False),
        sa.Column("name", sa.String(40), nullable = False),
        sa.Column("name_normalized", sa.String(), nullable = False),
        sa.Column("captain", sa.String(40), nullable = False),
        sa.Column("teammates", postgresql.ARRAY(sa.String()), server_default = sa.text("'{}'"), nullable = False),
        sa.Column("email", sa.String(), nullable = True),
        sa.Column("answers", postgresql.JSONB(), server_default = sa.text("'{}'::jsonb"), nullable = False),
        sa.Column("rating", sa.Integer(), server_default = sa.text("1200"), nullable = False),
        sa.Column("checked_in", sa.Boolean(), server_default = sa.text("false"), nullable = False),
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete = "SET NULL"), nullable = True),
        sa.Column("registration_id", sa.Uuid(), sa.ForeignKey("event_registrations.id", ondelete = "SET NULL"), nullable = True, unique = True),
        sa.Column("registered_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
    )
    op.create_index("ix_bracket_entrants_event_id", "bracket_entrants", ["event_id"])
    op.create_index("ix_bracket_entrants_account_id", "bracket_entrants", ["account_id"])
    op.create_index("uq_bracket_entrants_event_name", "bracket_entrants", ["event_id", "name_normalized"], unique = True)
    op.create_index(
        "uq_bracket_entrants_event_account", "bracket_entrants", ["event_id", "account_id"],
        unique = True, postgresql_where = sa.text("account_id IS NOT NULL")
    )

    op.create_table(
        "brackets",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("bracket_events.event_id", ondelete = "CASCADE"), primary_key = True),
        sa.Column("format", BRACKET_FORMAT, nullable = False),
        sa.Column("seeds", postgresql.ARRAY(sa.Uuid()), nullable = False),
        sa.Column("published", sa.Boolean(), server_default = sa.text("false"), nullable = False),
        sa.Column("published_at", sa.DateTime(timezone = True), nullable = True),
        sa.Column("generated_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
    )

    op.create_table(
        "bracket_match_results",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("brackets.event_id", ondelete = "CASCADE"), primary_key = True),
        sa.Column("match_id", sa.String(16), primary_key = True),
        sa.Column("entrant_a", sa.Uuid(), nullable = False),
        sa.Column("entrant_b", sa.Uuid(), nullable = False),
        sa.Column("score_a", sa.SmallInteger(), nullable = False),
        sa.Column("score_b", sa.SmallInteger(), nullable = False),
        sa.Column("forfeit", sa.Boolean(), server_default = sa.text("false"), nullable = False),
        sa.Column("decided_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
        sa.CheckConstraint("score_a <> score_b", name = "ck_bracket_match_results_winner"),
        sa.CheckConstraint("score_a >= 0 AND score_b >= 0", name = "ck_bracket_match_results_scores"),
    )

    op.create_table(
        "bracket_match_reports",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("brackets.event_id", ondelete = "CASCADE"), primary_key = True),
        sa.Column("match_id", sa.String(16), primary_key = True),
        sa.Column("reporter_id", sa.Uuid(), sa.ForeignKey("bracket_entrants.id", ondelete = "CASCADE"), primary_key = True),
        sa.Column("entrant_a", sa.Uuid(), nullable = False),
        sa.Column("entrant_b", sa.Uuid(), nullable = False),
        sa.Column("score_a", sa.SmallInteger(), nullable = False),
        sa.Column("score_b", sa.SmallInteger(), nullable = False),
        sa.Column("reported_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
        sa.CheckConstraint("score_a <> score_b", name = "ck_bracket_match_reports_winner"),
        sa.CheckConstraint("score_a >= 0 AND score_b >= 0", name = "ck_bracket_match_reports_scores"),
    )

    op.create_table(
        "bracket_no_shows",
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("brackets.event_id", ondelete = "CASCADE"), primary_key = True),
        sa.Column("match_id", sa.String(16), primary_key = True),
        sa.Column("entrant_a", sa.Uuid(), nullable = False),
        sa.Column("entrant_b", sa.Uuid(), nullable = False),
        sa.Column("reported_by", sa.Uuid(), sa.ForeignKey("bracket_entrants.id", ondelete = "CASCADE"), nullable = False),
        sa.Column("against", sa.Uuid(), sa.ForeignKey("bracket_entrants.id", ondelete = "CASCADE"), nullable = False),
        sa.Column("reported_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
    )

    op.create_table(
        "bracket_decision_log",
        sa.Column("id", sa.Uuid(), primary_key = True),
        sa.Column("event_id", sa.Uuid(), sa.ForeignKey("brackets.event_id", ondelete = "CASCADE"), nullable = False),
        sa.Column("match_id", sa.String(16), nullable = False),
        sa.Column("entrant_a", sa.Uuid(), nullable = False),
        sa.Column("entrant_b", sa.Uuid(), nullable = False),
        sa.Column("score_a", sa.SmallInteger(), nullable = True),
        sa.Column("score_b", sa.SmallInteger(), nullable = True),
        sa.Column("source", BRACKET_DECISION, nullable = False),
        sa.Column("prev_result", postgresql.JSONB(), nullable = True),
        sa.Column("prev_reports", postgresql.JSONB(), nullable = True),
        sa.Column("prev_no_show", postgresql.JSONB(), nullable = True),
        sa.Column("actor_account_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete = "SET NULL"), nullable = True),
        sa.Column("created_at", sa.DateTime(timezone = True), server_default = _now(), nullable = False),
        sa.Column("reverted_at", sa.DateTime(timezone = True), nullable = True),
    )
    op.create_index("ix_bracket_decision_log_event_id", "bracket_decision_log", ["event_id"])


def downgrade() -> None:
    """Downgrade schema."""
    # Pass tiers and registrations made for bracket events belong to events rows of type
    # 'bracket_events'; they're removed with them.
    op.drop_table("bracket_decision_log")
    op.drop_table("bracket_no_shows")
    op.drop_table("bracket_match_reports")
    op.drop_table("bracket_match_results")
    op.drop_table("brackets")
    op.drop_table("bracket_entrants")
    op.drop_table("bracket_event_delays")
    op.drop_table("bracket_events")
    op.execute(
        "DELETE FROM event_registrations WHERE event_id IN (SELECT id FROM events WHERE event_type = 'bracket_events')"
    )
    op.execute(
        "DELETE FROM event_pass_tiers WHERE event_id IN (SELECT id FROM events WHERE event_type = 'bracket_events')"
    )
    op.execute("DELETE FROM events WHERE event_type = 'bracket_events'")
    for enum in (BRACKET_DECISION, BRACKET_LOCATION, BRACKET_FORMAT, BRACKET_EVENT_TYPE):
        enum.drop(op.get_bind(), checkfirst = True)
