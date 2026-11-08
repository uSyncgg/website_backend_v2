import uuid

from datetime import datetime
from app.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import CheckConstraint, ForeignKey, DateTime, Index, UniqueConstraint, text

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

    type is 'deposit', 'withdrawal', 'entry_fee', 'entry_refund', 'prize' (winnings credited
    after an event closes) or 'host_payout' (a host's entry revenue minus prizes, less uSync's 2.5%,
    kept in fee_cents). related_id points a refund at the charge it reverses. amount_cents is the signed
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
    related_id: Mapped[uuid.UUID | None] = mapped_column(nullable = True)
    available_on: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)
    provider_ref: Mapped[str | None] = mapped_column(nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        CheckConstraint("type IN ('deposit', 'withdrawal', 'entry_fee', 'entry_refund', 'prize', 'host_payout')", name = "ck_wallet_transactions_type"),
        CheckConstraint("status IN ('completed', 'pending', 'cancelled')", name = "ck_wallet_transactions_status"),
        Index("ix_wallet_transactions_account_created", "account_id", "created_at"),
    )

class BracketEntryPayments(Base):
    """
    SQL Alchemy model describing the bracket_entry_payments table: what one player's spot in a
    paid event cost and who paid it. A team entry has one row per player; whoever signs the team up
    can cover teammates, otherwise each player pays their own share from their own balance.

    status is 'paid', 'refunded' (given back to the payer) or 'forfeited' (the player left after
    registration closed, so the money stays with the event). amount_cents is the pass price and
    fee_cents the 5% uSync fee charged on top. Rows outlive their entry (entry_id is set null when
    the entry is removed) so the event's revenue stays accurate.
    """

    __tablename__ = "bracket_entry_payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE"), index = True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True, index = True)
    member_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"))
    member_username: Mapped[str] = mapped_column()
    payer_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete = "CASCADE"), index = True)
    amount_cents: Mapped[int] = mapped_column()
    fee_cents: Mapped[int] = mapped_column(default = 0, server_default = text("0"))
    status: Mapped[str] = mapped_column(default = "paid", server_default = text("'paid'"))
    charge_tx_id: Mapped[uuid.UUID | None] = mapped_column(nullable = True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone = True), nullable = True)

    __table_args__ = (
        CheckConstraint("status IN ('paid', 'refunded', 'forfeited')", name = "ck_bracket_entry_payments_status"),
        Index("uq_bracket_entry_payments_paid", "entry_id", "member_profile_id", unique = True, postgresql_where = text("status = 'paid'")),
    )

class PlayerEventResults(Base):
    """
    SQL Alchemy model describing the player_event_results table: how each player did in each
    finished event, recorded when the event closes and its payouts run. This is what profile stats
    are built from (events played, wins, losses, podiums, earnings).

    A team's prize is split between its players, so a $100 first place in a 2v2 is $50 in each
    player's earnings_cents. placement is 1-3 for the podium, otherwise null. wins and losses count
    the matches the player's entry won and lost (byes and unplayed matches don't count).
    """

    __tablename__ = "player_event_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key = True, default = uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bracket_events.id", ondelete = "CASCADE"), index = True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bracket_entries.id", ondelete = "SET NULL"), nullable = True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete = "CASCADE"), index = True)
    placement: Mapped[int | None] = mapped_column(nullable = True)
    wins: Mapped[int] = mapped_column(default = 0, server_default = text("0"))
    losses: Mapped[int] = mapped_column(default = 0, server_default = text("0"))
    earnings_cents: Mapped[int] = mapped_column(default = 0, server_default = text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone = True), server_default = text("now()"))

    __table_args__ = (
        UniqueConstraint("event_id", "profile_id", name = "uq_player_event_results"),
    )
