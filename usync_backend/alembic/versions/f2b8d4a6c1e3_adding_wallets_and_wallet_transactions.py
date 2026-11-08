"""adding wallets and wallet transactions (the Bank)

Revision ID: f2b8d4a6c1e3
Revises: e7c1a4b92d3f
Create Date: 2026-11-08 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2b8d4a6c1e3'
down_revision: Union[str, Sequence[str], None] = 'e7c1a4b92d3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'wallets',
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('balance_cents', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('balance_cents >= 0', name='ck_wallets_balance_not_negative'),
        sa.ForeignKeyConstraint(['account_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('account_id'),
    )
    op.create_table(
        'wallet_transactions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('account_id', sa.Uuid(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('status', sa.String(), server_default=sa.text("'completed'"), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('fee_cents', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('description', sa.String(), server_default=sa.text("''"), nullable=False),
        sa.Column('event_public_id', sa.String(), nullable=True),
        sa.Column('entry_id', sa.Uuid(), nullable=True),
        sa.Column('available_on', sa.DateTime(timezone=True), nullable=True),
        sa.Column('provider_ref', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("type IN ('deposit', 'withdrawal', 'entry_fee', 'entry_refund')", name='ck_wallet_transactions_type'),
        sa.CheckConstraint("status IN ('completed', 'pending', 'cancelled')", name='ck_wallet_transactions_status'),
        sa.ForeignKeyConstraint(['account_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_wallet_transactions_account_created', 'wallet_transactions', ['account_id', 'created_at'])
    op.create_index(op.f('ix_wallet_transactions_entry_id'), 'wallet_transactions', ['entry_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_wallet_transactions_entry_id'), table_name='wallet_transactions')
    op.drop_index('ix_wallet_transactions_account_created', table_name='wallet_transactions')
    op.drop_table('wallet_transactions')
    op.drop_table('wallets')
