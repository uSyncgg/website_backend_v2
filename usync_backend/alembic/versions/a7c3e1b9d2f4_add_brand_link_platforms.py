"""Add brand link platforms (website, tiktok, linkedin, facebook, linktree)

Revision ID: a7c3e1b9d2f4
Revises: f3eda954f36a
Create Date: 2026-11-03

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c3e1b9d2f4'
down_revision: Union[str, Sequence[str], None] = 'f3eda954f36a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_PLATFORMS = ("website", "tiktok", "linkedin", "facebook", "linktree")
OLD_PLATFORMS = (
    "twitch", "twitter", "youtube", "kick", "discord", "instagram",
    "cmg", "gankster", "faceit", "battlefy",
)


def upgrade() -> None:
    """Upgrade schema."""
    # ALTER TYPE ... ADD VALUE can't run inside a transaction block on older Postgres versions.
    with op.get_context().autocommit_block():
        for platform in NEW_PLATFORMS:
            op.execute(f"ALTER TYPE link_platform ADD VALUE IF NOT EXISTS '{platform}'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres can't drop enum values, so links on the new platforms are deleted and the type is
    # rebuilt with only the old ones. The gist exclude constraint uses the column, so it's
    # dropped and recreated around the type swap.
    platforms = ", ".join(f"'{p}'" for p in NEW_PLATFORMS)
    op.execute(f"DELETE FROM profile_links WHERE platform IN ({platforms})")
    op.execute("ALTER TABLE profile_links DROP CONSTRAINT ex_profile_links_claimed")
    op.execute("ALTER TYPE link_platform RENAME TO link_platform_old")
    op.execute(f"CREATE TYPE link_platform AS ENUM ({', '.join(repr(p) for p in OLD_PLATFORMS)})")
    op.execute(
        "ALTER TABLE profile_links ALTER COLUMN platform TYPE link_platform "
        "USING platform::text::link_platform"
    )
    op.execute("DROP TYPE link_platform_old")
    op.execute(
        "ALTER TABLE profile_links ADD CONSTRAINT ex_profile_links_claimed "
        "EXCLUDE USING gist (platform WITH =, canonical_handle WITH =, account_id WITH <>)"
    )
