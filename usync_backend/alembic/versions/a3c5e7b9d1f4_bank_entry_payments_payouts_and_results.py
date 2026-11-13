"""Bank: per-player entry payments, prizes and payouts, event cancel/close, player results

Revision ID: a3c5e7b9d1f4
Revises: f2b8d4a6c1e3
Create Date: 2026-11-08 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a3c5e7b9d1f4'
down_revision: Union[str, Sequence[str], None] = 'f2b8d4a6c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Wallet transactions: prizes and host payouts, refunds that point at their charge, payout dates.
    op.add_column('wallet_transactions', sa.Column('related_id', sa.Uuid(), nullable=True))
    op.add_column('wallet_transactions', sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint('ck_wallet_transactions_type', 'wallet_transactions', type_='check')
    op.create_check_constraint(
        'ck_wallet_transactions_type', 'wallet_transactions',
        "type IN ('deposit', 'withdrawal', 'entry_fee', 'entry_refund', 'prize', 'host_payout')",
    )

    # Events: prize places, cancelling, closing (results recorded) and the payout that follows 45 minutes later.
    op.add_column('bracket_events', sa.Column('prizes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column('bracket_events', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bracket_events', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bracket_events', sa.Column('payout_due_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bracket_events', sa.Column('paid_out_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bracket_events', sa.Column('payout_summary', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_index('ix_bracket_events_payout_due', 'bracket_events', ['payout_due_at'], postgresql_where=sa.text('paid_out_at IS NULL AND payout_due_at IS NOT NULL'))

    # Entries: false while a teammate's share hasn't been paid (the entry stays pending).
    op.add_column('bracket_entries', sa.Column('fully_paid', sa.Boolean(), server_default=sa.text('true'), nullable=False))

    op.create_table(
        'bracket_entry_payments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('entry_id', sa.Uuid(), nullable=True),
        sa.Column('member_profile_id', sa.Uuid(), nullable=False),
        sa.Column('member_username', sa.String(), nullable=False),
        sa.Column('payer_account_id', sa.Uuid(), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('fee_cents', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('status', sa.String(), server_default=sa.text("'paid'"), nullable=False),
        sa.Column('charge_tx_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('paid', 'refunded', 'forfeited')", name='ck_bracket_entry_payments_status'),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entry_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['member_profile_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['payer_account_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_bracket_entry_payments_event_id'), 'bracket_entry_payments', ['event_id'])
    op.create_index(op.f('ix_bracket_entry_payments_entry_id'), 'bracket_entry_payments', ['entry_id'])
    op.create_index(op.f('ix_bracket_entry_payments_payer_account_id'), 'bracket_entry_payments', ['payer_account_id'])
    op.create_index(
        'uq_bracket_entry_payments_paid', 'bracket_entry_payments', ['entry_id', 'member_profile_id'],
        unique=True, postgresql_where=sa.text("status = 'paid'"),
    )

    op.create_table(
        'player_event_results',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('event_id', sa.Uuid(), nullable=False),
        sa.Column('entry_id', sa.Uuid(), nullable=True),
        sa.Column('profile_id', sa.Uuid(), nullable=False),
        sa.Column('placement', sa.Integer(), nullable=True),
        sa.Column('wins', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('losses', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('earnings_cents', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['bracket_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['entry_id'], ['bracket_entries.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'profile_id', name='uq_player_event_results'),
    )
    op.create_index(op.f('ix_player_event_results_event_id'), 'player_event_results', ['event_id'])
    op.create_index(op.f('ix_player_event_results_profile_id'), 'player_event_results', ['profile_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_player_event_results_profile_id'), table_name='player_event_results')
    op.drop_index(op.f('ix_player_event_results_event_id'), table_name='player_event_results')
    op.drop_table('player_event_results')
    op.drop_index('uq_bracket_entry_payments_paid', table_name='bracket_entry_payments')
    op.drop_index(op.f('ix_bracket_entry_payments_payer_account_id'), table_name='bracket_entry_payments')
    op.drop_index(op.f('ix_bracket_entry_payments_entry_id'), table_name='bracket_entry_payments')
    op.drop_index(op.f('ix_bracket_entry_payments_event_id'), table_name='bracket_entry_payments')
    op.drop_table('bracket_entry_payments')
    op.drop_column('bracket_entries', 'fully_paid')
    op.drop_index('ix_bracket_events_payout_due', table_name='bracket_events')
    for column in ('payout_summary', 'paid_out_at', 'payout_due_at', 'closed_at', 'cancelled_at', 'prizes'):
        op.drop_column('bracket_events', column)
    op.drop_constraint('ck_wallet_transactions_type', 'wallet_transactions', type_='check')
    op.create_check_constraint(
        'ck_wallet_transactions_type', 'wallet_transactions',
        "type IN ('deposit', 'withdrawal', 'entry_fee', 'entry_refund')",
    )
    op.drop_column('wallet_transactions', 'settled_at')
    op.drop_column('wallet_transactions', 'related_id')
