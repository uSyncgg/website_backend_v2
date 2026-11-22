"""Entry fees, guaranteed prizes and cancelled events (PR 3 of the wallet; see GLOSSARY.md).

Revision ID: e6a8c0e2f4b5
Revises: d4f6b8c0e2a3
Create Date: 2026-11-21 22:00:00.000000

Written by hand.

bracket_events gets its money settings
- entry_fee_cents: what one player pays (null = free). prize_funding: 'guaranteed' (the host pays
  guarantee_cents up front and keeps the event pool after the platform fee) or 'entry_funded'
  (the prize pool is the event pool after the platform fee and host_cut_bps). payout_structure:
  the prize pool's split by placement, in basis points (1st, 2nd, ...), checked by
  payout_structure_valid(). platform_fee_bps: uSync's fee, copied onto the event when it's made
  so a later change doesn't touch running events. A CHECK keeps the combination consistent.
- cancelled_at / cancel_reason: a Cancelled Event (kept, never deleted, once money moved).
- A wallet entry fee and LAN passes (the payments JSON) can't both be on.
- host_profile_id becomes nullable, ON DELETE SET NULL (was CASCADE): an event with money
  outlives its host's profile. The API deletes free events itself and refuses while unfinished.

Money that belongs to an event
- ledger_accounts gains event_id and two types: 'event_pool' (the entry fees) and
  'event_guarantee' (a guaranteed prize). One of each per event, opened the first time money
  moves. event_id is RESTRICT: an event that ever held money can't be deleted.
- entry_payments: one entry fee payment. covers = 'team' (Pay for Team: one payment of fee x team
  size from the captain) or 'player' (Each Pays Own: one per picked player). status 'paid' ->
  'refunded'. Two partial unique indexes stop double charges: one paid team payment per entry, and
  one paid payment per player per event.
- ledger_transactions gains entry_payment_id ((entry_payment_id, kind) unique: a payment is taken
  once and refunded once) and event_id ((event_id, kind) unique for the guarantee kinds: funded
  once, refunded once). New kinds: entry_fee, entry_refund, guarantee_fund, guarantee_refund.
- bracket_entries.pay_mode: 'team' or 'each' (null for free events).
- wallets.auto_pay_until: Auto-Pay is on until then (null = off).

Trigger changes
- Event accounts can never go below zero (wallets still can, only while frozen).
- A ledger account's event never changes, like its type and wallet.

RLS on entry_payments with no policies, like the rest of the ledger.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e6a8c0e2f4b5'
down_revision: Union[str, Sequence[str], None] = 'd4f6b8c0e2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_ACCOUNT_TYPES = "'wallet_deposited', 'wallet_earnings', 'platform_revenue', 'external'"
NEW_ACCOUNT_TYPES = OLD_ACCOUNT_TYPES + ", 'event_pool', 'event_guarantee'"
OLD_KINDS = ("'deposit', 'deposit_refund', 'chargeback', 'chargeback_reversal', 'manual_credit', 'manual_debit', "
             "'deposit_refund_reversal'")
NEW_KINDS = OLD_KINDS + ", 'entry_fee', 'entry_refund', 'guarantee_fund', 'guarantee_refund'"

# The post-line trigger from c2e4a6b8d0f1, plus: event accounts never go below zero.
POST_LINE_OLD = """
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
        IF acct.balance_cents < 0 AND acct.wallet_id IS NOT NULL THEN
            SELECT frozen_at INTO frozen FROM wallets WHERE id = acct.wallet_id;
            IF frozen IS NULL THEN
                RAISE EXCEPTION 'Insufficient funds in ledger account %', acct.id USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NULL;
    END $$;
"""
POST_LINE_NEW = """
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
        END IF;
        IF acct.balance_cents < 0 AND acct.wallet_id IS NOT NULL THEN
            SELECT frozen_at INTO frozen FROM wallets WHERE id = acct.wallet_id;
            IF frozen IS NULL THEN
                RAISE EXCEPTION 'Insufficient funds in ledger account %', acct.id USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NULL;
    END $$;
"""

def _accounts_guard(with_event: bool) -> str:
    owner = "NEW.wallet_id IS DISTINCT FROM OLD.wallet_id" + (" OR NEW.event_id IS DISTINCT FROM OLD.event_id" if with_event else "")
    return f"""
        CREATE OR REPLACE FUNCTION ledger_accounts_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Ledger accounts are never deleted';
            END IF;
            IF NEW.type <> OLD.type OR {owner} THEN
                RAISE EXCEPTION 'A ledger account''s type and owner never change';
            END IF;
            IF NEW.balance_cents <> OLD.balance_cents AND current_setting('usync.ledger_posting', true) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'Balances only change by posting ledger lines';
            END IF;
            RETURN NEW;
        END $$;
    """


def upgrade() -> None:
    """Upgrade schema."""

    # ---- bracket_events: money settings ----------------------------------------------------

    # 1 to 16 whole shares of at least 1% (100 bps), never more than the place above, adding to 100%.
    op.execute("""
        CREATE FUNCTION payout_structure_valid(p jsonb) RETURNS boolean
        LANGUAGE plpgsql IMMUTABLE SET search_path = public AS $$
        DECLARE
            v jsonb;
            share numeric;
            prev numeric := 10000;
            total numeric := 0;
        BEGIN
            IF p IS NULL THEN
                RETURN true;
            END IF;
            IF jsonb_typeof(p) <> 'array' OR jsonb_array_length(p) NOT BETWEEN 1 AND 16 THEN
                RETURN false;
            END IF;
            FOR v IN SELECT value FROM jsonb_array_elements(p) WITH ORDINALITY AS e(value, i) ORDER BY i LOOP
                IF jsonb_typeof(v) <> 'number' THEN
                    RETURN false;
                END IF;
                share := v::text::numeric;
                IF share <> trunc(share) OR share < 100 OR share > prev THEN
                    RETURN false;
                END IF;
                prev := share;
                total := total + share;
            END LOOP;
            RETURN total = 10000;
        END $$;
    """)

    op.add_column('bracket_events', sa.Column('entry_fee_cents', sa.Integer(), nullable = True))
    op.add_column('bracket_events', sa.Column('prize_funding', sa.Text(), nullable = True))
    op.add_column('bracket_events', sa.Column('guarantee_cents', sa.Integer(), nullable = True))
    op.add_column('bracket_events', sa.Column('host_cut_bps', sa.SmallInteger(), nullable = True))
    op.add_column('bracket_events', sa.Column('payout_structure', postgresql.JSONB(), nullable = True))
    op.add_column('bracket_events', sa.Column('platform_fee_bps', sa.SmallInteger(), nullable = False, server_default = sa.text('500')))
    op.add_column('bracket_events', sa.Column('cancelled_at', sa.DateTime(timezone = True), nullable = True))
    op.add_column('bracket_events', sa.Column('cancel_reason', sa.Text(), nullable = True))

    op.create_check_constraint('ck_bracket_events_entry_fee', 'bracket_events',
                               'entry_fee_cents IS NULL OR entry_fee_cents BETWEEN 100 AND 50000')
    op.create_check_constraint('ck_bracket_events_guarantee', 'bracket_events',
                               'guarantee_cents IS NULL OR (guarantee_cents > 0 AND guarantee_cents % 100 = 0)')
    op.create_check_constraint('ck_bracket_events_host_cut', 'bracket_events',
                               'host_cut_bps IS NULL OR host_cut_bps BETWEEN 0 AND 5000')
    op.create_check_constraint('ck_bracket_events_platform_fee', 'bracket_events',
                               'platform_fee_bps BETWEEN 0 AND 10000')
    op.create_check_constraint('ck_bracket_events_payout_structure', 'bracket_events',
                               'payout_structure_valid(payout_structure)')
    # Free (nothing set), entry-funded (fee + host cut + payout structure) or guaranteed (guarantee
    # + payout structure, the entry fee optional, no host cut: the host keeps the whole pool).
    op.create_check_constraint('ck_bracket_events_money', 'bracket_events', """
        (prize_funding IS NULL AND entry_fee_cents IS NULL AND guarantee_cents IS NULL
            AND host_cut_bps IS NULL AND payout_structure IS NULL)
        OR (prize_funding = 'entry_funded' AND entry_fee_cents IS NOT NULL AND guarantee_cents IS NULL
            AND host_cut_bps IS NOT NULL AND payout_structure IS NOT NULL)
        OR (prize_funding = 'guaranteed' AND guarantee_cents IS NOT NULL
            AND host_cut_bps IS NULL AND payout_structure IS NOT NULL)
    """)
    op.create_check_constraint('ck_bracket_events_no_passes_with_fee', 'bracket_events',
                               "entry_fee_cents IS NULL OR payments->'enabled' IS DISTINCT FROM 'true'::jsonb")
    op.create_check_constraint('ck_bracket_events_cancelled', 'bracket_events', """
        (cancelled_at IS NULL) = (cancel_reason IS NULL)
        AND (cancel_reason IS NULL OR cancel_reason IN ('host', 'min_entrants', 'admin'))
    """)

    op.alter_column('bracket_events', 'host_profile_id', nullable = True)
    op.drop_constraint('bracket_events_host_profile_id_fkey', 'bracket_events', type_ = 'foreignkey')
    op.create_foreign_key('bracket_events_host_profile_id_fkey', 'bracket_events', 'profiles',
                          ['host_profile_id'], ['id'], ondelete = 'SET NULL')

    # ---- entries and wallets ----------------------------------------------------------------

    op.add_column('bracket_entries', sa.Column('pay_mode', sa.Text(), nullable = True))
    op.create_check_constraint('ck_bracket_entries_pay_mode', 'bracket_entries',
                               "pay_mode IS NULL OR pay_mode IN ('team', 'each')")
    op.add_column('wallets', sa.Column('auto_pay_until', sa.DateTime(timezone = True), nullable = True))

    # ---- ledger accounts that belong to an event --------------------------------------------

    op.add_column('ledger_accounts', sa.Column('event_id', sa.Uuid(), nullable = True))
    op.create_foreign_key('ledger_accounts_event_id_fkey', 'ledger_accounts', 'bracket_events',
                          ['event_id'], ['id'], ondelete = 'RESTRICT')
    op.drop_constraint('ck_ledger_accounts_type', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_type', 'ledger_accounts', f"type IN ({NEW_ACCOUNT_TYPES})")
    op.drop_constraint('ck_ledger_accounts_owner', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_owner', 'ledger_accounts', """
        (type IN ('wallet_deposited', 'wallet_earnings')) = (wallet_id IS NOT NULL)
        AND (type IN ('event_pool', 'event_guarantee')) = (event_id IS NOT NULL)
    """)
    op.create_index('uq_ledger_accounts_event_type', 'ledger_accounts', ['event_id', 'type'], unique = True,
                    postgresql_where = sa.text('event_id IS NOT NULL'))

    # ---- entry_payments -------------------------------------------------------------------------

    op.create_table(
        'entry_payments',
        sa.Column('id', sa.Uuid(), primary_key = True, server_default = sa.text('gen_random_uuid()')),
        sa.Column('event_id', sa.Uuid(), sa.ForeignKey('bracket_events.id', ondelete = 'RESTRICT'), nullable = False),
        sa.Column('entry_id', sa.Uuid(), sa.ForeignKey('bracket_entries.id', ondelete = 'SET NULL'), nullable = True),
        sa.Column('profile_id', sa.Uuid(), sa.ForeignKey('profiles.id', ondelete = 'SET NULL'), nullable = True),
        sa.Column('payer_wallet_id', sa.Uuid(), sa.ForeignKey('wallets.id', ondelete = 'RESTRICT'), nullable = False),
        sa.Column('covers', sa.Text(), nullable = False),
        sa.Column('amount_cents', sa.BigInteger(), nullable = False),
        sa.Column('status', sa.Text(), nullable = False, server_default = sa.text("'paid'")),
        sa.Column('created_at', sa.DateTime(timezone = True), nullable = False, server_default = sa.text('now()')),
        sa.Column('refunded_at', sa.DateTime(timezone = True), nullable = True),
        sa.CheckConstraint("covers IN ('team', 'player')", name = 'ck_entry_payments_covers'),
        sa.CheckConstraint('amount_cents > 0', name = 'ck_entry_payments_amount'),
        sa.CheckConstraint("status IN ('paid', 'refunded')", name = 'ck_entry_payments_status'),
        sa.CheckConstraint("(status = 'refunded') = (refunded_at IS NOT NULL)", name = 'ck_entry_payments_refunded_at'),
    )
    op.create_index('uq_entry_payments_team_paid', 'entry_payments', ['entry_id'], unique = True,
                    postgresql_where = sa.text("status = 'paid' AND covers = 'team'"))
    op.create_index('uq_entry_payments_player_paid', 'entry_payments', ['event_id', 'profile_id'], unique = True,
                    postgresql_where = sa.text("status = 'paid' AND covers = 'player'"))
    op.create_index('ix_entry_payments_entry', 'entry_payments', ['entry_id'])
    op.create_index('ix_entry_payments_event_status', 'entry_payments', ['event_id', 'status'])
    op.create_index('ix_entry_payments_payer', 'entry_payments', ['payer_wallet_id'])

    # ---- ledger transactions: what event / payment they belong to -----------------------------

    op.add_column('ledger_transactions', sa.Column('entry_payment_id', sa.Uuid(), nullable = True))
    op.create_foreign_key('ledger_transactions_entry_payment_id_fkey', 'ledger_transactions', 'entry_payments',
                          ['entry_payment_id'], ['id'], ondelete = 'RESTRICT')
    op.add_column('ledger_transactions', sa.Column('event_id', sa.Uuid(), nullable = True))
    op.create_foreign_key('ledger_transactions_event_id_fkey', 'ledger_transactions', 'bracket_events',
                          ['event_id'], ['id'], ondelete = 'RESTRICT')
    op.drop_constraint('ck_ledger_transactions_kind', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_kind', 'ledger_transactions', f"kind IN ({NEW_KINDS})")
    op.create_check_constraint('ck_ledger_transactions_entry_payment', 'ledger_transactions',
                               "(kind IN ('entry_fee', 'entry_refund')) = (entry_payment_id IS NOT NULL)")
    op.create_check_constraint('ck_ledger_transactions_event', 'ledger_transactions',
                               "kind NOT IN ('entry_fee', 'entry_refund', 'guarantee_fund', 'guarantee_refund') OR event_id IS NOT NULL")
    op.create_index('uq_ledger_transactions_entry_payment_kind', 'ledger_transactions', ['entry_payment_id', 'kind'], unique = True,
                    postgresql_where = sa.text('entry_payment_id IS NOT NULL'))
    op.create_index('uq_ledger_transactions_event_guarantee', 'ledger_transactions', ['event_id', 'kind'], unique = True,
                    postgresql_where = sa.text("kind IN ('guarantee_fund', 'guarantee_refund')"))
    op.create_index('ix_ledger_transactions_event', 'ledger_transactions', ['event_id'],
                    postgresql_where = sa.text('event_id IS NOT NULL'))

    # ---- triggers ------------------------------------------------------------------------------

    op.execute(POST_LINE_NEW)
    op.execute(_accounts_guard(with_event = True))

    op.execute('ALTER TABLE public.entry_payments ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema. Fails (on purpose) once any event money, entry payment or money event exists."""
    op.execute(_accounts_guard(with_event = False))
    op.execute(POST_LINE_OLD)

    op.drop_index('ix_ledger_transactions_event', table_name = 'ledger_transactions')
    op.drop_index('uq_ledger_transactions_event_guarantee', table_name = 'ledger_transactions')
    op.drop_index('uq_ledger_transactions_entry_payment_kind', table_name = 'ledger_transactions')
    op.drop_constraint('ck_ledger_transactions_event', 'ledger_transactions', type_ = 'check')
    op.drop_constraint('ck_ledger_transactions_entry_payment', 'ledger_transactions', type_ = 'check')
    op.drop_constraint('ck_ledger_transactions_kind', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_kind', 'ledger_transactions', f"kind IN ({OLD_KINDS})")
    op.drop_constraint('ledger_transactions_event_id_fkey', 'ledger_transactions', type_ = 'foreignkey')
    op.drop_column('ledger_transactions', 'event_id')
    op.drop_constraint('ledger_transactions_entry_payment_id_fkey', 'ledger_transactions', type_ = 'foreignkey')
    op.drop_column('ledger_transactions', 'entry_payment_id')

    op.drop_table('entry_payments')

    op.drop_index('uq_ledger_accounts_event_type', table_name = 'ledger_accounts')
    op.drop_constraint('ck_ledger_accounts_owner', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_owner', 'ledger_accounts',
                               "(type IN ('wallet_deposited', 'wallet_earnings')) = (wallet_id IS NOT NULL)")
    op.drop_constraint('ck_ledger_accounts_type', 'ledger_accounts', type_ = 'check')
    op.create_check_constraint('ck_ledger_accounts_type', 'ledger_accounts', f"type IN ({OLD_ACCOUNT_TYPES})")
    op.drop_constraint('ledger_accounts_event_id_fkey', 'ledger_accounts', type_ = 'foreignkey')
    op.drop_column('ledger_accounts', 'event_id')

    op.drop_column('wallets', 'auto_pay_until')
    op.drop_constraint('ck_bracket_entries_pay_mode', 'bracket_entries', type_ = 'check')
    op.drop_column('bracket_entries', 'pay_mode')

    op.drop_constraint('bracket_events_host_profile_id_fkey', 'bracket_events', type_ = 'foreignkey')
    op.create_foreign_key('bracket_events_host_profile_id_fkey', 'bracket_events', 'profiles',
                          ['host_profile_id'], ['id'], ondelete = 'CASCADE')
    op.alter_column('bracket_events', 'host_profile_id', nullable = False)

    for name in ('ck_bracket_events_cancelled', 'ck_bracket_events_no_passes_with_fee', 'ck_bracket_events_money',
                 'ck_bracket_events_payout_structure', 'ck_bracket_events_platform_fee', 'ck_bracket_events_host_cut',
                 'ck_bracket_events_guarantee', 'ck_bracket_events_entry_fee'):
        op.drop_constraint(name, 'bracket_events', type_ = 'check')
    for column in ('cancel_reason', 'cancelled_at', 'platform_fee_bps', 'payout_structure', 'host_cut_bps',
                   'guarantee_cents', 'prize_funding', 'entry_fee_cents'):
        op.drop_column('bracket_events', column)
    op.execute('DROP FUNCTION IF EXISTS payout_structure_valid(jsonb)')
