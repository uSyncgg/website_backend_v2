import os
import uuid

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.wallet import Wallets, WalletTransactions

# The Bank: every account has a uSync balance (a wallet) in cents, and a history of what moved it.
#
# - Deposits are instant and carry a 5% fee charged on top: asking for $20 charges $21.00 and puts
#   the full $20 in the balance. Minimum $5.
# - Withdrawals are net 30: the money leaves the balance straight away (so it can't be spent twice)
#   and the payout lands on available_on, 30 days out. Minimum $5. A pending withdrawal can be
#   cancelled to put the money back.
# - Entry fees for paid events (pass price + the 5% uSync fee) come out of the balance when you
#   sign up and go back in when the entry is removed before the bracket goes live.
#
# Stripe isn't linked yet. Until it is, deposits only work when WALLET_TEST_DEPOSITS=true (local
# and staging), because otherwise anyone could deposit money that was never paid. Search for
# "STRIPE" below for the two places to connect it.

MIN_DEPOSIT_CENTS = 500
MIN_WITHDRAWAL_CENTS = 500
MAX_DEPOSIT_CENTS = 500_000
DEPOSIT_FEE_PERCENT = 5
ENTRY_FEE_PERCENT = 5
PAYOUT_DAYS = 30
HISTORY_LIMIT = 200

def _cents(dollars: Decimal) -> int:
    return int((dollars * 100).quantize(Decimal("1"), rounding = ROUND_HALF_UP))

def fee_for(cents: int, percent: int) -> int:
    return (cents * percent + 50) // 100

def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _test_deposits_enabled() -> bool:
    return os.getenv("WALLET_TEST_DEPOSITS", "false").lower() == "true"

async def _locked_wallet(user_id, db: AsyncSession) -> Wallets:
    """
    The account's wallet with its row locked until the caller commits or rolls back, created
    with a zero balance the first time it's needed.
    """

    await db.execute(insert(Wallets).values(account_id = user_id).on_conflict_do_nothing())
    return (await db.execute(
        select(Wallets).where(Wallets.account_id == user_id).with_for_update()
    )).scalar_one()

def _tx_out(t: WalletTransactions) -> dict:
    charged = t.amount_cents + t.fee_cents if t.type == "deposit" else None
    return {
        "id": str(t.id),
        "type": t.type,
        "status": t.status,
        "amountCents": t.amount_cents,
        "feeCents": t.fee_cents,
        "chargedCents": charged,
        "description": t.description,
        "eventId": t.event_public_id,
        "availableOn": t.available_on,
        "createdAt": t.created_at,
    }

def _limits() -> dict:
    return {
        "minDepositCents": MIN_DEPOSIT_CENTS,
        "minWithdrawalCents": MIN_WITHDRAWAL_CENTS,
        "maxDepositCents": MAX_DEPOSIT_CENTS,
        "depositFeePercent": DEPOSIT_FEE_PERCENT,
        "payoutDays": PAYOUT_DAYS,
        "depositsEnabled": _test_deposits_enabled(),
    }

async def get_wallet(user_id: str, db: AsyncSession) -> dict:
    """
    The balance, what's waiting on a payout, and the rules (minimums, fee, payout delay).
    """

    balance = (await db.execute(select(Wallets.balance_cents).where(Wallets.account_id == user_id))).scalar() or 0
    pending = (await db.execute(
        select(func.coalesce(func.sum(-WalletTransactions.amount_cents), 0))
        .where(WalletTransactions.account_id == user_id)
        .where(WalletTransactions.type == "withdrawal")
        .where(WalletTransactions.status == "pending")
    )).scalar()

    return {"balanceCents": balance, "pendingWithdrawalCents": int(pending), **_limits()}

async def list_transactions(user_id: str, db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        select(WalletTransactions)
        .where(WalletTransactions.account_id == user_id)
        .order_by(WalletTransactions.created_at.desc())
        .limit(HISTORY_LIMIT)
    )).scalars().all()

    return [_tx_out(t) for t in rows]

async def deposit(user_id: str, amount: Decimal, db: AsyncSession) -> dict:
    cents = _cents(amount)
    if cents < MIN_DEPOSIT_CENTS:
        raise HTTPException(status_code = 422, detail = f"The minimum deposit is {_money(MIN_DEPOSIT_CENTS)}.")
    if cents > MAX_DEPOSIT_CENTS:
        raise HTTPException(status_code = 422, detail = f"The maximum deposit is {_money(MAX_DEPOSIT_CENTS)}.")
    if not _test_deposits_enabled():
        # STRIPE: remove this once the charge below goes through Stripe.
        raise HTTPException(status_code = 503, detail = "Deposits aren't available yet - payments are coming soon.")

    fee = fee_for(cents, DEPOSIT_FEE_PERCENT)
    provider_ref = None
    # STRIPE: charge the customer cents + fee here (a PaymentIntent) and only continue once it
    # succeeds; store its id in provider_ref. Better still, credit the wallet from the webhook.

    wallet = await _locked_wallet(user_id, db)
    wallet.balance_cents += cents
    wallet.updated_at = _now()
    db.add(WalletTransactions(
        id = uuid.uuid4(), account_id = user_id, type = "deposit", status = "completed",
        amount_cents = cents, fee_cents = fee, description = "Deposit", provider_ref = provider_ref,
    ))
    await db.commit()
    return await get_wallet(user_id, db)

async def request_withdrawal(user_id: str, amount: Decimal, db: AsyncSession) -> dict:
    cents = _cents(amount)
    if cents < MIN_WITHDRAWAL_CENTS:
        raise HTTPException(status_code = 422, detail = f"The minimum withdrawal is {_money(MIN_WITHDRAWAL_CENTS)}.")

    wallet = await _locked_wallet(user_id, db)
    if cents > wallet.balance_cents:
        raise HTTPException(status_code = 409, detail = f"You only have {_money(wallet.balance_cents)} available to withdraw.")

    # STRIPE: schedule the payout for available_on (a Transfer/Payout job) and keep its id in provider_ref.
    wallet.balance_cents -= cents
    wallet.updated_at = _now()
    db.add(WalletTransactions(
        id = uuid.uuid4(), account_id = user_id, type = "withdrawal", status = "pending",
        amount_cents = -cents, description = "Withdrawal", available_on = _now() + timedelta(days = PAYOUT_DAYS),
    ))
    await db.commit()
    return await get_wallet(user_id, db)

async def cancel_withdrawal(user_id: str, transaction_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    Pulls a pending withdrawal back: it's marked cancelled and the money returns to the balance.
    """

    wallet = await _locked_wallet(user_id, db)
    tx = (await db.execute(
        select(WalletTransactions)
        .where(WalletTransactions.id == transaction_id)
        .where(WalletTransactions.account_id == user_id)
        .where(WalletTransactions.type == "withdrawal")
        .with_for_update()
    )).scalars().first()

    if not tx:
        raise HTTPException(status_code = 404, detail = "Withdrawal not found.")
    if tx.status != "pending":
        raise HTTPException(status_code = 409, detail = "This withdrawal can't be cancelled any more.")

    tx.status = "cancelled"
    wallet.balance_cents += -tx.amount_cents
    wallet.updated_at = _now()
    await db.commit()
    return await get_wallet(user_id, db)

# ---- entry fees (called from app.services.brackets) ------------------------

def entry_cost(event, pass_name: str | None) -> tuple[int, int, str] | None:
    """
    (price_cents, fee_cents, pass name) to sign up for the event, or None when it's free. Events
    with uSync payments turned on charge the chosen pass's price plus the 5% uSync fee.
    """

    payments = event.payments or {}
    tiers = payments.get("tiers") or []
    if not payments.get("enabled") or not tiers:
        return None

    tier = next((t for t in tiers if t.get("name") == pass_name), None) if pass_name else tiers[0]
    if tier is None:
        raise HTTPException(status_code = 422, detail = "Pick one of the event's passes.")

    price = _cents(Decimal(str(tier.get("price") or 0)))
    if price <= 0:
        return None

    return price, fee_for(price, ENTRY_FEE_PERCENT), tier.get("name", "")

async def charge_entry(user_id: str, event, entry_id: uuid.UUID, cost: tuple[int, int, str], db: AsyncSession) -> None:
    """
    Takes an entry's price and fee out of the balance. Doesn't commit - it goes in the same
    transaction as the entry itself, so a failed sign-up never costs anything.
    """

    price, fee, pass_name = cost
    total = price + fee
    wallet = await _locked_wallet(user_id, db)
    if wallet.balance_cents < total:
        raise HTTPException(
            status_code = 402,
            detail = f"Entering {event.name} costs {_money(total)} ({_money(price)} + {_money(fee)} fee) and you have {_money(wallet.balance_cents)}. Add funds on the Bank page.",
        )

    wallet.balance_cents -= total
    wallet.updated_at = _now()
    db.add(WalletTransactions(
        id = uuid.uuid4(), account_id = user_id, type = "entry_fee", status = "completed",
        amount_cents = -total, fee_cents = fee, description = f"Entry: {event.name}" + (f" ({pass_name})" if pass_name else ""),
        event_public_id = event.public_id, entry_id = entry_id,
    ))

async def refund_entries(entry_ids, db: AsyncSession) -> None:
    """
    Puts back what was charged for each of these entries (once). Doesn't commit.
    """

    ids = [i for i in entry_ids if i]
    if not ids:
        return

    charges = (await db.execute(
        select(WalletTransactions)
        .where(WalletTransactions.entry_id.in_(ids))
        .where(WalletTransactions.type == "entry_fee")
    )).scalars().all()
    refunded = set((await db.execute(
        select(WalletTransactions.entry_id)
        .where(WalletTransactions.entry_id.in_(ids))
        .where(WalletTransactions.type == "entry_refund")
    )).scalars().all())

    for charge in charges:
        if charge.entry_id in refunded:
            continue
        wallet = await _locked_wallet(charge.account_id, db)
        wallet.balance_cents += -charge.amount_cents
        wallet.updated_at = _now()
        db.add(WalletTransactions(
            id = uuid.uuid4(), account_id = charge.account_id, type = "entry_refund", status = "completed",
            amount_cents = -charge.amount_cents, fee_cents = charge.fee_cents,
            description = charge.description.replace("Entry:", "Refund:", 1),
            event_public_id = charge.event_public_id, entry_id = charge.entry_id,
        ))
