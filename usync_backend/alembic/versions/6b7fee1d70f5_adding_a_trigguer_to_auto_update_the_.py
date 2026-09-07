"""Adding a trigguer to auto update the updated at timing for event entries.

Revision ID: 6b7fee1d70f5
Revises: 96415aae4f9a
Create Date: 2026-09-04 23:08:30.834905

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6b7fee1d70f5'
down_revision: Union[str, Sequence[str], None] = '96415aae4f9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ["lan_events", "league_events", "league_parent_events", "wager_events", "xp_events"]

def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
          CREATE OR REPLACE FUNCTION set_updated_at()
          RETURNS trigger AS $$
          BEGIN
            NEW.updated_at = now();
            RETURN NEW;
          END;
          $$ LANGUAGE plpgsql;
      """)
    for table in TABLES:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """)


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table};")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")

