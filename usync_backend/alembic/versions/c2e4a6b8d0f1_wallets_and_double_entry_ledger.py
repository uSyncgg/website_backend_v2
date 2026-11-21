"""Wallets, the double-entry ledger and deposits (see docs/adr/0001-double-entry-wallet-ledger.md).

Revision ID: c2e4a6b8d0f1
Revises: a1c3e5f7b9d0
Create Date: 2026-11-21 12:00:00.000000

Written by hand: the triggers aren't something autogenerate can see.

Tables
- wallets: one per account that has at least one profile (a trigger on profiles makes it). Holds
  the wallet's settings, not its money. account_id is nulled when the account is deleted, so the
  history stays; the API refuses to delete an account whose wallet isn't empty or is frozen.
- ledger_accounts: everything that holds money. Each wallet has a 'wallet_deposited' and a
  'wallet_earnings' account (made by a trigger on wallets); uSync has one 'platform_revenue' and
  one 'external' (the outside world: Stripe, banks). balance_cents is kept by the ledger_lines
  trigger and can't be edited any other way.
- ledger_transactions: one movement of money (a deposit, a refund, a chargeback...), with what it
  was for. ledger_lines: its signed amounts, one per account touched; positive = money in.
- deposits: the Stripe side of a deposit (its PaymentIntent and status).

Rules Postgres enforces
1. A transaction's lines sum to zero and there are at least two (checked at COMMIT, deferred).
2. Ledger transactions and lines can never be updated or deleted.
3. A line moves its account's balance; nothing else can.
4. Wallet accounts can't go below zero unless the wallet is frozen; a wallet can't be unfrozen
   while one of its accounts is below zero. uSync's own accounts may go negative.

RLS is on with no policies: the browser can't read any of this through Supabase; only the
backend (which bypasses RLS) can.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c2e4a6b8d0f1'
down_revision: Union[str, Sequence[str], None] = 'a1c3e5f7b9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('wallets', 'ledger_accounts', 'ledger_transactions', 'ledger_lines', 'deposits')


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('admins', sa.Column('can_manage_money', sa.Boolean(), nullable = False, server_default = sa.text('false')))

    # ---- tables -----------------------------------------------------------------------

    op.create_table(
        'wallets',
        sa.Column('id', sa.Uuid(), primary_key = True, server_default = sa.text('gen_random_uuid()')),
        sa.Column('account_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete = 'SET NULL'), nullable = True),
        sa.Column('frozen_at', sa.DateTime(timezone = True), nullable = True),
        sa.Column('created_at', sa.DateTime(timezone = True), nullable = False, server_default = sa.text('now()')),
        sa.UniqueConstraint('account_id', name = 'uq_wallets_account'),
    )

    op.create_table(
        'ledger_accounts',
        sa.Column('id', sa.Uuid(), primary_key = True, server_default = sa.text('gen_random_uuid()')),
        sa.Column('type', sa.Text(), nullable = False),
        sa.Column('wallet_id', sa.Uuid(), sa.ForeignKey('wallets.id', ondelete = 'RESTRICT'), nullable = True),
        sa.Column('balance_cents', sa.BigInteger(), nullable = False, server_default = sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone = True), nullable = False, server_default = sa.text('now()')),
        sa.CheckConstraint(
            "type IN ('wallet_deposited', 'wallet_earnings', 'platform_revenue', 'external')",
            name = 'ck_ledger_accounts_type',
        ),
        # Wallet accounts belong to a wallet; uSync's own accounts don't.
        sa.CheckConstraint(
            "(type IN ('wallet_deposited', 'wallet_earnings')) = (wallet_id IS NOT NULL)",
            name = 'ck_ledger_accounts_owner',
        ),
    )
    op.create_index('uq_ledger_accounts_wallet_type', 'ledger_accounts', ['wallet_id', 'type'], unique = True,
                    postgresql_where = sa.text('wallet_id IS NOT NULL'))
    op.create_index('uq_ledger_accounts_system_type', 'ledger_accounts', ['type'], unique = True,
                    postgresql_where = sa.text("type IN ('platform_revenue', 'external')"))

    op.create_table(
        'deposits',
        sa.Column('id', sa.Uuid(), primary_key = True, server_default = sa.text('gen_random_uuid()')),
        sa.Column('wallet_id', sa.Uuid(), sa.ForeignKey('wallets.id', ondelete = 'RESTRICT'), nullable = False),
        sa.Column('amount_cents', sa.BigInteger(), nullable = False),
        sa.Column('fee_cents', sa.BigInteger(), nullable = False),
        sa.Column('stripe_payment_intent_id', sa.Text(), nullable = False),
        sa.Column('status', sa.Text(), nullable = False, server_default = sa.text("'pending'")),
        sa.Column('created_at', sa.DateTime(timezone = True), nullable = False, server_default = sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone = True), nullable = True),
        sa.UniqueConstraint('stripe_payment_intent_id', name = 'uq_deposits_payment_intent'),
        sa.CheckConstraint('amount_cents >= 500', name = 'ck_deposits_minimum'),
        sa.CheckConstraint('fee_cents >= 0', name = 'ck_deposits_fee'),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'canceled', 'refunded', 'charged_back')",
            name = 'ck_deposits_status',
        ),
    )
    op.create_index('ix_deposits_wallet_created', 'deposits', ['wallet_id', 'created_at'])

    op.create_table(
        'ledger_transactions',
        sa.Column('id', sa.Uuid(), primary_key = True, server_default = sa.text('gen_random_uuid()')),
        sa.Column('kind', sa.Text(), nullable = False),
        sa.Column('deposit_id', sa.Uuid(), sa.ForeignKey('deposits.id', ondelete = 'RESTRICT'), nullable = True),
        # Who did it (an admin, for manual ones). No foreign key: this row can never change, so it
        # can't be nulled when that account is deleted. admin_audit_log has the same record.
        sa.Column('created_by_account_id', sa.Uuid(), nullable = True),
        sa.Column('reason', sa.Text(), nullable = True),
        sa.Column('reference', sa.Text(), nullable = True),
        sa.Column('created_at', sa.DateTime(timezone = True), nullable = False, server_default = sa.text('now()')),
        sa.CheckConstraint(
            "kind IN ('deposit', 'deposit_refund', 'chargeback', 'chargeback_reversal', 'manual_credit', 'manual_debit')",
            name = 'ck_ledger_transactions_kind',
        ),
        sa.CheckConstraint(
            "kind NOT IN ('manual_credit', 'manual_debit') OR (reason IS NOT NULL AND reference IS NOT NULL)",
            name = 'ck_ledger_transactions_manual_reason',
        ),
    )
    # A deposit is credited, refunded, charged back and reinstated at most once each.
    op.create_index('uq_ledger_transactions_deposit_kind', 'ledger_transactions', ['deposit_id', 'kind'], unique = True,
                    postgresql_where = sa.text('deposit_id IS NOT NULL'))

    op.create_table(
        'ledger_lines',
        sa.Column('id', sa.BigInteger(), sa.Identity(always = True), primary_key = True),
        sa.Column('transaction_id', sa.Uuid(), sa.ForeignKey('ledger_transactions.id', ondelete = 'RESTRICT'), nullable = False),
        sa.Column('ledger_account_id', sa.Uuid(), sa.ForeignKey('ledger_accounts.id', ondelete = 'RESTRICT'), nullable = False),
        sa.Column('amount_cents', sa.BigInteger(), nullable = False),
        sa.CheckConstraint('amount_cents <> 0', name = 'ck_ledger_lines_nonzero'),
    )
    op.create_index('ix_ledger_lines_transaction', 'ledger_lines', ['transaction_id'])
    op.create_index('ix_ledger_lines_account', 'ledger_lines', ['ledger_account_id', 'id'])

    # ---- rule 1: every transaction balances (checked at COMMIT) -------------------------

    op.execute("""
        CREATE FUNCTION ledger_assert_balanced(txn uuid) RETURNS void
        LANGUAGE plpgsql SET search_path = public AS $$
        DECLARE
            total bigint;
            lines int;
        BEGIN
            SELECT coalesce(sum(amount_cents), 0), count(*) INTO total, lines FROM ledger_lines WHERE transaction_id = txn;
            IF lines < 2 OR total <> 0 THEN
                RAISE EXCEPTION 'Ledger transaction % does not balance (% lines, sum %)', txn, lines, total;
            END IF;
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION ledger_transactions_check() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            PERFORM ledger_assert_balanced(NEW.id);
            RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE FUNCTION ledger_lines_check() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            PERFORM ledger_assert_balanced(NEW.transaction_id);
            RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER ledger_transactions_balanced AFTER INSERT ON ledger_transactions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_transactions_check();
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER ledger_lines_balanced AFTER INSERT ON ledger_lines
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_lines_check();
    """)

    # ---- rule 2: the ledger is append-only ----------------------------------------------

    op.execute("""
        CREATE FUNCTION ledger_append_only() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: post a new transaction to correct it', TG_TABLE_NAME;
        END $$;
    """)
    for table in ('ledger_transactions', 'ledger_lines'):
        op.execute(f"""
            CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
        """)
        op.execute(f"""
            CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();
        """)

    # ---- rules 3 and 4: lines move balances, and only lines do ---------------------------

    op.execute("""
        CREATE FUNCTION ledger_post_line() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        DECLARE
            acct ledger_accounts%ROWTYPE;
            frozen timestamptz;
        BEGIN
            PERFORM set_config('usync.ledger_posting', 'on', true);
            UPDATE ledger_accounts SET balance_cents = balance_cents + NEW.amount_cents
                WHERE id = NEW.ledger_account_id RETURNING * INTO acct;
            PERFORM set_config('usync.ledger_posting', 'off', true);
            IF acct.balance_cents < 0 AND acct.wallet_id IS NOT NULL THEN
                SELECT frozen_at INTO frozen FROM wallets WHERE id = acct.wallet_id;
                IF frozen IS NULL THEN
                    RAISE EXCEPTION 'Insufficient funds in ledger account %', acct.id USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER ledger_lines_post AFTER INSERT ON ledger_lines
        FOR EACH ROW EXECUTE FUNCTION ledger_post_line();
    """)
    op.execute("""
        CREATE FUNCTION ledger_accounts_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Ledger accounts are never deleted';
            END IF;
            IF NEW.type <> OLD.type OR NEW.wallet_id IS DISTINCT FROM OLD.wallet_id THEN
                RAISE EXCEPTION 'A ledger account''s type and wallet never change';
            END IF;
            IF NEW.balance_cents <> OLD.balance_cents AND current_setting('usync.ledger_posting', true) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'Balances only change by posting ledger lines';
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER ledger_accounts_guard BEFORE UPDATE OR DELETE ON ledger_accounts
        FOR EACH ROW EXECUTE FUNCTION ledger_accounts_guard();
    """)
    op.execute("""
        CREATE FUNCTION wallets_unfreeze_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            IF OLD.frozen_at IS NOT NULL AND NEW.frozen_at IS NULL
               AND EXISTS (SELECT 1 FROM ledger_accounts WHERE wallet_id = NEW.id AND balance_cents < 0) THEN
                RAISE EXCEPTION 'Wallet % is below zero, so it stays frozen', NEW.id;
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER wallets_unfreeze_guard BEFORE UPDATE ON wallets
        FOR EACH ROW EXECUTE FUNCTION wallets_unfreeze_guard();
    """)

    # ---- wallets come with their accounts, and accounts with a profile get a wallet ---

    op.execute("""
        CREATE FUNCTION wallets_open_accounts() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            INSERT INTO ledger_accounts (type, wallet_id) VALUES ('wallet_deposited', NEW.id), ('wallet_earnings', NEW.id);
            RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER wallets_open_accounts AFTER INSERT ON wallets
        FOR EACH ROW EXECUTE FUNCTION wallets_open_accounts();
    """)
    op.execute("""
        CREATE FUNCTION profiles_open_wallet() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            INSERT INTO wallets (account_id) VALUES (NEW.account_id) ON CONFLICT (account_id) DO NOTHING;
            RETURN NULL;
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER profiles_open_wallet AFTER INSERT ON profiles
        FOR EACH ROW EXECUTE FUNCTION profiles_open_wallet();
    """)

    # ---- data: uSync's own accounts, and a wallet for every account with a profile ------

    op.execute("INSERT INTO ledger_accounts (type) VALUES ('platform_revenue'), ('external')")
    op.execute("INSERT INTO wallets (account_id) SELECT DISTINCT account_id FROM profiles ON CONFLICT (account_id) DO NOTHING")

    for table in TABLES:
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema. Throws away every wallet and its history: only for a fresh environment."""
    op.execute('DROP TRIGGER IF EXISTS profiles_open_wallet ON profiles')
    op.execute('DROP TRIGGER IF EXISTS ledger_lines_append_only ON ledger_lines')
    op.execute('DROP TRIGGER IF EXISTS ledger_lines_no_truncate ON ledger_lines')
    op.execute('DROP TRIGGER IF EXISTS ledger_transactions_append_only ON ledger_transactions')
    op.execute('DROP TRIGGER IF EXISTS ledger_transactions_no_truncate ON ledger_transactions')
    op.execute('DROP TRIGGER IF EXISTS ledger_accounts_guard ON ledger_accounts')
    op.drop_table('ledger_lines')
    op.drop_table('ledger_transactions')
    op.drop_table('deposits')
    op.drop_table('ledger_accounts')
    op.drop_table('wallets')
    for fn in ('profiles_open_wallet', 'wallets_open_accounts', 'wallets_unfreeze_guard', 'ledger_accounts_guard',
               'ledger_post_line', 'ledger_append_only', 'ledger_lines_check', 'ledger_transactions_check'):
        op.execute(f'DROP FUNCTION IF EXISTS {fn}()')
    op.execute('DROP FUNCTION IF EXISTS ledger_assert_balanced(uuid)')
    op.drop_column('admins', 'can_manage_money')
