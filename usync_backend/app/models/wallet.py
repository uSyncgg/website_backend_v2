import uuid

from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base

# Wallets and the double-entry ledger (docs/adr/0001-double-entry-wallet-ledger.md). The rules -
# transactions balance, the ledger is append-only, balances only move through lines, wallet
# accounts stay >= 0 unless the wallet is frozen - are Postgres triggers in migration
# c2e4a6b8d0f1, so they hold whatever code writes here. Write money only through
# app.services.ledger.post().

WALLET_ACCOUNT_TYPES = ("wallet_deposited", "wallet_earnings")
SYSTEM_ACCOUNT_TYPES = ("platform_revenue", "external")

class Wallets(Base):
    """
    SQL Alchemy model describing the wallets table: one per account that has a profile (a trigger
    on profiles makes it; a trigger here opens its two ledger accounts). account_id is nulled when
    the account is deleted, so its history stays. frozen_at is set by a chargeback that leaves it
    below zero; it can't be cleared while it's still below zero.
    """

    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, server_default = text("gen_random_uuid()"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete = "SET NULL"), nullable = True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (UniqueConstraint("account_id", name = "uq_wallets_account"),)

class LedgerAccounts(Base):
    """
    SQL Alchemy model describing the ledger_accounts table: everything that holds money. A
    wallet's 'wallet_deposited' and 'wallet_earnings', and uSync's 'platform_revenue' and
    'external' (the outside world). balance_cents is kept by the ledger_lines trigger; never set it.
    """

    __tablename__ = "ledger_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, server_default = text("gen_random_uuid()"))
    type: Mapped[str] = mapped_column(Text)
    wallet_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("wallets.id", ondelete = "RESTRICT"), nullable = True)
    balance_cents: Mapped[int] = mapped_column(BigInteger, server_default = text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("type IN ('wallet_deposited', 'wallet_earnings', 'platform_revenue', 'external')", name = "ck_ledger_accounts_type"),
        CheckConstraint("(type IN ('wallet_deposited', 'wallet_earnings')) = (wallet_id IS NOT NULL)", name = "ck_ledger_accounts_owner"),
        Index("uq_ledger_accounts_wallet_type", "wallet_id", "type", unique = True, postgresql_where = text("wallet_id IS NOT NULL")),
        Index("uq_ledger_accounts_system_type", "type", unique = True, postgresql_where = text("type IN ('platform_revenue', 'external')")),
    )

class Deposits(Base):
    """
    SQL Alchemy model describing the deposits table: the Stripe side of one deposit. The wallet is
    only credited (a 'deposit' ledger transaction) when Stripe's webhook says it succeeded; the
    unique PaymentIntent id and the ledger's (deposit_id, kind) index stop double credits.
    """

    __tablename__ = "deposits"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, server_default = text("gen_random_uuid()"))
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallets.id", ondelete = "RESTRICT"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    fee_cents: Mapped[int] = mapped_column(BigInteger)
    stripe_payment_intent_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default = text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        UniqueConstraint("stripe_payment_intent_id", name = "uq_deposits_payment_intent"),
        CheckConstraint("amount_cents >= 500", name = "ck_deposits_minimum"),
        CheckConstraint("fee_cents >= 0", name = "ck_deposits_fee"),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed', 'canceled', 'refunded', 'charged_back')", name = "ck_deposits_status"),
        Index("ix_deposits_wallet_created", "wallet_id", "created_at"),
    )

class LedgerTransactions(Base):
    """
    SQL Alchemy model describing the ledger_transactions table: one movement of money and what it
    was for. Its lines must sum to zero (checked at COMMIT). Never updated or deleted.
    created_by_account_id has no foreign key on purpose: the row can't change when that account is
    deleted.
    """

    __tablename__ = "ledger_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, server_default = text("gen_random_uuid()"))
    kind: Mapped[str] = mapped_column(Text)
    deposit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deposits.id", ondelete = "RESTRICT"), nullable = True)
    created_by_account_id: Mapped[uuid.UUID | None] = mapped_column(nullable = True)
    reason: Mapped[str | None] = mapped_column(Text, nullable = True)
    reference: Mapped[str | None] = mapped_column(Text, nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('deposit', 'deposit_refund', 'chargeback', 'chargeback_reversal', 'manual_credit', 'manual_debit')",
            name = "ck_ledger_transactions_kind",
        ),
        CheckConstraint(
            "kind NOT IN ('manual_credit', 'manual_debit') OR (reason IS NOT NULL AND reference IS NOT NULL)",
            name = "ck_ledger_transactions_manual_reason",
        ),
        Index("uq_ledger_transactions_deposit_kind", "deposit_id", "kind", unique = True, postgresql_where = text("deposit_id IS NOT NULL")),
    )

class LedgerLines(Base):
    """
    SQL Alchemy model describing the ledger_lines table: one signed amount on one ledger account
    (positive = money in). Inserting one moves that account's balance. Never updated or deleted.
    """

    __tablename__ = "ledger_lines"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always = True), primary_key = True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_transactions.id", ondelete = "RESTRICT"), index = True)
    ledger_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_accounts.id", ondelete = "RESTRICT"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (
        CheckConstraint("amount_cents <> 0", name = "ck_ledger_lines_nonzero"),
        Index("ix_ledger_lines_account", "ledger_account_id", "id"),
    )
