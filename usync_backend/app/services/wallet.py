import html
import logging
import uuid

import resend
import stripe
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from app.core.db import AsyncSessionLocal
from app.models.users import UsersParent
from app.models.admin import AdminAuditLog
from app.models.brackets import BracketEvents
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
# amount plus the 10% fee. The browser pays it with the Payment Element. Only Stripe's webhook
# (payment_intent.succeeded) credits the wallet: external -total, Deposited Funds +amount,
# platform revenue +fee, in one ledger transaction. Unpaid deposits are canceled after 30 minutes.
#
# A chargeback (charge.dispute.funds_withdrawn) takes the deposit back out - Deposited Funds
# first, then Earnings - and freezes the wallet if that leaves it below zero. Winning the dispute
# (charge.dispute.funds_reinstated) puts it back and thaws the wallet once it's clear.

# Deposit ids are derived from the browser's idempotency key (see start_deposit).
DEPOSIT_ID_NAMESPACE = uuid.UUID("8f1c2b6e-3d4a-4e5f-9a7b-1c2d3e4f5a6b")

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
        # Spendable money only: a Pending Withdrawal is shown on its own.
        "totalCents": deposited + earnings,
        "pendingWithdrawalCents": accounts[ledger.PENDING_WITHDRAWAL].balance_cents,
        "frozen": wallet.frozen_at is not None,
        "frozenAt": wallet.frozen_at.isoformat() if wallet.frozen_at else None,
        "autoPayUntil": wallet.auto_pay_until.isoformat() if wallet.auto_pay_until and wallet.auto_pay_until > _now() else None,
    }

BUCKETS = {ledger.DEPOSITED: "deposited", ledger.EARNINGS: "earnings", ledger.PENDING_WITHDRAWAL: "pending_withdrawal"}

async def my_wallet(user_id: str, db: AsyncSession) -> dict:
    return await _wallet_out(await ledger.wallet_of(user_id, db), db)

async def history(user_id: str, db: AsyncSession, before: int | None = None, limit: int = 30) -> dict:
    """
    The wallet's ledger lines, newest first: one row per bucket a transaction touched. `before`
    is the last line id of the previous page. Pending Withdrawal lines are only shown for a
    completed withdrawal (the money leaving); the request and the return show as the Earnings
    line alone, so the user sees each step once.
    """

    wallet = await ledger.wallet_of(user_id, db)
    query = (
        select(LedgerLines, LedgerTransactions, LedgerAccounts.type, BracketEvents.public_id, BracketEvents.name)
        .join(LedgerTransactions, LedgerTransactions.id == LedgerLines.transaction_id)
        .join(LedgerAccounts, LedgerAccounts.id == LedgerLines.ledger_account_id)
        .outerjoin(BracketEvents, BracketEvents.id == LedgerTransactions.event_id)
        .where(LedgerAccounts.wallet_id == wallet.id)
        .where(or_(LedgerAccounts.type != ledger.PENDING_WITHDRAWAL, LedgerTransactions.kind == "withdrawal_complete"))
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
            "bucket": BUCKETS[type_],
            "amountCents": line.amount_cents,
            "createdAt": txn.created_at.isoformat(),
            # Staff write the reason for manual changes; the reference (a wire number) stays internal.
            "reason": txn.reason,
            "eventId": event_public_id,
            "eventName": event_name,
        }
        for line, txn, type_, event_public_id, event_name in rows[:limit]
    ]
    return {"items": items, "nextBefore": items[-1]["id"] if len(rows) > limit else None}

# ---- deposits ----------------------------------------------------------------------------

def _deposit_out(deposit: Deposits, client_secret: str | None) -> dict:
    return {
        "depositId": str(deposit.id),
        "clientSecret": client_secret,
        "amountCents": deposit.amount_cents,
        "feeCents": deposit.fee_cents,
        "totalCents": deposit.amount_cents + deposit.fee_cents,
        "status": deposit.status,
    }

async def start_deposit(user_id: str, amount_cents: int, idempotency_key: str, db: AsyncSession, stripeClient: StripeClient) -> dict:
    """
    Makes a pending deposit and its PaymentIntent (amount + fee). Idempotent: the browser sends
    one key per deposit dialog, and the same key always returns the same deposit and
    PaymentIntent, so a retried or double-submitted request never makes a second one. The
    deposit id is derived from the key, and Stripe gets the same idempotency key, so even a crash
    between Stripe and our COMMIT ends with one PaymentIntent. The wallet row is locked so two
    deposits started at once can't both slip under the daily limit.
    """

    if amount_cents < DEPOSIT_MIN_CENTS:
        raise HTTPException(status_code=422, detail=f"The minimum deposit is {_money(DEPOSIT_MIN_CENTS)}.")
    wallet = await ledger.wallet_of(user_id, db, lock = True)

    existing = (await db.execute(select(Deposits).where(Deposits.idempotency_key == idempotency_key))).scalars().first()
    if existing is not None:
        if existing.wallet_id != wallet.id or existing.amount_cents != amount_cents:
            raise HTTPException(status_code=409, detail="This deposit was already started for a different amount. Close it and start again.")
        if existing.status == "canceled":
            # A dialog left open past the 30 minute hold: its PaymentIntent is gone. The browser
            # makes a new key and starts over; the old deposit stays canceled.
            raise HTTPException(status_code=410, detail="This deposit timed out. Press Continue to start a new one.")
        intent = await stripeClient.v1.payment_intents.retrieve_async(existing.stripe_payment_intent_id)
        return _deposit_out(existing, intent.client_secret)

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
    deposit_id = uuid.uuid5(DEPOSIT_ID_NAMESPACE, f"{wallet.id}:{idempotency_key}")
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
    deposit = Deposits(id = deposit_id, wallet_id = wallet.id, amount_cents = amount_cents, fee_cents = fee,
                       stripe_payment_intent_id = intent.id, idempotency_key = idempotency_key, status = "pending")
    db.add(deposit)
    await db.commit()
    return _deposit_out(deposit, intent.client_secret)

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

async def handle_deposit_event(event_type: str, deposit: Deposits, db: AsyncSession, stripe_object: dict | None = None) -> None:
    """
    Stripe webhook events for a wallet deposit. Each is safe to receive twice (or at the same
    time): the deposit row is locked by the caller, we check before posting, and the ledger's
    (deposit_id, kind) unique index has the final say.
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
    elif event_type == "charge.refunded":
        # Our refund landed. Backs up refund_deposit / the sweeper if their Stripe reply got lost.
        if deposit.status == "refund_pending":
            _refund_done(deposit, wallet, db)
    elif event_type in ("refund.failed", "charge.refund.updated"):
        # A refund the bank bounced later: put the money back in the wallet.
        if (stripe_object or {}).get("status") == "failed" and (stripe_object.get("metadata") or {}).get("deposit_id") == str(deposit.id):
            await _refund_failed(deposit, wallet, db, "the bank returned the refund")
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
    if accounts[ledger.PENDING_WITHDRAWAL].balance_cents != 0:
        raise HTTPException(status_code=409, detail="You have a pending withdrawal. Wait for it to finish before deleting your account.")
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

async def admin_cancel_event(admin: dict, public_id: str, reason: str, db: AsyncSession) -> dict:
    """
    Staff cancel an event and refund everyone (finished or not, e.g. a broken event that can't
    pay out). Repeating it does nothing more. Audit logged.
    """

    from app.services.brackets import admin_cancel_event as cancel  # brackets doesn't import this module
    event = await cancel(public_id, db)
    _audit(db, admin, "event_cancel", {"event_id": public_id, "event_name": event.name, "reason": reason})
    await db.commit()
    return {"id": event.public_id, "cancelledAt": event.cancelled_at.isoformat() if event.cancelled_at else None}

async def manual_move(admin: dict, email: str, direction: str, amount_cents: int, reason: str, reference: str,
                      idempotency_key: str, db: AsyncSession) -> dict:
    """
    Staff add money to (credit) or take it out of (debit) a wallet's Deposited Funds, e.g. a
    wired guaranteed prize. Never Earnings: wired money mustn't become withdrawable. Idempotent:
    the admin form sends one key per submission, so a double click or a retry posts once (the
    wallet row lock makes the check-then-post safe, and the unique key backs it up).
    """

    if amount_cents <= 0:
        raise HTTPException(status_code=422, detail="Enter an amount above $0.")
    user = await _account_by_email(email, db)
    wallet = await ledger.wallet_of(user.id, db, lock = True)
    done = (await db.execute(
        select(LedgerTransactions.id).where(LedgerTransactions.idempotency_key == idempotency_key)
    )).first()
    if done:
        return await admin_wallet(user.email, db)
    accounts = await ledger.wallet_accounts(wallet.id, db)
    deposited = accounts[ledger.DEPOSITED]
    if direction == "debit" and deposited.balance_cents < amount_cents and wallet.frozen_at is None:
        raise HTTPException(status_code=409, detail=f"Deposited Funds only has {_money(deposited.balance_cents)}.")
    external = await ledger.system_account("external", db)
    sign = 1 if direction == "credit" else -1
    kind = "manual_credit" if direction == "credit" else "manual_debit"
    await ledger.post(db, kind, [(external.id, -sign * amount_cents), (deposited.id, sign * amount_cents)],
                      created_by = uuid.UUID(admin["sub"]), reason = reason.strip(), reference = reference.strip(),
                      idempotency_key = idempotency_key)
    await db.flush()
    await ledger.unfreeze_if_clear(wallet, db)
    details = {"account": str(user.id), "email": user.email, "amount_cents": amount_cents, "reason": reason, "reference": reference}
    _audit(db, admin, f"wallet:{kind}", details)
    notify(db, [user.id], "wallet_adjusted", {"amount_cents": sign * amount_cents, "reason": reason.strip()})
    await db.commit()
    await _alert(f"uSync {kind.replace('_', ' ')}: {_money(amount_cents)} for {user.email}", {**details, "by": admin.get("email")})
    return await admin_wallet(user.email, db)

# ---- refunds ------------------------------------------------------------------------------
#
# A refund takes the money out of the wallet FIRST (a 'deposit_refund' ledger transaction and
# status 'refund_pending', committed), so it can't be spent while Stripe works. Then Stripe is
# asked for the refund. Every step can be repeated safely:
# - the ledger allows one 'deposit_refund' (and one 'deposit_refund_reversal') per deposit;
# - before creating a refund we look for one Stripe already has for this deposit (idempotency
#   keys only last 24 hours), and create with a fixed key;
# - a temporary Stripe error leaves the deposit 'refund_pending': the hold sweeper retries every
#   couple of minutes, and the charge.refunded webhook also finishes it;
# - if Stripe refuses for good (or the bank later bounces the refund), a reversal puts the money
#   back and the deposit is 'refund_failed'.

def _refund_done(deposit: Deposits, wallet: Wallets, db: AsyncSession) -> None:
    deposit.status = "refunded"
    notify(db, [wallet.account_id], "deposit_refunded", {"amount_cents": deposit.amount_cents + deposit.fee_cents})

async def _refund_failed(deposit: Deposits, wallet: Wallets, db: AsyncSession, why: str) -> None:
    if not await _has(deposit, "deposit_refund", db) or await _has(deposit, "deposit_refund_reversal", db):
        return
    accounts = await ledger.wallet_accounts(wallet.id, db)
    await ledger.post(db, "deposit_refund_reversal", await _deposit_lines(deposit, accounts, 1, db), deposit_id = deposit.id,
                      reason = f"Refund didn't go through: {why}")
    deposit.status = "refund_failed"
    logger.error("Refund of deposit %s failed (%s); the money is back in wallet %s", deposit.id, why, wallet.id)
    await _alert(f"uSync deposit refund FAILED: {_money(deposit.amount_cents + deposit.fee_cents)}",
                 {"deposit": str(deposit.id), "wallet": str(wallet.id), "why": why, "what happened": "the money was put back in the wallet"})

async def _stripe_refund(deposit: Deposits, stripeClient: StripeClient):
    """The refund Stripe already has for this deposit, or a new one (with a fixed key)."""
    existing = await stripeClient.v1.refunds.list_async({"payment_intent": deposit.stripe_payment_intent_id, "limit": 100})
    for refund in existing.data:
        if (refund.metadata or {}).get("deposit_id") == str(deposit.id) and refund.status not in ("failed", "canceled"):
            return refund
    return await stripeClient.v1.refunds.create_async(
        {"payment_intent": deposit.stripe_payment_intent_id, "amount": deposit.amount_cents + deposit.fee_cents,
         "metadata": {"kind": "wallet_deposit_refund", "deposit_id": str(deposit.id)}},
        {"idempotency_key": f"wallet-deposit-refund-{deposit.id}"},
    )

async def settle_refund(deposit_id, db: AsyncSession, stripeClient: StripeClient) -> str:
    """
    Gets Stripe to refund a 'refund_pending' deposit. Returns 'refunded', 'failed', 'retry'
    (left pending for the sweeper) or 'noop' (nothing pending). Commits.
    """

    deposit = (await db.execute(
        select(Deposits).where(Deposits.id == deposit_id).where(Deposits.status == "refund_pending").with_for_update()
    )).scalars().first()
    if deposit is None:
        await db.rollback()
        return "noop"
    wallet = (await db.execute(select(Wallets).where(Wallets.id == deposit.wallet_id).with_for_update())).scalars().one()
    try:
        refund = await _stripe_refund(deposit, stripeClient)
    except (stripe.InvalidRequestError, stripe.CardError) as e:
        await _refund_failed(deposit, wallet, db, e.user_message or str(e))
        await db.commit()
        return "failed"
    except stripe.StripeError:
        logger.warning("Temporary Stripe error refunding deposit %s; the sweeper will retry", deposit.id, exc_info = True)
        await db.rollback()
        return "retry"
    if refund.status in ("failed", "canceled"):
        await _refund_failed(deposit, wallet, db, f"Stripe marked the refund {refund.status}")
        await db.commit()
        return "failed"
    _refund_done(deposit, wallet, db)
    await db.commit()
    return "refunded"

async def refund_deposit(admin: dict, deposit_id: uuid.UUID, reason: str, db: AsyncSession, stripeClient: StripeClient) -> dict:
    """
    Staff refund a deposit to its card: the whole charge (amount + fee), only while that much is
    still in Deposited Funds. Pressing it again (or retrying) never refunds twice: a refund that's
    already pending is just pushed along, and a finished one is left alone.
    """

    deposit = (await db.execute(select(Deposits).where(Deposits.id == deposit_id).with_for_update())).scalars().first()
    if deposit is None:
        raise HTTPException(status_code=404, detail="Deposit not found.")
    wallet = (await db.execute(select(Wallets).where(Wallets.id == deposit.wallet_id).with_for_update())).scalars().one()
    email = (await db.execute(select(UsersParent.email).where(UsersParent.id == wallet.account_id))).scalar()

    deposit_id, total = deposit.id, deposit.amount_cents + deposit.fee_cents
    if deposit.status in ("refund_pending", "refunded"):
        await db.rollback()
        await settle_refund(deposit_id, db, stripeClient)
        return await admin_wallet(email, db) if email else {}
    if deposit.status != "succeeded":
        raise HTTPException(status_code=409, detail="Only a succeeded deposit that hasn't been refunded or charged back can be refunded.")
    accounts = await ledger.wallet_accounts(wallet.id, db)
    if accounts[ledger.DEPOSITED].balance_cents < deposit.amount_cents:
        raise HTTPException(status_code=409, detail=(
            f"Deposited Funds only has {_money(accounts[ledger.DEPOSITED].balance_cents)}, so this {_money(deposit.amount_cents)} deposit can't be refunded."
        ))

    # 1. Take the money out of the wallet and commit, before Stripe is involved.
    await ledger.post(db, "deposit_refund", await _deposit_lines(deposit, accounts, -1, db), deposit_id = deposit.id,
                      created_by = uuid.UUID(admin["sub"]), reason = reason.strip() or None)
    deposit.status = "refund_pending"
    details = {"deposit": str(deposit.id), "wallet": str(wallet.id), "amount_cents": deposit.amount_cents, "fee_cents": deposit.fee_cents, "reason": reason}
    _audit(db, admin, "wallet:deposit_refund", details)
    await db.commit()

    # 2. Ask Stripe. A temporary failure stays pending for the sweeper.
    outcome = await settle_refund(deposit_id, db, stripeClient)
    await _alert(f"uSync deposit refund ({outcome}): {_money(total)}", {**details, "by": admin.get("email")})
    if outcome == "failed":
        raise HTTPException(status_code=502, detail="Stripe refused the refund, so the money was put back in the wallet.")
    return await admin_wallet(email, db) if email else {}

async def retry_pending_refunds(stripeClient: StripeClient, older_than_minutes: int = 1) -> int:
    """
    The hold sweeper's pass over refunds left 'refund_pending' by a temporary Stripe error (or a
    crash between our COMMIT and Stripe). Returns how many finished.
    """

    async with AsyncSessionLocal() as db:
        # Leave a refund that's being made right now to the request making it.
        cutoff = _now() - timedelta(minutes = older_than_minutes)
        pending = (await db.execute(
            select(Deposits.id).join(LedgerTransactions, (LedgerTransactions.deposit_id == Deposits.id) & (LedgerTransactions.kind == "deposit_refund"))
            .where(Deposits.status == "refund_pending").where(LedgerTransactions.created_at < cutoff).limit(50)
        )).scalars().all()
        await db.rollback()
        finished = 0
        for deposit_id in pending:
            try:
                if await settle_refund(deposit_id, db, stripeClient) in ("refunded", "failed"):
                    finished += 1
            except Exception:
                await db.rollback()
                logger.exception("Retrying the refund of deposit %s failed", deposit_id)
        return finished
