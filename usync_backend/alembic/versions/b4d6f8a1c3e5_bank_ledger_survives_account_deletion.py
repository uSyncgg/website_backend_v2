"""Bank: keep the ledger (de-identified) when an account is deleted

Revision ID: b4d6f8a1c3e5
Revises: a3c5e7b9d1f4
Create Date: 2026-11-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b4d6f8a1c3e5'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7b9d1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Transactions and entry payments are financial records the privacy policy says we keep for
    # seven years. Deleting an account now clears who they belong to instead of deleting them.
    op.alter_column('wallet_transactions', 'account_id', nullable=True)
    op.drop_constraint('wallet_transactions_account_id_fkey', 'wallet_transactions', type_='foreignkey')
    op.create_foreign_key('wallet_transactions_account_id_fkey', 'wallet_transactions', 'users', ['account_id'], ['id'], ondelete='SET NULL')

    op.alter_column('bracket_entry_payments', 'payer_account_id', nullable=True)
    op.drop_constraint('bracket_entry_payments_payer_account_id_fkey', 'bracket_entry_payments', type_='foreignkey')
    op.create_foreign_key('bracket_entry_payments_payer_account_id_fkey', 'bracket_entry_payments', 'users', ['payer_account_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('bracket_entry_payments_payer_account_id_fkey', 'bracket_entry_payments', type_='foreignkey')
    op.create_foreign_key('bracket_entry_payments_payer_account_id_fkey', 'bracket_entry_payments', 'users', ['payer_account_id'], ['id'], ondelete='CASCADE')
    op.execute('DELETE FROM bracket_entry_payments WHERE payer_account_id IS NULL')
    op.alter_column('bracket_entry_payments', 'payer_account_id', nullable=False)

    op.drop_constraint('wallet_transactions_account_id_fkey', 'wallet_transactions', type_='foreignkey')
    op.create_foreign_key('wallet_transactions_account_id_fkey', 'wallet_transactions', 'users', ['account_id'], ['id'], ondelete='CASCADE')
    op.execute('DELETE FROM wallet_transactions WHERE account_id IS NULL')
    op.alter_column('wallet_transactions', 'account_id', nullable=False)
