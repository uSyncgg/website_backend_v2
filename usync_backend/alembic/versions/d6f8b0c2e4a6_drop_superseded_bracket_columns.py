"""Drop the columns the bracket tables replaced, and mark matches decided by a ruling.

Revision ID: d6f8b0c2e4a6
Revises: c5e7a9b1d3f5
Create Date: 2026-11-15 12:00:00.000000

Written by hand. The services now read only the tables added in a3c5e7f9b1d2 / b4d6f8a0c2e4, so:

- bracket_events.bracket (the old JSONB bracket), .game and .game_title (free text; game_id and
  game_title_id replace them) are dropped, and game_id becomes required.
- bracket_entries.roster (copied usernames; bracket_entry_players replaces it) and .rating
  (every entrant was 1200; seeding by rating was removed) are dropped.
- teams.game is dropped (team_games replaces it).
- bracket_matches.ruled is added: true when the current result came from a host or admin ruling.
  Rulings are final, so a ruled match can't be disputed after advancement.
- The foreign keys that tie bracket rows to their parent (event -> entries / bracket -> matches ->
  reports, decisions, queue items) become DEFERRABLE INITIALLY DEFERRED. Deleting an account or
  event cascades down several paths in one statement; a path that sets a column to null (say a
  decision's decided_by) can touch a row another path is deleting, and an immediate check would
  fail on the half-finished cascade. Deferred, the check runs at commit, once the cascade is done.
  Nothing else changes: the cascades still delete the same rows.

The only rows affected are a test event, whose JSONB bracket is discarded.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd6f8b0c2e4a6'
down_revision: Union[str, Sequence[str], None] = 'c5e7a9b1d3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, column, referenced table, constraint name, on delete) - every parent link in the
# bracket tables. All existing ones are the Postgres default names.
PARENT_LINKS = (
    ('bracket_entries', 'event_id', 'bracket_events', 'bracket_entries_event_id_fkey', 'CASCADE'),
    ('bracket_event_admins', 'event_id', 'bracket_events', 'bracket_event_admins_event_id_fkey', 'CASCADE'),
    ('bracket_entry_players', 'event_id', 'bracket_events', 'bracket_entry_players_event_id_fkey', 'CASCADE'),
    ('bracket_entry_players', 'entry_id', 'bracket_entries', 'bracket_entry_players_entry_id_fkey', 'CASCADE'),
    ('brackets', 'event_id', 'bracket_events', 'brackets_event_id_fkey', 'CASCADE'),
    ('bracket_seeds', 'bracket_id', 'brackets', 'bracket_seeds_bracket_id_fkey', 'CASCADE'),
    ('bracket_seeds', 'entry_id', 'bracket_entries', 'bracket_seeds_entry_id_fkey', 'CASCADE'),
    ('bracket_matches', 'bracket_id', 'brackets', 'bracket_matches_bracket_id_fkey', 'CASCADE'),
    ('match_reports', 'match_id', 'bracket_matches', 'match_reports_match_id_fkey', 'CASCADE'),
    ('match_reports', 'entry_id', 'bracket_entries', 'match_reports_entry_id_fkey', 'CASCADE'),
    ('match_decisions', 'match_id', 'bracket_matches', 'match_decisions_match_id_fkey', 'CASCADE'),
    ('event_issues', 'event_id', 'bracket_events', 'event_issues_event_id_fkey', 'CASCADE'),
    ('event_issues', 'match_id', 'bracket_matches', 'event_issues_match_id_fkey', 'CASCADE'),
    ('event_issue_actions', 'issue_id', 'event_issues', 'event_issue_actions_issue_id_fkey', 'CASCADE'),
)


def _relink(deferred: bool) -> None:
    for table, column, target, name, on_delete in PARENT_LINKS:
        op.drop_constraint(name, table, type_='foreignkey')
        op.create_foreign_key(name, table, target, [column], ['id'], ondelete=on_delete,
                              deferrable=deferred or None, initially='DEFERRED' if deferred else None)


def upgrade() -> None:
    """Upgrade schema."""
    _relink(deferred=True)
    op.add_column('bracket_matches', sa.Column('ruled', sa.Boolean(), server_default=sa.text('false'), nullable=False))

    op.drop_column('bracket_events', 'bracket')
    op.drop_column('bracket_events', 'game')
    op.drop_column('bracket_events', 'game_title')
    op.alter_column('bracket_events', 'game_id', existing_type=sa.SmallInteger(), nullable=False)

    op.drop_column('bracket_entries', 'roster')
    op.drop_column('bracket_entries', 'rating')

    op.drop_column('teams', 'game')


def downgrade() -> None:
    """Downgrade schema."""
    # The old values can't come back from nothing: names are rebuilt from the lookup tables, and
    # brackets / rosters / ratings start empty.
    op.add_column('teams', sa.Column('game', sa.String(), nullable=True))
    op.execute("UPDATE teams t SET game = g.name FROM team_games tg JOIN games g ON g.id = tg.game_id WHERE tg.team_id = t.id")

    op.add_column('bracket_entries', sa.Column('rating', sa.Integer(), server_default=sa.text('1200'), nullable=False))
    op.add_column('bracket_entries', sa.Column('roster', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))

    op.alter_column('bracket_events', 'game_id', existing_type=sa.SmallInteger(), nullable=True)
    op.add_column('bracket_events', sa.Column('game_title', sa.String(), server_default=sa.text("''"), nullable=False))
    op.add_column('bracket_events', sa.Column('game', sa.String(), server_default=sa.text("''"), nullable=False))
    op.execute("UPDATE bracket_events e SET game = g.name FROM games g WHERE g.id = e.game_id")
    op.execute("UPDATE bracket_events e SET game_title = t.name FROM game_titles t WHERE t.id = e.game_title_id")
    op.add_column('bracket_events', sa.Column('bracket', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.drop_column('bracket_matches', 'ruled')
    _relink(deferred=False)
