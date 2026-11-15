"""Brackets as tables (brackets, seeds, matches, reports, decisions), per-event rosters, the
per-event admin queue, and the column changes they need.

Revision ID: b4d6f8a0c2e4
Revises: a3c5e7f9b1d2
Create Date: 2026-11-14 12:30:00.000000

Written by hand. Replaces the bracket JSONB document and the copied roster usernames with
rows. The old columns (bracket_events.bracket, bracket_entries.roster) stay until the services
read the new tables, then a later migration drops them. Nothing is copied from them: there are
no teams, entries or live brackets yet (only a test event).

RLS is turned on for every new table and for bracket_event_admins (missed when it was added).
No policies: only the backend, which bypasses RLS, reads or writes them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b4d6f8a0c2e4'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f9b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = (
    'bracket_entry_players', 'brackets', 'bracket_seeds', 'bracket_matches',
    'match_reports', 'event_issues', 'match_decisions', 'event_issue_actions',
)


def upgrade() -> None:
    """Upgrade schema."""
    # ---- entries survive their registering profile being deleted ---------------
    op.alter_column('bracket_entries', 'profile_id', existing_type=sa.Uuid(), nullable=True)
    op.drop_constraint('bracket_entries_profile_id_fkey', 'bracket_entries', type_='foreignkey')
    op.create_foreign_key('bracket_entries_profile_id_fkey', 'bracket_entries', 'profiles', ['profile_id'], ['id'], ondelete='SET NULL')

    # ---- who plays for each entry in an event -----------------------------------
    op.create_table(
        'bracket_entry_players',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('entry_id', sa.Uuid(), nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=True),
        sa.Column('profile_id', sa.Uuid(), nullable=True),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entry_id'], ['bracket_entries.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'profile_id', name='uq_bracket_entry_players_event_profile'),
    )
    op.create_index('ix_bracket_entry_players_entry_id', 'bracket_entry_players', ['entry_id'])
    op.create_index('ix_bracket_entry_players_profile_id', 'bracket_entry_players', ['profile_id'])

    # ---- the bracket and its seeds -------------------------------------------------
    op.create_table(
        'brackets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('format', sa.String(), nullable=False),
        sa.Column('status', sa.String(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column('created_by_profile_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'published', 'complete')", name='ck_brackets_status'),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['format'], ['bracket_formats.key'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_profile_id'], ['profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', name='uq_brackets_event_id'),
    )
    op.create_table(
        'bracket_seeds',
        sa.Column('bracket_id', sa.Uuid(), nullable=False),
        sa.Column('entry_id', sa.Uuid(), nullable=False),
        sa.Column('seed', sa.SmallInteger(), nullable=False),
        sa.CheckConstraint('seed >= 1', name='ck_bracket_seeds_seed'),
        sa.ForeignKeyConstraint(['bracket_id'], ['brackets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entry_id'], ['bracket_entries.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('bracket_id', 'entry_id'),
        sa.UniqueConstraint('bracket_id', 'seed', name='uq_bracket_seeds_bracket_seed'),
    )

    # ---- matches -------------------------------------------------------------------
    op.create_table(
        'bracket_matches',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('bracket_id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('stage', sa.String(), server_default=sa.text("'main'"), nullable=False),
        sa.Column('round', sa.SmallInteger(), nullable=False),
        sa.Column('slot', sa.SmallInteger(), nullable=False),
        sa.Column('best_of', sa.SmallInteger(), nullable=False),
        sa.Column('entry_a_id', sa.Uuid(), nullable=True),
        sa.Column('entry_b_id', sa.Uuid(), nullable=True),
        sa.Column('status', sa.String(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('score_a', sa.SmallInteger(), nullable=True),
        sa.Column('score_b', sa.SmallInteger(), nullable=True),
        sa.Column('winner_entry_id', sa.Uuid(), nullable=True),
        sa.Column('forfeit', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('locked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('next_match_id', sa.Uuid(), nullable=True),
        sa.Column('next_slot', sa.String(), nullable=True),
        sa.Column('loser_next_match_id', sa.Uuid(), nullable=True),
        sa.Column('loser_next_slot', sa.String(), nullable=True),
        sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("stage IN ('main', 'winners', 'losers', 'grand')", name='ck_bracket_matches_stage'),
        sa.CheckConstraint("status IN ('pending', 'ready', 'complete', 'bye', 'empty')", name='ck_bracket_matches_status'),
        sa.CheckConstraint("(next_match_id IS NULL) = (next_slot IS NULL) AND (next_slot IS NULL OR next_slot IN ('a', 'b'))", name='ck_bracket_matches_next_slot'),
        sa.CheckConstraint("(loser_next_match_id IS NULL) = (loser_next_slot IS NULL) AND (loser_next_slot IS NULL OR loser_next_slot IN ('a', 'b'))", name='ck_bracket_matches_loser_next_slot'),
        sa.CheckConstraint('score_a >= 0 AND score_b >= 0', name='ck_bracket_matches_scores'),
        sa.CheckConstraint('best_of BETWEEN 1 AND 9 AND best_of % 2 = 1', name='ck_bracket_matches_best_of'),
        sa.ForeignKeyConstraint(['bracket_id'], ['brackets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entry_a_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['entry_b_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['winner_entry_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['next_match_id'], ['bracket_matches.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['loser_next_match_id'], ['bracket_matches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bracket_id', 'code', name='uq_bracket_matches_bracket_code'),
    )
    op.create_index('ix_bracket_matches_bracket_id', 'bracket_matches', ['bracket_id'])
    op.create_index('ix_bracket_matches_entry_a_id', 'bracket_matches', ['entry_a_id'])
    op.create_index('ix_bracket_matches_entry_b_id', 'bracket_matches', ['entry_b_id'])

    op.create_table(
        'match_reports',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('match_id', sa.Uuid(), nullable=False),
        sa.Column('entry_id', sa.Uuid(), nullable=False),
        sa.Column('reported_by_profile_id', sa.Uuid(), nullable=True),
        sa.Column('score_a', sa.SmallInteger(), nullable=False),
        sa.Column('score_b', sa.SmallInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('score_a >= 0 AND score_b >= 0 AND score_a <> score_b', name='ck_match_reports_scores'),
        sa.ForeignKeyConstraint(['match_id'], ['bracket_matches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entry_id'], ['bracket_entries.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reported_by_profile_id'], ['profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('match_id', 'entry_id', name='uq_match_reports_match_entry'),
    )

    # ---- the admin queue ------------------------------------------------------------
    op.create_table(
        'event_issues',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('match_id', sa.Uuid(), nullable=True),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('raised_by_entry_id', sa.Uuid(), nullable=True),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('status', sa.String(), server_default=sa.text("'open'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('claimed_by_profile_id', sa.Uuid(), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_by_profile_id', sa.Uuid(), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("type IN ('dispute', 'one_sided_report', 'no_show', 'post_advancement_dispute')", name='ck_event_issues_type'),
        sa.CheckConstraint("status IN ('open', 'claimed', 'dismissed', 'resolved')", name='ck_event_issues_status'),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['match_id'], ['bracket_matches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['raised_by_entry_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['claimed_by_profile_id'], ['profiles.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['closed_by_profile_id'], ['profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_event_issues_queue', 'event_issues', ['event_id', 'status', 'created_at'])
    op.create_index('ix_event_issues_match_id', 'event_issues', ['match_id'])
    op.create_index('uq_event_issues_one_dispute_per_side', 'event_issues', ['match_id', 'raised_by_entry_id'], unique=True,
                    postgresql_where=sa.text("type = 'post_advancement_dispute'"))
    op.create_index('uq_event_issues_open_per_match_type', 'event_issues', ['match_id', 'type'], unique=True,
                    postgresql_where=sa.text("status IN ('open', 'claimed') AND type <> 'post_advancement_dispute'"))

    op.create_table(
        'match_decisions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('match_id', sa.Uuid(), nullable=False),
        sa.Column('decided_by_profile_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('issue_id', sa.Uuid(), nullable=True),
        sa.Column('reverts_decision_id', sa.Uuid(), nullable=True),
        sa.Column('prev_status', sa.String(), nullable=False),
        sa.Column('prev_score_a', sa.SmallInteger(), nullable=True),
        sa.Column('prev_score_b', sa.SmallInteger(), nullable=True),
        sa.Column('prev_winner_entry_id', sa.Uuid(), nullable=True),
        sa.Column('new_status', sa.String(), nullable=False),
        sa.Column('new_score_a', sa.SmallInteger(), nullable=True),
        sa.Column('new_score_b', sa.SmallInteger(), nullable=True),
        sa.Column('new_winner_entry_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("action IN ('award', 'forfeit', 'clear', 'uphold', 'revert')", name='ck_match_decisions_action'),
        sa.ForeignKeyConstraint(['match_id'], ['bracket_matches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by_profile_id'], ['profiles.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['issue_id'], ['event_issues.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['reverts_decision_id'], ['match_decisions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['prev_winner_entry_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['new_winner_entry_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_match_decisions_match_id', 'match_decisions', ['match_id'])

    op.create_table(
        'event_issue_actions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('issue_id', sa.Uuid(), nullable=False),
        sa.Column('profile_id', sa.Uuid(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("action IN ('claim', 'release', 'dismiss', 'resolve', 'auto_resolve')", name='ck_event_issue_actions_action'),
        sa.ForeignKeyConstraint(['issue_id'], ['event_issues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_event_issue_actions_issue_id', 'event_issue_actions', ['issue_id'])

    # ---- invites: who sent them and when they were answered --------------------------
    op.add_column('team_invites', sa.Column('invited_by_profile_id', sa.Uuid(), nullable=True))
    op.add_column('team_invites', sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_team_invites_invited_by', 'team_invites', 'profiles', ['invited_by_profile_id'], ['id'], ondelete='SET NULL')

    # ---- notifications: when they were read (for the 90 day clean-up) ------------------
    op.add_column('notifications', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))

    for table in (*NEW_TABLES, 'bracket_event_admins'):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('ALTER TABLE bracket_event_admins DISABLE ROW LEVEL SECURITY')
    op.drop_column('notifications', 'read_at')
    op.drop_constraint('fk_team_invites_invited_by', 'team_invites', type_='foreignkey')
    op.drop_column('team_invites', 'responded_at')
    op.drop_column('team_invites', 'invited_by_profile_id')
    op.drop_index('ix_event_issue_actions_issue_id', table_name='event_issue_actions')
    op.drop_table('event_issue_actions')
    op.drop_index('ix_match_decisions_match_id', table_name='match_decisions')
    op.drop_table('match_decisions')
    op.drop_index('uq_event_issues_open_per_match_type', table_name='event_issues')
    op.drop_index('uq_event_issues_one_dispute_per_side', table_name='event_issues')
    op.drop_index('ix_event_issues_match_id', table_name='event_issues')
    op.drop_index('ix_event_issues_queue', table_name='event_issues')
    op.drop_table('event_issues')
    op.drop_table('match_reports')
    op.drop_index('ix_bracket_matches_entry_b_id', table_name='bracket_matches')
    op.drop_index('ix_bracket_matches_entry_a_id', table_name='bracket_matches')
    op.drop_index('ix_bracket_matches_bracket_id', table_name='bracket_matches')
    op.drop_table('bracket_matches')
    op.drop_table('bracket_seeds')
    op.drop_table('brackets')
    op.drop_index('ix_bracket_entry_players_profile_id', table_name='bracket_entry_players')
    op.drop_index('ix_bracket_entry_players_entry_id', table_name='bracket_entry_players')
    op.drop_table('bracket_entry_players')
    # Entries whose registering profile was deleted can't go back to NOT NULL; remove them first.
    op.execute('DELETE FROM bracket_entries WHERE profile_id IS NULL')
    op.drop_constraint('bracket_entries_profile_id_fkey', 'bracket_entries', type_='foreignkey')
    op.create_foreign_key('bracket_entries_profile_id_fkey', 'bracket_entries', 'profiles', ['profile_id'], ['id'], ondelete='CASCADE')
    op.alter_column('bracket_entries', 'profile_id', existing_type=sa.Uuid(), nullable=False)
