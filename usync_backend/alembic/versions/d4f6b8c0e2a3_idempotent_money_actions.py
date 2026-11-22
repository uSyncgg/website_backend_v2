"""Idempotency keys for money actions, and refunds that survive a failure halfway.

Revision ID: d4f6b8c0e2a3
Revises: c2e4a6b8d0f1
Create Date: 2026-11-21 18:00:00.000000

Written by hand.
- deposits.idempotency_key (required, unique): the browser sends one key per deposit dialog, so
  a retried or double-submitted "start deposit" returns the same deposit and PaymentIntent
  instead of making another. The table is empty, so NOT NULL needs no backfill.
- ledger_transactions.idempotency_key (optional, unique when set): manual credits and debits
  carry the key from the admin form, so a double click or a retry posts once.
- deposits.status gains 'refund_pending' (money already taken out of the wallet, the Stripe
  refund not confirmed yet; the sweeper retries) and 'refund_failed' (Stripe refused for good;
  the money was put back).
- ledger_transactions.kind gains 'deposit_refund_reversal' (puts a refund's money back when
  Stripe refuses it). (deposit_id, kind) stays unique, so that happens at most once too.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4f6b8c0e2a3'
down_revision: Union[str, Sequence[str], None] = 'c2e4a6b8d0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KINDS = "'deposit', 'deposit_refund', 'chargeback', 'chargeback_reversal', 'manual_credit', 'manual_debit'"
NEW_KINDS = OLD_KINDS + ", 'deposit_refund_reversal'"
OLD_STATUSES = "'pending', 'succeeded', 'failed', 'canceled', 'refunded', 'charged_back'"
NEW_STATUSES = OLD_STATUSES + ", 'refund_pending', 'refund_failed'"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('deposits', sa.Column('idempotency_key', sa.Text(), nullable = False))
    op.create_unique_constraint('uq_deposits_idempotency_key', 'deposits', ['idempotency_key'])
    op.drop_constraint('ck_deposits_status', 'deposits', type_ = 'check')
    op.create_check_constraint('ck_deposits_status', 'deposits', f"status IN ({NEW_STATUSES})")

    op.add_column('ledger_transactions', sa.Column('idempotency_key', sa.Text(), nullable = True))
    op.create_index('uq_ledger_transactions_idempotency_key', 'ledger_transactions', ['idempotency_key'], unique = True,
                    postgresql_where = sa.text('idempotency_key IS NOT NULL'))
    op.drop_constraint('ck_ledger_transactions_kind', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_kind', 'ledger_transactions', f"kind IN ({NEW_KINDS})")


def downgrade() -> None:
    """Downgrade schema. Fails if a reversal or a refund_pending/failed deposit exists (on purpose)."""
    op.drop_constraint('ck_ledger_transactions_kind', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_kind', 'ledger_transactions', f"kind IN ({OLD_KINDS})")
    op.drop_index('uq_ledger_transactions_idempotency_key', table_name = 'ledger_transactions')
    op.drop_column('ledger_transactions', 'idempotency_key')

    op.drop_constraint('ck_deposits_status', 'deposits', type_ = 'check')
    op.create_check_constraint('ck_deposits_status', 'deposits', f"status IN ({OLD_STATUSES})")
    op.drop_constraint('uq_deposits_idempotency_key', 'deposits', type_ = 'unique')
    op.drop_column('deposits', 'idempotency_key')
