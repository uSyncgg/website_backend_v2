"""Terms of Service acceptance on users; players can no longer host brackets.

Revision ID: a7c3e9f1b2d4
Revises: f0a2c4e6b8d1
Create Date: 2026-11-16 12:00:00.000000

Written by hand. Adds users.tos_version and users.tos_accepted_at (set at registration from the
sign-up form's required Terms checkbox; null for older accounts), and turns bracket hosting off
for every player profile, since only host and venue profiles can run brackets now.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3e9f1b2d4'
down_revision: Union[str, Sequence[str], None] = 'f0a2c4e6b8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('tos_version', sa.String(length=32), nullable=True))
    op.add_column('users', sa.Column('tos_accepted_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE profiles SET bracket_hosting = false WHERE type = 'player'")


def downgrade() -> None:
    """Downgrade schema. Player bracket_hosting values are not restored."""
    op.drop_column('users', 'tos_accepted_at')
    op.drop_column('users', 'tos_version')
