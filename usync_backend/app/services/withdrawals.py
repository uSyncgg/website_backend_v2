import uuid

from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import UsersParent, Profiles
from app.models.wallet import Wallets, Withdrawals
from app.services import WITHDRAWAL_MIN_CENTS
from app.services import ledger
from app.services.notifications import notify
from app.services.wallet import my_wallet, _money, _alert, _audit

# Withdrawals (GLOSSARY.md): a user asks to withdraw some or all of their Earnings, never
# Deposited Funds. Asking moves the money from Earnings to the wallet's Pending Withdrawal
# account, where nothing can spend it. uSync staff send it by hand through the payout service,
# then complete it here (Pending Withdrawal -> external), or return it with a reason (back to
# Earnings).
#
# Idempotency: the wallet row is locked while a request is checked and posted, the browser sends
# one key per withdraw dialog (unique on withdrawals), a wallet has at most one pending
# withdrawal (partial unique index), and each ledger kind is posted once per withdrawal (unique
# index). Staff decisions lock the withdrawal row: repeating a decision returns the same result,
# and the opposite decision is refused.

def _now() -> datetime:
    return datetime.now(timezone.utc)

# ---- the user ------------------------------------------------------------------------------

async def request_withdrawal(user_id: str, amount_cents: int | None, withdraw_all: bool, idempotency_key: str,
                             db: AsyncSession) -> dict:
    """
    Moves `amount_cents` (or, with withdraw_all, every cent of Earnings at this moment) from
    Earnings to Pending Withdrawal. Returns the wallet.
    """

    wallet = await ledger.wallet_of(user_id, db, lock = True)

    existing = (await db.execute(select(Withdrawals).where(Withdrawals.idempotency_key == idempotency_key))).scalars().first()
    if existing is not None:
        if existing.wallet_id != wallet.id:
            raise HTTPException(status_code=409, detail="This withdrawal was already started. Close it and try again.")
        await db.rollback()
        return await my_wallet(user_id, db)

    if wallet.frozen_at is not None:
        raise HTTPException(status_code=409, detail="Your wallet is frozen after a disputed deposit, so you can't withdraw. Contact uSync support.")
    pending = (await db.execute(
        select(Withdrawals.id).where(Withdrawals.wallet_id == wallet.id).where(Withdrawals.status == "pending")
    )).first()
    if pending is not None:
        raise HTTPException(status_code=409, detail="You already have a pending withdrawal. Wait for it to finish before starting another.")

    accounts = await ledger.wallet_accounts(wallet.id, db)
    earnings = accounts[ledger.EARNINGS]
    if withdraw_all:
        amount_cents = earnings.balance_cents
        if amount_cents <= 0:
            raise HTTPException(status_code=409, detail="You have no Earnings to withdraw.")
    else:
        if amount_cents < WITHDRAWAL_MIN_CENTS:
            raise HTTPException(status_code=422, detail=f"The minimum withdrawal is {_money(WITHDRAWAL_MIN_CENTS)}. To withdraw less, withdraw all of your Earnings.")
        if amount_cents > earnings.balance_cents:
            raise HTTPException(status_code=409, detail=f"You only have {_money(max(earnings.balance_cents, 0))} in Earnings.")

    withdrawal = Withdrawals(id = uuid.uuid4(), wallet_id = wallet.id, amount_cents = amount_cents, status = "pending",
                             requested_by_account_id = uuid.UUID(user_id), idempotency_key = idempotency_key)
    db.add(withdrawal)
    await db.flush()
    await ledger.post(db, "withdrawal_request", [(earnings.id, -amount_cents), (accounts[ledger.PENDING_WITHDRAWAL].id, amount_cents)],
                      created_by = uuid.UUID(user_id), withdrawal_id = withdrawal.id)
    notify(db, [user_id], "withdrawal_requested", {"amount_cents": amount_cents})
    try:
        await db.commit()
    except IntegrityError:
        # Only reachable if the wallet lock was somehow bypassed; the unique indexes have the final say.
        await db.rollback()
        raise HTTPException(status_code=409, detail="You already have a pending withdrawal. Refresh to see it.")

    email = (await db.execute(select(UsersParent.email).where(UsersParent.id == wallet.account_id))).scalar()
    await _alert(f"uSync withdrawal requested: {_money(amount_cents)} for {email}",
                 {"withdrawal": str(withdrawal.id), "account": user_id, "email": email, "amount_cents": amount_cents})
    return await my_wallet(user_id, db)

# ---- uSync staff ---------------------------------------------------------------------------

async def _out(rows: list[Withdrawals], db: AsyncSession) -> list[dict]:
    """Withdrawals for the staff list, with who asked and what their wallet looks like now."""
    if not rows:
        return []
    wallet_ids = {w.wallet_id for w in rows}
    wallets = {w.id: w for w in (await db.execute(select(Wallets).where(Wallets.id.in_(wallet_ids)))).scalars().all()}
    account_ids = {w.account_id for w in wallets.values() if w.account_id}
    emails = dict((await db.execute(select(UsersParent.id, UsersParent.email).where(UsersParent.id.in_(account_ids)))).all())
    usernames: dict = {}
    for account_id, username in (await db.execute(
        select(Profiles.account_id, Profiles.username).where(Profiles.account_id.in_(account_ids)).order_by(Profiles.username)
    )).all():
        usernames.setdefault(account_id, []).append(username)
    out = []
    for w in rows:
        wallet = wallets[w.wallet_id]
        accounts = await ledger.wallet_accounts(wallet.id, db)
        out.append({
            "id": str(w.id),
            "accountId": str(wallet.account_id) if wallet.account_id else None,
            "email": emails.get(wallet.account_id),
            "usernames": usernames.get(wallet.account_id, []),
            "amountCents": w.amount_cents,
            "status": w.status,
            "requestedAt": w.requested_at.isoformat(),
            "decidedAt": w.decided_at.isoformat() if w.decided_at else None,
            "returnReason": w.return_reason,
            "externalReference": w.external_reference,
            "walletFrozen": wallet.frozen_at is not None,
            "earningsCents": accounts[ledger.EARNINGS].balance_cents,
        })
    return out

async def list_withdrawals(status: str, db: AsyncSession) -> dict:
    """Withdrawals with this status, oldest first (the queue order). At most 200."""
    rows = (await db.execute(
        select(Withdrawals).where(Withdrawals.status == status).order_by(Withdrawals.requested_at).limit(200)
    )).scalars().all()
    return {"items": await _out(list(rows), db)}

async def _one(withdrawal_id: uuid.UUID, db: AsyncSession) -> dict:
    withdrawal = (await db.execute(
        select(Withdrawals).where(Withdrawals.id == withdrawal_id).execution_options(populate_existing = True)
    )).scalars().one()
    return (await _out([withdrawal], db))[0]

async def _locked(withdrawal_id: uuid.UUID, db: AsyncSession) -> tuple[Withdrawals, Wallets]:
    withdrawal = (await db.execute(select(Withdrawals).where(Withdrawals.id == withdrawal_id).with_for_update())).scalars().first()
    if withdrawal is None:
        raise HTTPException(status_code=404, detail="Withdrawal not found.")
    wallet = (await db.execute(select(Wallets).where(Wallets.id == withdrawal.wallet_id).with_for_update())).scalars().one()
    return withdrawal, wallet

async def complete_withdrawal(admin: dict, withdrawal_id: uuid.UUID, external_reference: str | None, db: AsyncSession) -> dict:
    """
    Staff sent the money by hand: it leaves the wallet for good. Refused while the wallet is
    frozen (return it instead). Completing again returns the same result.
    """

    withdrawal, wallet = await _locked(withdrawal_id, db)
    if withdrawal.status == "completed":
        await db.rollback()
        return await _one(withdrawal_id, db)
    if withdrawal.status == "returned":
        raise HTTPException(status_code=409, detail="This withdrawal was already returned.")
    if wallet.frozen_at is not None:
        raise HTTPException(status_code=409, detail="This wallet is frozen after a disputed deposit. Return the withdrawal instead.")

    accounts = await ledger.wallet_accounts(wallet.id, db)
    external = await ledger.system_account("external", db)
    reference = (external_reference or "").strip() or None
    await ledger.post(db, "withdrawal_complete", [(accounts[ledger.PENDING_WITHDRAWAL].id, -withdrawal.amount_cents),
                                                  (external.id, withdrawal.amount_cents)],
                      created_by = uuid.UUID(admin["sub"]), reference = reference, withdrawal_id = withdrawal.id)
    withdrawal.status, withdrawal.decided_at = "completed", _now()
    withdrawal.decided_by_account_id, withdrawal.external_reference = uuid.UUID(admin["sub"]), reference
    _audit(db, admin, "wallet:withdrawal_complete", {"withdrawal": str(withdrawal.id), "account": str(wallet.account_id),
                                                     "amount_cents": withdrawal.amount_cents, "reference": reference})
    notify(db, [wallet.account_id], "withdrawal_completed", {"amount_cents": withdrawal.amount_cents})
    await db.commit()
    return await _one(withdrawal_id, db)

async def return_withdrawal(admin: dict, withdrawal_id: uuid.UUID, reason: str, db: AsyncSession) -> dict:
    """
    Staff didn't send it: the money goes back to Earnings, and the user sees the reason.
    Returning again returns the same result.
    """

    withdrawal, wallet = await _locked(withdrawal_id, db)
    if withdrawal.status == "returned":
        await db.rollback()
        return await _one(withdrawal_id, db)
    if withdrawal.status == "completed":
        raise HTTPException(status_code=409, detail="This withdrawal was already completed.")

    reason = reason.strip()
    accounts = await ledger.wallet_accounts(wallet.id, db)
    await ledger.post(db, "withdrawal_return", [(accounts[ledger.PENDING_WITHDRAWAL].id, -withdrawal.amount_cents),
                                                (accounts[ledger.EARNINGS].id, withdrawal.amount_cents)],
                      created_by = uuid.UUID(admin["sub"]), reason = reason, withdrawal_id = withdrawal.id)
    withdrawal.status, withdrawal.decided_at = "returned", _now()
    withdrawal.decided_by_account_id, withdrawal.return_reason = uuid.UUID(admin["sub"]), reason
    await db.flush()
    await ledger.unfreeze_if_clear(wallet, db)
    _audit(db, admin, "wallet:withdrawal_return", {"withdrawal": str(withdrawal.id), "account": str(wallet.account_id),
                                                   "amount_cents": withdrawal.amount_cents, "reason": reason})
    notify(db, [wallet.account_id], "withdrawal_returned", {"amount_cents": withdrawal.amount_cents, "reason": reason})
    await db.commit()
    return await _one(withdrawal_id, db)
