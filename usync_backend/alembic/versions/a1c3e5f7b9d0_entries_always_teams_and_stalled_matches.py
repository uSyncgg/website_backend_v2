"""Every entry comes from a team, no waitlist, and stalled matches go in the admin queue.

Revision ID: a1c3e5f7b9d0
Revises: f0a2c4e6b8d1
Create Date: 2026-11-20 12:00:00.000000

Written by hand.
- bracket_entries: drop uq_bracket_entries_team (a team can now enter a solo event once per
  player) and uq_bracket_entries_solo (entries without a team are no longer created). "One entry
  per team" in team events is checked by the API under the event's row lock.
- bracket_entries.status: 'pending' becomes 'needs_players'. There's no waitlist any more, so the
  only entries that aren't signed up are ones that lost a picked player before the bracket was
  published.
- event_issues.type gains 'stalled_match': a ready match nobody has reported for too long.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1c3e5f7b9d0'
down_revision: Union[str, Sequence[str], None] = 'f0a2c4e6b8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index('uq_bracket_entries_team', table_name = 'bracket_entries')
    op.drop_index('uq_bracket_entries_solo', table_name = 'bracket_entries')

    op.drop_constraint('ck_bracket_entries_status', 'bracket_entries', type_ = 'check')
    op.execute("UPDATE bracket_entries SET status = 'needs_players' WHERE status = 'pending'")
    op.create_check_constraint('ck_bracket_entries_status', 'bracket_entries', "status IN ('signed_up', 'needs_players')")

    op.drop_constraint('ck_event_issues_type', 'event_issues', type_ = 'check')
    op.create_check_constraint(
        'ck_event_issues_type', 'event_issues',
        "type IN ('dispute', 'one_sided_report', 'no_show', 'post_advancement_dispute', 'stalled_match')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM event_issues WHERE type = 'stalled_match'")
    op.drop_constraint('ck_event_issues_type', 'event_issues', type_ = 'check')
    op.create_check_constraint(
        'ck_event_issues_type', 'event_issues',
        "type IN ('dispute', 'one_sided_report', 'no_show', 'post_advancement_dispute')",
    )

    op.drop_constraint('ck_bracket_entries_status', 'bracket_entries', type_ = 'check')
    op.execute("UPDATE bracket_entries SET status = 'pending' WHERE status = 'needs_players'")
    op.create_check_constraint('ck_bracket_entries_status', 'bracket_entries', "status IN ('signed_up', 'pending')")

    # Fails if a team now has two entries in one event (solo events allow that after upgrade).
    op.create_index('uq_bracket_entries_solo', 'bracket_entries', ['event_id', 'profile_id'], unique = True,
                    postgresql_where = "team_id IS NULL")
    op.create_index('uq_bracket_entries_team', 'bracket_entries', ['event_id', 'team_id'], unique = True,
                    postgresql_where = "team_id IS NOT NULL")
