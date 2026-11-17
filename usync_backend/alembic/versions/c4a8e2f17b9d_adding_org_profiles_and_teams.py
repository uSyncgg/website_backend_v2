"""Adding org profiles, teams, team members and team invites.

Revision ID: c4a8e2f17b9d
Revises: e7b2c4a91d05
Create Date: 2026-11-05 12:00:00.000000

Written by hand. Adds 'org' to the profile_type enum, an organizations table for the org
profile's details, and teams (owned by any profile: an org's teams or a player's own) with
their members and invites.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4a8e2f17b9d'
down_revision: Union[str, Sequence[str], None] = 'e7b2c4a91d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A new enum value can't be used in the transaction that adds it, so add it on its own.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE profile_type ADD VALUE IF NOT EXISTS 'org'")

    op.create_table(
        'organizations',
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('organization', sa.String(), nullable=False),
        sa.Column('org_country', sa.String(), nullable=False),
        sa.Column('games', postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column('other_games', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('profile_id', name='organizations_pkey'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], name='organizations_profile_id_fkey', ondelete='CASCADE'),
    )
    op.execute('ALTER TABLE organizations ENABLE ROW LEVEL SECURITY')

    op.create_table(
        'teams',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('public_id', sa.String(), nullable=False),
        sa.Column('owner_profile_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('canonical_name', sa.String(), nullable=False),
        sa.Column('game', sa.String(), nullable=True),
        sa.Column('picture', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name='teams_pkey'),
        sa.ForeignKeyConstraint(['owner_profile_id'], ['profiles.id'], name='teams_owner_profile_id_fkey', ondelete='CASCADE'),
        sa.UniqueConstraint('public_id', name='uq_teams_public_id'),
        sa.UniqueConstraint('owner_profile_id', 'canonical_name', name='uq_teams_owner_canonical_name'),
    )
    op.create_index('ix_teams_owner_profile_id', 'teams', ['owner_profile_id'])
    op.execute('ALTER TABLE teams ENABLE ROW LEVEL SECURITY')

    op.create_table(
        'team_members',
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(), server_default=sa.text("'player'"), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('team_id', 'profile_id', name='team_members_pkey'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name='team_members_team_id_fkey', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], name='team_members_profile_id_fkey', ondelete='CASCADE'),
        sa.CheckConstraint("role IN ('captain', 'player')", name='ck_team_members_role'),
    )
    op.create_index('ix_team_members_profile_id', 'team_members', ['profile_id'])
    op.execute('ALTER TABLE team_members ENABLE ROW LEVEL SECURITY')

    op.create_table(
        'team_invites',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('status', sa.String(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name='team_invites_pkey'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name='team_invites_team_id_fkey', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], name='team_invites_profile_id_fkey', ondelete='CASCADE'),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'declined', 'cancelled')", name='ck_team_invites_status'),
    )
    op.create_index('ix_team_invites_team_id', 'team_invites', ['team_id'])
    op.create_index('ix_team_invites_profile_id', 'team_invites', ['profile_id'])
    op.create_index(
        'uq_team_invites_pending', 'team_invites', ['team_id', 'profile_id'],
        unique=True, postgresql_where=sa.text("status = 'pending'")
    )
    op.execute('ALTER TABLE team_invites ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('team_invites')
    op.drop_table('team_members')
    op.drop_table('teams')
    op.drop_table('organizations')

    # Postgres can't drop an enum value, so rebuild the type without 'org' (org profiles go first).
    op.execute("DELETE FROM profiles WHERE type = 'org'")
    op.execute("ALTER TYPE profile_type RENAME TO profile_type_old")
    op.execute("CREATE TYPE profile_type AS ENUM ('player', 'host', 'venue')")
    op.execute("ALTER TABLE profiles ALTER COLUMN type TYPE profile_type USING type::text::profile_type")
    op.execute("DROP TYPE profile_type_old")
