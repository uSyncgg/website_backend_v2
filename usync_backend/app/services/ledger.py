import uuid

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.wallet import Wallets, LedgerAccounts, LedgerTransactions, LedgerLines

# The one way money moves (docs/adr/0001-double-entry-wallet-ledger.md). post() writes a ledger
# transaction and its lines; Postgres then moves the balances, refuses an overdrawn wallet that
# isn't frozen, and refuses the whole COMMIT if the lines don't sum to zero. Nothing here commits:
# the caller commits the money together with whatever it belongs to (a deposit's status, ...).
#
# Wallet buckets: 'wallet_deposited' (can be spent, never withdrawn) and 'wallet_earnings'
# (prizes and host cuts; the only money that can be withdrawn).

DEPOSITED = "wallet_deposited"
EARNINGS = "wallet_earnings"

async def wallet_of(account_id, db: AsyncSession, lock: bool = False) -> Wallets:
    """
    The account's wallet. Every account with a profile has one (a trigger makes it); without a
    profile there's nothing to hold money for.
    """

    query = select(Wallets).where(Wallets.account_id == account_id)
    if lock:
        query = query.with_for_update()
    wallet = (await db.execute(query)).scalars().first()
    if wallet is None:
        raise HTTPException(status_code=404, detail="Make a profile first: your wallet comes with it.")
    return wallet

async def wallet_accounts(wallet_id, db: AsyncSession) -> dict[str, LedgerAccounts]:
    """
    { 'wallet_deposited': account, 'wallet_earnings': account } for a wallet, freshly read.
    """

    rows = (await db.execute(
        select(LedgerAccounts).where(LedgerAccounts.wallet_id == wallet_id).execution_options(populate_existing = True)
    )).scalars().all()
    return {a.type: a for a in rows}

async def system_account(type_: str, db: AsyncSession) -> LedgerAccounts:
    return (await db.execute(select(LedgerAccounts).where(LedgerAccounts.type == type_))).scalars().one()

async def post(db: AsyncSession, kind: str, lines: list[tuple[uuid.UUID, int]], *, deposit_id = None,
               created_by = None, reason: str | None = None, reference: str | None = None) -> LedgerTransactions:
    """
    Writes one ledger transaction: lines are (ledger account id, signed cents), positive = in.
    Zero amounts are dropped. The caller commits; Postgres checks the sum then.
    """

    lines = [(account_id, cents) for account_id, cents in lines if cents]
    if sum(cents for _, cents in lines) != 0:
        # Postgres would refuse it at COMMIT anyway; failing here gives a clearer stack trace.
        raise ValueError(f"Ledger transaction {kind} doesn't balance: {lines}")
    txn = LedgerTransactions(id = uuid.uuid4(), kind = kind, deposit_id = deposit_id, created_by_account_id = created_by,
                             reason = reason, reference = reference)
    db.add(txn)
    await db.flush()
    db.add_all([LedgerLines(transaction_id = txn.id, ledger_account_id = account_id, amount_cents = cents) for account_id, cents in lines])
    await db.flush()
    return txn

def take_from_buckets(accounts: dict[str, LedgerAccounts], cents: int) -> list[tuple[uuid.UUID, int]]:
    """
    Lines that take `cents` out of a wallet: Deposited Funds first, then Earnings. Whatever the
    two can't cover comes out of Deposited Funds anyway (only allowed when the wallet is frozen).
    """

    deposited, earnings = accounts[DEPOSITED], accounts[EARNINGS]
    from_deposited = min(cents, max(deposited.balance_cents, 0))
    from_earnings = min(cents - from_deposited, max(earnings.balance_cents, 0))
    short = cents - from_deposited - from_earnings
    return [(deposited.id, -(from_deposited + short)), (earnings.id, -from_earnings)]

async def unfreeze_if_clear(wallet: Wallets, db: AsyncSession) -> None:
    """
    A frozen wallet thaws once nothing in it is below zero (Postgres refuses it sooner).
    """

    if wallet.frozen_at is None:
        return
    accounts = await wallet_accounts(wallet.id, db)
    if all(a.balance_cents >= 0 for a in accounts.values()):
        await db.execute(update(Wallets).where(Wallets.id == wallet.id).values(frozen_at = None))
        wallet.frozen_at = None
