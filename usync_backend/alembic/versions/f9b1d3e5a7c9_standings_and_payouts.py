"""Stored standings and event payouts (PR 4 of the wallet; see GLOSSARY.md).

Revision ID: f9b1d3e5a7c9
Revises: e6a8c0e2f4b5
Create Date: 2026-11-22 12:00:00.000000

Written by hand.

- event_standings: every entrant's final placement once the bracket is complete, rewritten
  whenever a correction changes it (and removed if the bracket reopens). Entrants can share a
  placement. prize_cents is what the entry won, filled in at payout.
- bracket_events.payout_due_at: 24 hours after the standings last changed; the sweeper pays
  out after it (if no issue is open). paid_out_at: when it did. Only events with money are paid
  out, so paid_out_at is only possible on them.
- ledger_transactions kind 'event_payout': the whole payout as one transaction (pool and
  guarantee to the platform fee, the host and the players). The "once per event" unique index
  now covers it as well as the guarantee kinds, so an event is paid out at most once.

RLS on event_standings with no policies, like the ledger.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f9b1d3e5a7c9'
down_revision: Union[str, Sequence[str], None] = 'e6a8c0e2f4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KINDS = ("'deposit', 'deposit_refund', 'chargeback', 'chargeback_reversal', 'manual_credit', 'manual_debit', "
             "'deposit_refund_reversal', 'entry_fee', 'entry_refund', 'guarantee_fund', 'guarantee_refund'")
NEW_KINDS = OLD_KINDS + ", 'event_payout'"
OLD_EVENT_KINDS = "'entry_fee', 'entry_refund', 'guarantee_fund', 'guarantee_refund'"
NEW_EVENT_KINDS = OLD_EVENT_KINDS + ", 'event_payout'"
OLD_ONCE = "'guarantee_fund', 'guarantee_refund'"
NEW_ONCE = OLD_ONCE + ", 'event_payout'"


def _kinds(kinds: str, event_kinds: str, once: str, old_index: str, new_index: str) -> None:
    op.drop_constraint('ck_ledger_transactions_kind', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_kind', 'ledger_transactions', f"kind IN ({kinds})")
    op.drop_constraint('ck_ledger_transactions_event', 'ledger_transactions', type_ = 'check')
    op.create_check_constraint('ck_ledger_transactions_event', 'ledger_transactions',
                               f"kind NOT IN ({event_kinds}) OR event_id IS NOT NULL")
    op.drop_index(old_index, table_name = 'ledger_transactions')
    op.create_index(new_index, 'ledger_transactions', ['event_id', 'kind'], unique = True,
                    postgresql_where = sa.text(f"kind IN ({once})"))


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'event_standings',
        sa.Column('event_id', sa.Uuid(), sa.ForeignKey('bracket_events.id', ondelete = 'CASCADE', deferrable = True, initially = 'DEFERRED'),
                  primary_key = True),
        sa.Column('entry_id', sa.Uuid(), sa.ForeignKey('bracket_entries.id', ondelete = 'CASCADE', deferrable = True, initially = 'DEFERRED'),
                  primary_key = True),
        sa.Column('placement', sa.SmallInteger(), nullable = False),
        sa.Column('prize_cents', sa.BigInteger(), nullable = True),
        sa.Column('recorded_at', sa.DateTime(timezone = True), nullable = False, server_default = sa.text('now()')),
        sa.CheckConstraint('placement >= 1', name = 'ck_event_standings_placement'),
        sa.CheckConstraint('prize_cents IS NULL OR prize_cents >= 0', name = 'ck_event_standings_prize'),
    )
    op.create_index('ix_event_standings_event_placement', 'event_standings', ['event_id', 'placement'])
    op.create_index('ix_event_standings_entry', 'event_standings', ['entry_id'])

    op.add_column('bracket_events', sa.Column('payout_due_at', sa.DateTime(timezone = True), nullable = True))
    op.add_column('bracket_events', sa.Column('paid_out_at', sa.DateTime(timezone = True), nullable = True))
    op.create_check_constraint('ck_bracket_events_paid_out', 'bracket_events', 'paid_out_at IS NULL OR prize_funding IS NOT NULL')
    # The sweeper's question: which events are due a payout now.
    op.create_index('ix_bracket_events_payout_due', 'bracket_events', ['payout_due_at'],
                    postgresql_where = sa.text('payout_due_at IS NOT NULL AND paid_out_at IS NULL'))

    _kinds(NEW_KINDS, NEW_EVENT_KINDS, NEW_ONCE, 'uq_ledger_transactions_event_guarantee', 'uq_ledger_transactions_event_once')

    op.execute('ALTER TABLE public.event_standings ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    """Downgrade schema. Fails (on purpose) once any event has been paid out."""
    _kinds(OLD_KINDS, OLD_EVENT_KINDS, OLD_ONCE, 'uq_ledger_transactions_event_once', 'uq_ledger_transactions_event_guarantee')
    op.drop_index('ix_bracket_events_payout_due', table_name = 'bracket_events')
    op.drop_constraint('ck_bracket_events_paid_out', 'bracket_events', type_ = 'check')
    op.drop_column('bracket_events', 'paid_out_at')
    op.drop_column('bracket_events', 'payout_due_at')
    op.drop_table('event_standings')
