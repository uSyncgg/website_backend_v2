"""Live notifications: let each signed-in user read their own notifications over Supabase Realtime.

Revision ID: c5e7a9b1d3f5
Revises: b4d6f8a0c2e4
Create Date: 2026-11-14 13:00:00.000000

Written by hand. Two things:

1. An RLS policy on notifications. RLS is already on for this table with no policies, so
   nothing but the backend (which bypasses RLS) can read it. This adds the one thing browsers
   may do: a signed-in user SELECTs the rows whose account_id is their own auth user id.
   Nobody else's rows, and no INSERT / UPDATE / DELETE - the backend still writes everything.
2. Adds notifications to the supabase_realtime publication, so Supabase streams new rows to
   subscribed browsers. Realtime checks the policy above for each subscriber before sending
   a row, so a user only ever receives their own.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c5e7a9b1d3f5'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a0c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE POLICY notifications_select_own ON notifications
            FOR SELECT
            TO authenticated
            USING (account_id = (SELECT auth.uid()))
    """)
    op.execute('ALTER PUBLICATION supabase_realtime ADD TABLE notifications')


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('ALTER PUBLICATION supabase_realtime DROP TABLE notifications')
    op.execute('DROP POLICY notifications_select_own ON notifications')
