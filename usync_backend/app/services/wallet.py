import html
import logging
import uuid

import resend
import stripe
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from app.core.db import AsyncSessionLocal
from app.models.users import UsersParent
from app.models.admin import AdminAuditLog
from app.models.wallet import Wallets, LedgerAccounts, LedgerTransactions, LedgerLines, Deposits
from app.services import (
    STRIPE_CURRENCY, STRIPE_PAYMENT_METHOD_TYPES, DEPOSIT_MIN_CENTS, DEPOSIT_FEE_PERCENT, DEPOSIT_DAILY_LIMIT_CENTS,
    DEPOSIT_HOLD_MINUTES, MONEY_ALERT_EMAIL,
)
from app.services import ledger
from app.services.email import FROM_EMAIL
from app.services.notifications import notify

logger = logging.getLogger(__name__)

# Wallets: what a user sees, deposits through Stripe, chargebacks, and uSync staff's money tools.
#
# A deposit: POST /wallet/deposits makes a 'pending' deposits row and a PaymentIntent for the
# amount plus the 5% fee. The browser pays it with the Payment Element. Only Stripe's webhook
# (payment_intent.succeeded) credits the wallet: external -total, Deposited Funds +amount,
# platform revenue +fee, in one ledger transaction. Unpaid deposits are canceled after 30 minutes.
#
# A chargeback (charge.dispute.funds_withdrawn) takes the deposit back out - Deposited Funds
# first, then Earnings - and freezes the wallet if that leaves it below zero. Winning the dispute
# (charge.dispute.funds_reinstated) puts it back and thaws the wallet once it's clear.

def _now() -> datetime:
    return datetime.now(timezone.utc)

def deposit_fee(amount_cents: int) -> int:
    """5% of the deposit, rounded half up to the cent ($12.34 -> 62c)."""
    return (amount_cents * DEPOSIT_FEE_PERCENT + 50) // 100

def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"

# ---- reading ---------------------------------------------------------------------------

async def _wallet_out(wallet: Wallets, db: AsyncSession) -> dict:
    accounts = await ledger.wallet_accounts(wallet.id, db)
    deposited = accounts[ledger.DEPOSITED].balance_cents
    earnings = accounts[ledger.EARNINGS].balance_cents
    return {
        "depositedCents": deposited,
        "earningsCents": earnings,
        "totalCents": deposited + earnings,
        "frozen": wallet.frozen_at is not None,
        "frozenAt": wallet.frozen_at.isoformat() if wallet.frozen_at else None,
    }

async def my_wallet(user_id: str, db: AsyncSession) -> dict:
    return await _wallet_out(await ledger.wallet_of(user_id, db), db)

async def history(user_id: str, db: AsyncSession, before: int | None = None, limit: int = 30) -> dict:
    """
    The wallet's ledger lines, newest first: one row per bucket a transaction touched. `before`
    is the last line id of the previous page.
    """

    wallet = await ledger.wallet_of(user_id, db)
    query = (
        select(LedgerLines, LedgerTransactions, LedgerAccounts.type)
        .join(LedgerTransactions, LedgerTransactions.id == LedgerLines.transaction_id)
        .join(LedgerAccounts, LedgerAccounts.id == LedgerLines.ledger_account_id)
        .where(LedgerAccounts.wallet_id == wallet.id)
        .order_by(LedgerLines.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(LedgerLines.id < before)
    rows = (await db.execute(query)).all()
    items = [
        {
            "id": line.id,
            "kind": txn.kind,
            "bucket": "deposited" if type_ == ledger.DEPOSITED else "earnings",
            "amountCents": line.amount_cents,
            "createdAt": txn.created_at.isoformat(),
            # Staff write the reason for manual changes; the reference (a wire number) stays internal.
            "reason": txn.reason,
        }
        for line, txn, type_ in rows[:limit]
    ]
    return {"items": items, "nextBefore": items[-1]["id"] if len(rows) > limit else None}

# ---- deposits ----------------------------------------------------------------------------

async def start_deposit(user_id: str, amount_cents: int, db: AsyncSession, stripeClient: StripeClient) -> dict:
    """
    Makes a pending deposit and its PaymentIntent (amount + fee). The wallet row is locked so two
    deposits started at once can't both slip under the daily limit.
    """

    if amount_cents < DEPOSIT_MIN_CENTS:
        raise HTTPException(status_code=422, detail=f"The minimum deposit is {_money(DEPOSIT_MIN_CENTS)}.")
    wallet = await ledger.wallet_of(user_id, db, lock = True)
    since = _now() - timedelta(hours = 24)
    recent = (await db.execute(
        select(func.coalesce(func.sum(Deposits.amount_cents), 0))
        .where(Deposits.wallet_id == wallet.id).where(Deposits.created_at >= since)
        .where(Deposits.status.in_(("pending", "succeeded")))
    )).scalar()
    room = DEPOSIT_DAILY_LIMIT_CENTS - recent
    if amount_cents > room:
        raise HTTPException(status_code=409, detail=(
            f"You can deposit up to {_money(DEPOSIT_DAILY_LIMIT_CENTS)} in 24 hours. "
            + (f"You can add {_money(room)} more right now." if room >= DEPOSIT_MIN_CENTS else "Try again tomorrow.")
        ))

    fee = deposit_fee(amount_cents)
    deposit_id = uuid.uuid4()
    intent = await stripeClient.v1.payment_intents.create_async(
        {
            "amount": amount_cents + fee,
            "currency": STRIPE_CURRENCY,
            "payment_method_types": STRIPE_PAYMENT_METHOD_TYPES,
            "description": f"uSync wallet deposit {_money(amount_cents)} + {_money(fee)} fee",
            "metadata": {"kind": "wallet_deposit", "deposit_id": str(deposit_id), "wallet_id": str(wallet.id)},
        },
        {"idempotency_key": f"wallet-deposit-{deposit_id}"},
    )
    db.add(Deposits(id = deposit_id, wallet_id = wallet.id, amount_cents = amount_cents, fee_cents = fee,
                    stripe_payment_intent_id = intent.id, status = "pending"))
    await db.commit()
    return {
        "depositId": str(deposit_id),
        "clientSecret": intent.client_secret,
        "amountCents": amount_cents,
        "feeCents": fee,
        "totalCents": amount_cents + fee,
    }

async def _deposit_lines(deposit: Deposits, accounts: dict, sign: int, db: AsyncSession) -> list:
    """external -total, Deposited Funds +amount, revenue +fee (or all reversed with sign = -1)."""
    external = await ledger.system_account("external", db)
    revenue = await ledger.system_account("platform_revenue", db)
    return [
        (external.id, -sign * (deposit.amount_cents + deposit.fee_cents)),
        (accounts[ledger.DEPOSITED].id, sign * deposit.amount_cents),
        (revenue.id, sign * deposit.fee_cents),
    ]

async def deposit_for_payment_intent(payment_intent_id: str, db: AsyncSession, lock: bool = False) -> Deposits | None:
    query = select(Deposits).where(Deposits.stripe_payment_intent_id == payment_intent_id)
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalars().first()

async def _has(deposit: Deposits, kind: str, db: AsyncSession) -> bool:
    return (await db.execute(
        select(LedgerTransactions.id).where(LedgerTransactions.deposit_id == deposit.id).where(LedgerTransactions.kind == kind)
    )).first() is not None

async def handle_deposit_event(event_type: str, deposit: Deposits, db: AsyncSession) -> None:
    """
    Stripe webhook events for a wallet deposit. Each is safe to receive twice: the ledger's
    (deposit_id, kind) index allows one of each kind, and we check before posting.
    """

    wallet = (await db.execute(select(Wallets).where(Wallets.id == deposit.wallet_id).with_for_update())).scalars().one()

    if event_type == "payment_intent.succeeded":
        if await _has(deposit, "deposit", db):
            return
        accounts = await ledger.wallet_accounts(wallet.id, db)
        await ledger.post(db, "deposit", await _deposit_lines(deposit, accounts, 1, db), deposit_id = deposit.id)
        deposit.status, deposit.completed_at = "succeeded", _now()
        await db.flush()
        await ledger.unfreeze_if_clear(wallet, db)
        notify(db, [wallet.account_id], "wallet_deposit", {"amount_cents": deposit.amount_cents})
    elif event_type == "payment_intent.payment_failed":
        # Not final: Stripe lets the buyer retry on the same PaymentIntent.
        if deposit.status == "pending":
            deposit.status = "failed"
    elif event_type == "payment_intent.canceled":
        if deposit.status in ("pending", "failed"):
            deposit.status = "canceled"
    elif event_type == "charge.dispute.funds_withdrawn":
        if not await _has(deposit, "deposit", db) or await _has(deposit, "chargeback", db):
            return
        accounts = await ledger.wallet_accounts(wallet.id, db)
        lines = ledger.take_from_buckets(accounts, deposit.amount_cents)
        if accounts[ledger.DEPOSITED].balance_cents + accounts[ledger.EARNINGS].balance_cents < deposit.amount_cents:
            # Frozen before posting, so Postgres lets it go below zero.
            await db.execute(update(Wallets).where(Wallets.id == wallet.id).values(frozen_at = _now()))
            wallet.frozen_at = _now()
            notify(db, [wallet.account_id], "wallet_frozen", {"amount_cents": deposit.amount_cents})
        external = await ledger.system_account("external", db)
        revenue = await ledger.system_account("platform_revenue", db)
        await ledger.post(db, "chargeback", [*lines, (revenue.id, -deposit.fee_cents),
                                             (external.id, deposit.amount_cents + deposit.fee_cents)], deposit_id = deposit.id)
        deposit.status = "charged_back"
        logger.warning("Chargeback on deposit %s (wallet %s, %s)", deposit.id, wallet.id, _money(deposit.amount_cents))
    elif event_type == "charge.dispute.funds_reinstated":
        if not await _has(deposit, "chargeback", db) or await _has(deposit, "chargeback_reversal", db):
            return
        accounts = await ledger.wallet_accounts(wallet.id, db)
        await ledger.post(db, "chargeback_reversal", await _deposit_lines(deposit, accounts, 1, db), deposit_id = deposit.id)
        deposit.status = "succeeded"
        await db.flush()
        await ledger.unfreeze_if_clear(wallet, db)
    await db.commit()

async def expire_stale_deposits(stripeClient: StripeClient) -> int:
    """
    Cancels deposits nobody paid within DEPOSIT_HOLD_MINUTES, so they stop counting towards the
    daily limit. If Stripe won't cancel (the buyer is mid-payment), it's left for the webhook.
    """

    from app.services.registration_holds import cancel_payment_intent

    async with AsyncSessionLocal() as db:
        cutoff = _now() - timedelta(minutes = DEPOSIT_HOLD_MINUTES)
        stale = (await db.execute(
            select(Deposits.id, Deposits.stripe_payment_intent_id)
            .where(Deposits.status.in_(("pending", "failed"))).where(Deposits.created_at < cutoff)
            .order_by(Deposits.created_at).limit(50)
        )).all()
        await db.rollback()
        canceled = 0
        for deposit_id, payment_intent_id in stale:
            try:
                if not await cancel_payment_intent(payment_intent_id, stripeClient):
                    continue
                result = await db.execute(
                    update(Deposits).where(Deposits.id == deposit_id).where(Deposits.status.in_(("pending", "failed")))
                    .values(status = "canceled")
                )
                await db.commit()
                canceled += result.rowcount or 0
            except Exception:
                await db.rollback()
                logger.exception("Failed to cancel stale deposit %s", deposit_id)
        return canceled

# ---- account deletion --------------------------------------------------------------------

async def check_can_delete_account(user_id: str, db: AsyncSession) -> None:
    """
    Accounts with money in their wallet, or a frozen wallet, can't be deleted (the wallet itself
    stays behind, unowned, so the ledger still adds up).
    """

    wallet = (await db.execute(select(Wallets).where(Wallets.account_id == user_id))).scalars().first()
    if wallet is None:
        return
    if wallet.frozen_at is not None:
        raise HTTPException(status_code=409, detail="Your wallet is frozen after a disputed deposit. Contact uSync support before deleting your account.")
    accounts = await ledger.wallet_accounts(wallet.id, db)
    if any(a.balance_cents != 0 for a in accounts.values()):
        raise HTTPException(status_code=409, detail=(
            "Your wallet still has money in it. Spend or withdraw it (or contact uSync support) before deleting your account."
        ))

# ---- uSync staff: money tools --------------------------------------------------------------

async def _account_by_email(email: str, db: AsyncSession) -> UsersParent:
    user = (await db.execute(select(UsersParent).where(func.lower(UsersParent.email) == email.strip().lower()))).scalars().first()
    if user is None:
        raise HTTPException(status_code=404, detail="No account with that email.")
    return user

async def admin_wallet(email: str, db: AsyncSession) -> dict:
    """
    A wallet for the staff tools: balances, plus the last 20 deposits (to pick one to refund).
    """

    user = await _account_by_email(email, db)
    wallet = await ledger.wallet_of(user.id, db)
    deposits = (await db.execute(
        select(Deposits).where(Deposits.wallet_id == wallet.id).order_by(Deposits.created_at.desc()).limit(20)
    )).scalars().all()
    return {
        "accountId": str(user.id),
        "email": user.email,
        **(await _wallet_out(wallet, db)),
        "deposits": [
            {
                "id": str(d.id), "amountCents": d.amount_cents, "feeCents": d.fee_cents, "status": d.status,
                "createdAt": d.created_at.isoformat(), "paymentIntent": d.stripe_payment_intent_id,
            }
            for d in deposits
        ],
    }

async def _alert(subject: str, rows: dict) -> None:
    """Emails uSync about a money action. A failed email never undoes the action."""
    body = "".join(f"<p><strong>{html.escape(k)}:</strong> {html.escape(str(v))}</p>" for k, v in rows.items())
    try:
        await resend.Emails.send_async({"from": FROM_EMAIL, "to": [MONEY_ALERT_EMAIL], "subject": subject, "html": body})
    except Exception:
        logger.exception("Failed to send money alert: %s", subject)

def _audit(db: AsyncSession, admin: dict, action: str, details: dict) -> None:
    db.add(AdminAuditLog(admin_id = uuid.UUID(admin["sub"]), admin_email = admin.get("email") or "", action = action, details = details))

async def manual_move(admin: dict, email: str, direction: str, amount_cents: int, reason: str, reference: str, db: AsyncSession) -> dict:
    """
    Staff add money to (credit) or take it out of (debit) a wallet's Deposited Funds, e.g. a
    wired guaranteed prize. Never Earnings: wired money mustn't become withdrawable.
    """

    if amount_cents <= 0:
        raise HTTPException(status_code=422, detail="Enter an amount above $0.")
    user = await _account_by_email(email, db)
    wallet = await ledger.wallet_of(user.id, db, lock = True)
    accounts = await ledger.wallet_accounts(wallet.id, db)
    deposited = accounts[ledger.DEPOSITED]
    if direction == "debit" and deposited.balance_cents < amount_cents and wallet.frozen_at is None:
        raise HTTPException(status_code=409, detail=f"Deposited Funds only has {_money(deposited.balance_cents)}.")
    external = await ledger.system_account("external", db)
    sign = 1 if direction == "credit" else -1
    kind = "manual_credit" if direction == "credit" else "manual_debit"
    await ledger.post(db, kind, [(external.id, -sign * amount_cents), (deposited.id, sign * amount_cents)],
                      created_by = uuid.UUID(admin["sub"]), reason = reason.strip(), reference = reference.strip())
    await db.flush()
    await ledger.unfreeze_if_clear(wallet, db)
    details = {"account": str(user.id), "email": user.email, "amount_cents": amount_cents, "reason": reason, "reference": reference}
    _audit(db, admin, f"wallet:{kind}", details)
    notify(db, [user.id], "wallet_adjusted", {"amount_cents": sign * amount_cents, "reason": reason.strip()})
    await db.commit()
    await _alert(f"uSync {kind.replace('_', ' ')}: {_money(amount_cents)} for {user.email}", {**details, "by": admin.get("email")})
    return await admin_wallet(user.email, db)

async def refund_deposit(admin: dict, deposit_id: uuid.UUID, reason: str, db: AsyncSession, stripeClient: StripeClient) -> dict:
    """
    Staff refund a deposit to its card: the whole charge (amount + fee), only while that much is
    still in Deposited Funds. Stripe first, then the ledger in the same request.
    """

    deposit = (await db.execute(select(Deposits).where(Deposits.id == deposit_id).with_for_update())).scalars().first()
    if deposit is None:
        raise HTTPException(status_code=404, detail="Deposit not found.")
    if deposit.status != "succeeded" or await _has(deposit, "deposit_refund", db):
        raise HTTPException(status_code=409, detail="Only a succeeded deposit that hasn't been refunded or charged back can be refunded.")
    wallet = (await db.execute(select(Wallets).where(Wallets.id == deposit.wallet_id).with_for_update())).scalars().one()
    accounts = await ledger.wallet_accounts(wallet.id, db)
    if accounts[ledger.DEPOSITED].balance_cents < deposit.amount_cents:
        raise HTTPException(status_code=409, detail=(
            f"Deposited Funds only has {_money(accounts[ledger.DEPOSITED].balance_cents)}, so this {_money(deposit.amount_cents)} deposit can't be refunded."
        ))
    try:
        await stripeClient.v1.refunds.create_async(
            {"payment_intent": deposit.stripe_payment_intent_id, "amount": deposit.amount_cents + deposit.fee_cents,
             "metadata": {"kind": "wallet_deposit_refund", "deposit_id": str(deposit.id)}},
            {"idempotency_key": f"wallet-deposit-refund-{deposit.id}"},
        )
    except stripe.StripeError as e:
        raise HTTPException(status_code=502, detail=f"Stripe refused the refund: {e.user_message or e}")
    await ledger.post(db, "deposit_refund", await _deposit_lines(deposit, accounts, -1, db), deposit_id = deposit.id,
                      created_by = uuid.UUID(admin["sub"]), reason = reason.strip() or None)
    deposit.status = "refunded"
    details = {"deposit": str(deposit.id), "wallet": str(wallet.id), "amount_cents": deposit.amount_cents, "fee_cents": deposit.fee_cents, "reason": reason}
    _audit(db, admin, "wallet:deposit_refund", details)
    notify(db, [wallet.account_id], "deposit_refunded", {"amount_cents": deposit.amount_cents + deposit.fee_cents})
    await db.commit()
    await _alert(f"uSync deposit refund: {_money(deposit.amount_cents + deposit.fee_cents)}", {**details, "by": admin.get("email")})
    email = (await db.execute(select(UsersParent.email).where(UsersParent.id == wallet.account_id))).scalar()
    return await admin_wallet(email, db) if email else {}
