"""Merge point: staging's admins / RLS migration and the bracket redesign.

Revision ID: f0a2c4e6b8d1
Revises: b4f1a7d2c963, d6f8b0c2e4a6
Create Date: 2026-11-15 18:00:00.000000

Written by hand. No schema changes: it only joins the two migration branches so Alembic has one
head again. b4f1a7d2c963 (staging: the admins and admin_audit_log tables, and RLS on every table
exposed through Supabase) and the bracket chain ending in d6f8b0c2e4a6 both came off older
revisions independently and don't touch the same tables, so their order doesn't matter.
"""
from typing import Sequence, Union


revision: str = 'f0a2c4e6b8d1'
down_revision: Union[str, Sequence[str], None] = ('b4f1a7d2c963', 'd6f8b0c2e4a6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
