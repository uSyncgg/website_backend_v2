"""Adding verified, website and contact_email to organizations.

Revision ID: f8d2b6c41a07
Revises: e7c1a4b92d3f
Create Date: 2026-11-13 12:00:00.000000

Written by hand. `verified` is set by uSync (never by the org) and starts false for every org;
`website` and `contact_email` are optional and shown on the org's public profile.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8d2b6c41a07'
down_revision: Union[str, Sequence[str], None] = 'e7c1a4b92d3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('organizations', sa.Column('verified', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('organizations', sa.Column('website', sa.String(), nullable=True))
    op.add_column('organizations', sa.Column('contact_email', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('organizations', 'contact_email')
    op.drop_column('organizations', 'website')
    op.drop_column('organizations', 'verified')
