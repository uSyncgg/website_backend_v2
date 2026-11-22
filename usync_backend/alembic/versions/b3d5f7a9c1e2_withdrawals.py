"""Withdrawals: Pending Withdrawal accounts and the withdrawals table (see GLOSSARY.md).

Revision ID: b3d5f7a9c1e2
Revises: a7c3e9f1b2d4
Create Date: 2026-11-22 18:00:00.000000

Written by hand.

A withdrawal moves a wallet's Earnings into its Pending Withdrawal account, where nothing can
spend it. uSync staff then either complete it (Pending Withdrawal -> external: the money has been
sent by hand) or return it (Pending Withdrawal -> Earnings, with a reason).

- ledger_accounts gains the type 'wallet_pending_withdrawal': every wallet's third account. The
  wallets trigger now opens all three, and existing wallets get theirs here.
- The post-line trigger: a Pending Withdrawal account can never go below zero, frozen or not.
- withdrawals: one row per request. status 'pending' -> 'completed' or 'returned'. At most one
  pending per wallet (partial unique index), and the browser's idempotency key is unique.
- ledger_transactions gains withdrawal_id and the kinds withdrawal_request, withdrawal_complete
  and withdrawal_return, each at most once per withdrawal.

RLS on withdrawals with no policies, like the rest of the ledger: only the API reads it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d5f7a9c1e2'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9f1b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_ACCOUNT_TYPES = "'wallet_deposited', 'wallet_earnings', 'platform_revenue', 'external', 'event_pool', 'event_guarantee'"
NEW_ACCOUNT_TYPES = OLD_ACCOUNT_TYPES + ", 'wallet_pending_withdrawal'"
OLD_WALLET_TYPES = "'wallet_deposited', 'wallet_earnings'"
NEW_WALLET_TYPES = OLD_WALLET_TYPES + ", 'wallet_pending_withdrawal'"
OWNER_CHECK = """
    (type IN ({wallet_types})) = (wallet_id IS NOT NULL)
    AND (type IN ('event_pool', 'event_guarantee')) = (event_id IS NOT NULL)
"""

OLD_KINDS = ("'deposit', 'deposit_refund', 'chargeback', 'chargeback_reversal', 'manual_credit', 'manual_debit', "
             "'deposit_refund_reversal', 'entry_fee', 'entry_refund', 'guarantee_fund', 'guarantee_refund', 'event_payout'")
WITHDRAWAL_KINDS = "'withdrawal_request', 'withdrawal_complete', 'withdrawal_return'"
NEW_KINDS = OLD_KINDS + ", " + WITHDRAWAL_KINDS

OPEN_ACCOUNTS = """
    CREATE OR REPLACE FUNCTION wallets_open_accounts() RETURNS trigger
    LANGUAGE plpgsql SET search_path = public AS $$
    BEGIN
        INSERT INTO ledger_accounts (type, wallet_id) VALUES {values};
        RETURN NULL;
    END $$;
"""
OLD_OPEN_VALUES = "('wallet_deposited', NEW.id), ('wallet_earnings', NEW.id)"
NEW_OPEN_VALUES = OLD_OPEN_VALUES + ", ('wallet_pending_withdrawal', NEW.id)"

POST_LINE = """
    CREATE OR REPLACE FUNCTION ledger_post_line() RETURNS trigger
    LANGUAGE plpgsql SET search_path = public AS $$
    DECLARE
        acct ledger_accounts%ROWTYPE;
        frozen timestamptz;
    BEGIN
        PERFORM set_config('usync.ledger_posting', 'on', true);
        UPDATE ledger_accounts SET balance_cents = balance_cents + NEW.amount_cents
            WHERE id = NEW.ledger_account_id RETURNING * INTO acct;
        PERFORM set_config('usync.ledger_posting', 'off', true);
        IF acct.balance_cents < 0 AND acct.event_id IS NOT NULL THEN
            RAISE EXCEPTION 'Event ledger account % would go below zero', acct.id USING ERRCODE = 'check_violation';
        END IF;{pending}
        IF acct.balance_cents < 0 AND acct.wallet_id IS NOT NULL THEN
            SELECT frozen_at INTO frozen FROM wallets WHERE id = acct.wallet_id;
            IF frozen IS NULL THEN
                RAISE EXCEPTION 'Insufficient funds in ledger account %', acct.id USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NULL;
    END $$;
"""
PENDING_RULE = """
        IF acct.balance_cents < 0 AND acct.type = 'wallet_pending_withdrawal' THEN
            RAISE EXCEPTION 'Pending withdrawal account % would go below zero', acct.id USING ERRCODE = 'check_violation';
        END IF;"""


def upgrade() -> None:
    """Upgrade schema."""
    # ---- the third wallet account ------------------------------------------------------------

    op.drop_constraint('ck_ledger_accounts_type', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_type', 'ledger_accounts', f"type IN ({NEW_ACCOUNT_TYPES})")
    op.drop_constraint('ck_ledger_accounts_owner', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_owner', 'ledger_accounts', OWNER_CHECK.format(wallet_types = NEW_WALLET_TYPES))

    op.execute(OPEN_ACCOUNTS.format(values = NEW_OPEN_VALUES))
    op.execute("""
        INSERT INTO ledger_accounts (type, wallet_id)
        SELECT 'wallet_pending_withdrawal', w.id FROM wallets w
        WHERE NOT EXISTS (SELECT 1 FROM ledger_accounts a WHERE a.wallet_id = w.id AND a.type = 'wallet_pending_withdrawal')
    """)
    op.execute(POST_LINE.format(pending = PENDING_RULE))

    # ---- withdrawals ---------------------------------------------------------------------------

    op.create_table(
        'withdrawals',
        sa.Column('id', sa.Uuid(), primary_key = True, server_default = sa.text('gen_random_uuid()')),
        sa.Column('wallet_id', sa.Uuid(), sa.ForeignKey('wallets.id', ondelete = 'RESTRICT'), nullable = False),
        sa.Column('amount_cents', sa.BigInteger(), nullable = False),
        sa.Column('status', sa.Text(), nullable = False, server_default = sa.text("'pending'")),
        sa.Column('requested_by_account_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete = 'SET NULL'), nullable = True),
        sa.Column('requested_at', sa.DateTime(timezone = True), nullable = False, server_default = sa.text('now()')),
        sa.Column('decided_by_account_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete = 'SET NULL'), nullable = True),
        sa.Column('decided_at', sa.DateTime(timezone = True), nullable = True),
        sa.Column('return_reason', sa.Text(), nullable = True),
        sa.Column('external_reference', sa.Text(), nullable = True),
        sa.Column('idempotency_key', sa.Text(), nullable = False),
        sa.UniqueConstraint('idempotency_key', name = 'uq_withdrawals_idempotency_key'),
        sa.CheckConstraint('amount_cents > 0', name = 'ck_withdrawals_amount'),
        sa.CheckConstraint("status IN ('pending', 'completed', 'returned')", name = 'ck_withdrawals_status'),
        sa.CheckConstraint("(status = 'pending') = (decided_at IS NULL)", name = 'ck_withdrawals_decided_at'),
        sa.CheckConstraint("(status = 'returned') = (return_reason IS NOT NULL)", name = 'ck_withdrawals_return_reason'),
        sa.CheckConstraint("status = 'completed' OR external_reference IS NULL", name = 'ck_withdrawals_external_reference'),
    )
    op.create_index('uq_withdrawals_one_pending', 'withdrawals', ['wallet_id'], unique = True,
                    postgresql_where = sa.text("status = 'pending'"))
    op.create_index('ix_withdrawals_status_requested', 'withdrawals', ['status', 'requested_at'])

    # ---- ledger transactions that belong to a withdrawal ----------------------------------------

    op.add_column('ledger_transactions', sa.Column('withdrawal_id', sa.Uuid(), nullable = True))
    op.create_foreign_key('ledger_transactions_withdrawal_id_fkey', 'ledger_transactions', 'withdrawals',
                          ['withdrawal_id'], ['id'], ondelete = 'RESTRICT')
    op.drop_constraint('ck_ledger_transactions_kind', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_kind', 'ledger_transactions', f"kind IN ({NEW_KINDS})")
    op.create_check_constraint('ck_ledger_transactions_withdrawal', 'ledger_transactions',
                               f"(kind IN ({WITHDRAWAL_KINDS})) = (withdrawal_id IS NOT NULL)")
    op.create_index('uq_ledger_transactions_withdrawal_kind', 'ledger_transactions', ['withdrawal_id', 'kind'], unique = True,
                    postgresql_where = sa.text('withdrawal_id IS NOT NULL'))

    op.execute('ALTER TABLE public.withdrawals ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema. Fails (on purpose) once any withdrawal has moved money."""
    op.drop_index('uq_ledger_transactions_withdrawal_kind', table_name = 'ledger_transactions')
    op.drop_constraint('ck_ledger_transactions_withdrawal', 'ledger_transactions', type_ = 'check')
    op.drop_constraint('ck_ledger_transactions_kind', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_kind', 'ledger_transactions', f"kind IN ({OLD_KINDS})")
    op.drop_constraint('ledger_transactions_withdrawal_id_fkey', 'ledger_transactions', type_ = 'foreignkey')
    op.drop_column('ledger_transactions', 'withdrawal_id')

    op.drop_table('withdrawals')

    op.execute(POST_LINE.format(pending = ""))
    op.execute(OPEN_ACCOUNTS.format(values = OLD_OPEN_VALUES))
    # Ledger accounts are never deleted, except here: an unused Pending Withdrawal account. Any
    # account with lines can't go (the lines' foreign key refuses).
    op.execute('ALTER TABLE ledger_accounts DISABLE TRIGGER ledger_accounts_guard')
    op.execute("DELETE FROM ledger_accounts WHERE type = 'wallet_pending_withdrawal'")
    op.execute('ALTER TABLE ledger_accounts ENABLE TRIGGER ledger_accounts_guard')
    op.drop_constraint('ck_ledger_accounts_owner', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_owner', 'ledger_accounts', OWNER_CHECK.format(wallet_types = OLD_WALLET_TYPES))
    op.drop_constraint('ck_ledger_accounts_type', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_type', 'ledger_accounts', f"type IN ({OLD_ACCOUNT_TYPES})")
