import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import CheckConstraint, ForeignKey, DateTime, Index, text

class Wallets(Base):
    """
    SQL Alchemy model describing the wallets table: one row per account holding its uSync balance
    in cents. Every change to balance_cents is paired with a row in wallet_transactions, and the
    row is locked (SELECT ... FOR UPDATE) while it changes so two requests can't spend the same
    dollars.
    """

    __tablename__ = "wallets"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"), primary_key = True)
    balance_cents: Mapped[int] = mapped_column(default = 0, server_default = text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("balance_cents >= 0", name = "ck_wallets_balance_not_negative"),
    )

class WalletTransactions(Base):
    """
    SQL Alchemy model describing the wallet_transactions table: the bank history.

    type is 'deposit', 'withdrawal', 'entry_fee' or 'entry_refund'. amount_cents is the signed
    change to the balance (a deposit credits the amount asked for; the 5% deposit fee is charged
    on top and kept in fee_cents; an entry fee debits the pass price plus fee). A withdrawal is
    'pending' until its payout date (available_on, net 30) and 'cancelled' if it's pulled back
    first, which returns the money to the balance. provider_ref is left for the payment provider's
    id once Stripe is linked.
    """

    __tablename__ = "wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"))
    type: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default = "completed", server_default = text("'completed'"))
    amount_cents: Mapped[int] = mapped_column()
    fee_cents: Mapped[int] = mapped_column(default = 0, server_default = text("0"))
    description: Mapped[str] = mapped_column(default = "", server_default = text("''"))
    event_public_id: Mapped[str | None] = mapped_column(nullable = True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(nullable = True, index = True)
    available_on: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    provider_ref: Mapped[str | None] = mapped_column(nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("type IN ('deposit', 'withdrawal', 'entry_fee', 'entry_refund')", name = "ck_wallet_transactions_type"),
        CheckConstraint("status IN ('completed', 'pending', 'cancelled')", name = "ck_wallet_transactions_status"),
        Index("ix_wallet_transactions_account_created", "account_id", "created_at"),
    )
