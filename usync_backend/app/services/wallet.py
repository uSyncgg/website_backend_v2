import csv
import io
import logging
import os
import uuid

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from sqlalchemy import select, func, extract
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import AsyncSessionLocal
from app.models.users import UsersParent, Profiles, Players
from app.models.brackets import BracketEvents
from app.models.wallet import Wallets, WalletTransactions, PlayerEventResults, BracketEntryPayments
from app.services.notifications import notify

logger = logging.getLogger(__name__)

# The Bank: every account has a uSync balance (a wallet) in cents, and a history of what moved it.
#
# - Deposits are instant and carry a 5% fee charged on top: asking for $20 charges $21.00 and puts
#   the full $20 in the balance. Minimum $5.
# - Withdrawals are net 30: the money leaves the balance straight away (so it can't be spent twice)
#   and is paid out on available_on, 30 days out (see settle_due_withdrawals). Minimum $5. A pending
#   withdrawal can be cancelled to put the money back.
# - Entry fees, refunds, prizes and host payouts also move the balance; those are worked out in
#   app.services.entry_payments and app.services.payouts and use the helpers here.
#
# Stripe isn't linked yet. Until it is, deposits only work when WALLET_TEST_DEPOSITS=true (local
# and staging), because otherwise anyone could deposit money that was never paid. Search for
# "STRIPE" for the places to connect it.

MIN_DEPOSIT_CENTS = 500
MIN_WITHDRAWAL_CENTS = 500
MAX_DEPOSIT_CENTS = 500_000
DEPOSIT_FEE_PERCENT = 5
PAYOUT_DAYS = 30
HISTORY_LIMIT = 200

class InsufficientFunds(Exception):
    def __init__(self, balance_cents: int, needed_cents: int):
        self.balance_cents = balance_cents
        self.needed_cents = needed_cents
        super().__init__(f"needs {needed_cents}, has {balance_cents}")

def _cents(dollars: Decimal) -> int:
    return int((dollars * 100).quantize(Decimal("1"), rounding = ROUND_HALF_UP))

def fee_for(cents: int, percent: int) -> int:
    return (cents * percent + 50) // 100

def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _test_deposits_enabled() -> bool:
    return os.getenv("WALLET_TEST_DEPOSITS", "false").lower() == "true"

MIN_AGE = 18

async def is_minor(account_id, db: AsyncSession) -> bool:
    """
    Whether the account's player profile gives a date of birth that makes them under 18. Accounts
    with no date of birth (hosts, venues, organizations) aren't treated as minors.
    """

    dob = (await db.execute(
        select(Players.date_of_birth).join(Profiles, Profiles.id == Players.profile_id).where(Profiles.account_id == account_id)
    )).scalars().first()
    if not dob:
        return False
    today = _now().date()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age < MIN_AGE

async def assert_adult(account_id, db: AsyncSession) -> None:
    if await is_minor(account_id, db):
        raise HTTPException(status_code = 403, detail = "The uSync Bank is for users 18 and older.")

async def assert_can_close(account_id, db: AsyncSession) -> None:
    """
    Raises 409 while the account still has money in motion, so closing it never strands or loses
    anyone's funds: a balance, a pending withdrawal, entry fees in events that haven't finished, or
    entry revenue in a hosted event that hasn't been paid out or cancelled.
    """

    if await balance_of(account_id, db) > 0:
        raise HTTPException(status_code = 409, detail = "Withdraw your uSync balance before closing your account.")

    pending = (await db.execute(
        select(WalletTransactions.id).where(WalletTransactions.account_id == account_id)
        .where(WalletTransactions.type == "withdrawal").where(WalletTransactions.status == "pending").limit(1)
    )).first()
    if pending:
        raise HTTPException(status_code = 409, detail = "You have a withdrawal that hasn't been paid out yet. You can close your account once it has been.")

    open_event = (BracketEvents.paid_out_at.is_(None)) & (BracketEvents.cancelled_at.is_(None))
    entered = (await db.execute(
        select(BracketEntryPayments.id).join(BracketEvents, BracketEvents.id == BracketEntryPayments.event_id)
        .where(BracketEntryPayments.payer_account_id == account_id).where(BracketEntryPayments.status == "paid").where(open_event).limit(1)
    )).first()
    if entered:
        raise HTTPException(status_code = 409, detail = "You've paid entry fees for an event that hasn't finished. Leave it or wait until it's paid out before closing your account.")

    hosted = (await db.execute(
        select(BracketEntryPayments.id).join(BracketEvents, BracketEvents.id == BracketEntryPayments.event_id)
        .where(BracketEvents.host_profile_id.in_(select(Profiles.id).where(Profiles.account_id == account_id)))
        .where(BracketEntryPayments.status.in_(("paid", "forfeited"))).where(open_event).limit(1)
    )).first()
    if hosted:
        raise HTTPException(status_code = 409, detail = "You host an event with entry fees that haven't been paid out. Close it out or cancel it before closing your account.")

# ---- primitives (callers commit) -------------------------------------------

async def locked_wallet(account_id, db: AsyncSession) -> Wallets:
    """
    The account's wallet with its row locked until the caller commits or rolls back, created
    with a zero balance the first time it's needed.
    """

    await db.execute(insert(Wallets).values(account_id = account_id).on_conflict_do_nothing())
    return (await db.execute(
        select(Wallets).where(Wallets.account_id == account_id).with_for_update()
    )).scalar_one()

async def balance_of(account_id, db: AsyncSession) -> int:
    return (await db.execute(select(Wallets.balance_cents).where(Wallets.account_id == account_id))).scalar() or 0

async def debit(account_id, cents: int, type_: str, description: str, db: AsyncSession, *, fee_cents: int = 0,
                event_public_id: str | None = None, entry_id=None, related_id=None) -> WalletTransactions:
    """
    Takes cents out of the balance and records it. Raises InsufficientFunds (and changes nothing)
    when the balance is short.
    """

    wallet = await locked_wallet(account_id, db)
    if wallet.balance_cents < cents:
        raise InsufficientFunds(wallet.balance_cents, cents)
    wallet.balance_cents -= cents
    wallet.updated_at = _now()
    tx = WalletTransactions(
        id = uuid.uuid4(), account_id = account_id, type = type_, status = "completed", amount_cents = -cents, fee_cents = fee_cents,
        description = description, event_public_id = event_public_id, entry_id = entry_id, related_id = related_id,
    )
    db.add(tx)
    return tx

async def credit(account_id, cents: int, type_: str, description: str, db: AsyncSession, *, fee_cents: int = 0,
                 event_public_id: str | None = None, entry_id=None, related_id=None) -> WalletTransactions:
    """
    Puts cents into the balance and records it.
    """

    wallet = await locked_wallet(account_id, db)
    wallet.balance_cents += cents
    wallet.updated_at = _now()
    tx = WalletTransactions(
        id = uuid.uuid4(), account_id = account_id, type = type_, status = "completed", amount_cents = cents, fee_cents = fee_cents,
        description = description, event_public_id = event_public_id, entry_id = entry_id, related_id = related_id,
    )
    db.add(tx)
    return tx

# ---- output ----------------------------------------------------------------

def receipt_number(tx_id) -> str:
    return "USY-" + str(tx_id).replace("-", "")[:8].upper()

def _tx_out(t: WalletTransactions) -> dict:
    charged = t.amount_cents + t.fee_cents if t.type == "deposit" else None
    return {
        "id": str(t.id),
        "receiptNumber": receipt_number(t.id),
        "type": t.type,
        "status": t.status,
        "amountCents": t.amount_cents,
        "feeCents": t.fee_cents,
        "chargedCents": charged,
        "description": t.description,
        "eventId": t.event_public_id,
        "availableOn": t.available_on,
        "settledAt": t.settled_at,
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

    balance = await balance_of(user_id, db)
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

# ---- deposits and withdrawals ----------------------------------------------

async def deposit(user_id: str, amount: Decimal, db: AsyncSession) -> dict:
    await assert_adult(user_id, db)
    cents = _cents(amount)
    if cents < MIN_DEPOSIT_CENTS:
        raise HTTPException(status_code = 422, detail = f"The minimum deposit is {money(MIN_DEPOSIT_CENTS)}.")
    if cents > MAX_DEPOSIT_CENTS:
        raise HTTPException(status_code = 422, detail = f"The maximum deposit is {money(MAX_DEPOSIT_CENTS)}.")
    if not _test_deposits_enabled():
        # STRIPE: remove this once the charge below goes through Stripe.
        raise HTTPException(status_code = 503, detail = "Deposits aren't available yet - payments are coming soon.")

    fee = fee_for(cents, DEPOSIT_FEE_PERCENT)
    # STRIPE: charge the customer cents + fee here (a PaymentIntent) and only continue once it
    # succeeds; store its id in provider_ref. Better still, credit the wallet from the webhook.
    tx = await credit(user_id, cents, "deposit", "Deposit", db, fee_cents = fee)
    tx.provider_ref = None
    await db.commit()
    return await get_wallet(user_id, db)

async def request_withdrawal(user_id: str, amount: Decimal, db: AsyncSession) -> dict:
    await assert_adult(user_id, db)
    cents = _cents(amount)
    if cents < MIN_WITHDRAWAL_CENTS:
        raise HTTPException(status_code = 422, detail = f"The minimum withdrawal is {money(MIN_WITHDRAWAL_CENTS)}.")

    try:
        tx = await debit(user_id, cents, "withdrawal", "Withdrawal", db)
    except InsufficientFunds as e:
        raise HTTPException(status_code = 409, detail = f"You only have {money(e.balance_cents)} available to withdraw.")

    # STRIPE: schedule the payout for available_on (a Transfer/Payout) and keep its id in provider_ref.
    tx.status = "pending"
    tx.available_on = _now() + timedelta(days = PAYOUT_DAYS)
    await db.commit()
    return await get_wallet(user_id, db)

async def cancel_withdrawal(user_id: str, transaction_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    Pulls a pending withdrawal back: it's marked cancelled and the money returns to the balance.
    """

    wallet = await locked_wallet(user_id, db)
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

async def settle_due_withdrawals() -> int:
    """
    The net 30 job: marks every pending withdrawal whose payout date has arrived as paid, tells the
    player in the app and by email, and returns how many it settled. Safe to run repeatedly - a
    withdrawal is only picked up while it's pending, and each is settled in its own transaction.
    """

    from app.services.email import send_withdrawal_paid_email

    async with AsyncSessionLocal() as db:
        due = (await db.execute(
            select(WalletTransactions.id)
            .where(WalletTransactions.type == "withdrawal")
            .where(WalletTransactions.status == "pending")
            .where(WalletTransactions.available_on <= _now())
            .limit(100)
        )).scalars().all()

    settled = 0
    for tx_id in due:
        async with AsyncSessionLocal() as db:
            tx = (await db.execute(
                select(WalletTransactions).where(WalletTransactions.id == tx_id).where(WalletTransactions.status == "pending").with_for_update()
            )).scalars().first()
            if not tx:
                continue

            # STRIPE: this is where the payout is sent; only mark it paid once Stripe confirms.
            tx.status = "completed"
            tx.settled_at = _now()
            notify(db, [tx.account_id], "withdrawal_paid", {"amount_cents": -tx.amount_cents, "receipt": receipt_number(tx.id), "transaction_id": str(tx.id)})
            email = (await db.execute(select(UsersParent.email).where(UsersParent.id == tx.account_id))).scalar()
            amount, number = -tx.amount_cents, receipt_number(tx.id)
            await db.commit()
            settled += 1

        if email:
            try:
                await send_withdrawal_paid_email(email, amount, number)
            except Exception:
                logger.exception("Couldn't email the withdrawal receipt for %s", number)

    return settled

# ---- receipts and exports --------------------------------------------------

async def get_receipt(user_id: str, transaction_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    A printable receipt for one transaction: the receipt number, who and when, and the line items
    that add up to what moved.
    """

    tx = (await db.execute(
        select(WalletTransactions).where(WalletTransactions.id == transaction_id).where(WalletTransactions.account_id == user_id)
    )).scalars().first()
    if not tx:
        raise HTTPException(status_code = 404, detail = "Receipt not found.")

    email = (await db.execute(select(UsersParent.email).where(UsersParent.id == user_id))).scalar()
    amount, fee = abs(tx.amount_cents), tx.fee_cents

    if tx.type == "deposit":
        lines = [("Added to your balance", amount), ("uSync deposit fee (5%)", fee)]
        total = amount + fee
    elif tx.type == "entry_fee":
        lines = [("Pass price", amount - fee), ("uSync fee (5%)", fee)]
        total = amount
    elif tx.type == "entry_refund":
        lines = [("Pass price", amount - fee), ("uSync fee (5%)", fee)]
        total = amount
    elif tx.type == "host_payout":
        lines = [("Entry revenue after prizes", amount + fee), ("uSync fee (2.5%)", -fee)]
        total = amount
    else:
        lines = [(tx.description or tx.type.replace("_", " ").title(), amount)]
        total = amount

    return {
        "receiptNumber": receipt_number(tx.id),
        "type": tx.type,
        "status": tx.status,
        "description": tx.description,
        "eventId": tx.event_public_id,
        "date": tx.created_at,
        "paidOn": tx.settled_at,
        "payoutDate": tx.available_on,
        "email": email,
        "lines": [{"label": label, "cents": cents} for label, cents in lines],
        "totalCents": total,
        "direction": "credit" if tx.amount_cents > 0 else "debit",
    }

# Spreadsheet programs run text that starts with these as a formula; stop that for names players or hosts typed.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

def _safe(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_STARTS) else text

def _year_bounds(year: int) -> tuple[datetime, datetime]:
    return datetime(year, 1, 1, tzinfo = timezone.utc), datetime(year + 1, 1, 1, tzinfo = timezone.utc)

async def _year_transactions(user_id: str, year: int | None, db: AsyncSession) -> list[WalletTransactions]:
    query = select(WalletTransactions).where(WalletTransactions.account_id == user_id)
    if year is not None:
        start, end = _year_bounds(year)
        query = query.where(WalletTransactions.created_at >= start).where(WalletTransactions.created_at < end)
    return list((await db.execute(query.order_by(WalletTransactions.created_at))).scalars().all())

async def export_csv(user_id: str, year: int | None, db: AsyncSession) -> str:
    """
    The bank history as CSV: one row per transaction, oldest first (all of it, or one calendar year).
    """

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date (UTC)", "Receipt", "Type", "Status", "Description", "Event", "Amount (USD)", "uSync fee (USD)"])
    for t in await _year_transactions(user_id, year, db):
        writer.writerow([
            t.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), receipt_number(t.id), t.type, t.status,
            _safe(t.description), _safe(t.event_public_id or ""), f"{t.amount_cents / 100:.2f}", f"{t.fee_cents / 100:.2f}",
        ])
    return out.getvalue()

async def tax_summary(user_id: str, year: int, db: AsyncSession) -> dict:
    """
    What moved in a calendar year, grouped for tax time: prize winnings and host earnings are the
    income; deposit and entry fees are costs. Cancelled withdrawals are left out. This is a record
    of activity, not tax advice.
    """

    totals = {"deposits": 0, "depositFees": 0, "entryFees": 0, "entryRefunds": 0, "prizeWinnings": 0, "hostEarnings": 0, "hostFees": 0, "withdrawalsPaid": 0, "withdrawalsPending": 0}
    for t in await _year_transactions(user_id, year, db):
        if t.type == "deposit":
            totals["deposits"] += t.amount_cents
            totals["depositFees"] += t.fee_cents
        elif t.type == "entry_fee":
            totals["entryFees"] += -t.amount_cents
        elif t.type == "entry_refund":
            totals["entryRefunds"] += t.amount_cents
        elif t.type == "prize":
            totals["prizeWinnings"] += t.amount_cents
        elif t.type == "host_payout":
            totals["hostEarnings"] += t.amount_cents
            totals["hostFees"] += t.fee_cents
        elif t.type == "withdrawal" and t.status == "completed":
            totals["withdrawalsPaid"] += -t.amount_cents
        elif t.type == "withdrawal" and t.status == "pending":
            totals["withdrawalsPending"] += -t.amount_cents

    return {"year": year, **{f"{k}Cents": v for k, v in totals.items()}, "netEntryFeesCents": totals["entryFees"] - totals["entryRefunds"]}

async def tax_summary_csv(user_id: str, year: int, db: AsyncSession) -> str:
    s = await tax_summary(user_id, year, db)
    rows = [
        ("Prize winnings (income)", s["prizeWinningsCents"]),
        ("Host earnings after uSync fee (income)", s["hostEarningsCents"]),
        ("Entry fees paid, net of refunds", s["netEntryFeesCents"]),
        ("Deposit fees paid", s["depositFeesCents"]),
        ("uSync fee on host earnings", s["hostFeesCents"]),
        ("Deposits", s["depositsCents"]),
        ("Withdrawals paid out", s["withdrawalsPaidCents"]),
        ("Withdrawals still pending", s["withdrawalsPendingCents"]),
    ]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([f"uSync tax summary {s['year']}", "Amount (USD)"])
    for label, cents in rows:
        writer.writerow([label, f"{cents / 100:.2f}"])
    writer.writerow([])
    writer.writerow(["This is a record of account activity, not tax advice."])
    return out.getvalue()

async def get_stats(user_id: str, db: AsyncSession) -> dict:
    """
    Career numbers across all of the account's profiles, from the results recorded when events close.
    """

    mine = select(Profiles.id).where(Profiles.account_id == user_id)
    row = (await db.execute(
        select(
            func.count(PlayerEventResults.id),
            func.coalesce(func.sum(PlayerEventResults.wins), 0),
            func.coalesce(func.sum(PlayerEventResults.losses), 0),
            func.coalesce(func.sum(PlayerEventResults.earnings_cents), 0),
            func.count().filter(PlayerEventResults.placement == 1),
            func.count().filter(PlayerEventResults.placement.in_([1, 2, 3])),
        ).where(PlayerEventResults.profile_id.in_(mine))
    )).one()

    return {
        "eventsPlayed": row[0], "wins": int(row[1]), "losses": int(row[2]), "earningsCents": int(row[3]),
        "firstPlaces": row[4], "podiums": row[5],
    }

async def available_years(user_id: str, db: AsyncSession) -> list[int]:
    years = (await db.execute(
        select(extract("year", WalletTransactions.created_at)).where(WalletTransactions.account_id == user_id).distinct()
    )).scalars().all()
    return sorted({int(y) for y in years} | {_now().year}, reverse = True)
