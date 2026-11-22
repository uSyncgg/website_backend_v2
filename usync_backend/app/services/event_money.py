import uuid

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, func, delete, exists, and_, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.brackets import BracketEvents, BracketEntries, BracketEntryPlayers, Brackets
from app.models.wallet import Wallets, LedgerAccounts, LedgerTransactions, LedgerLines, EntryPayments, EVENT_ACCOUNT_TYPES
from app.services import PLATFORM_FEE_PERCENT
from app.services import ledger
from app.services import bracket_access as access
from app.services.notifications import notify

# Event money (GLOSSARY.md): entry fees into an event's pool, a guaranteed prize held for the
# event, refunds and cancellations. Payouts (the pool and guarantee going to players and the host)
# are PR 4.
#
# Every function here runs inside the caller's transaction, after the caller has locked the
# event row, and never commits. Wallets are locked in id order, so two sign-ups sharing a payer
# can't deadlock. Every movement is backed by a unique key in Postgres:
# - entry fee / refund: one 'entry_fee' and one 'entry_refund' ledger transaction per payment;
# - one paid team payment per entry, and one paid payment per player per event;
# - guarantee: one 'guarantee_fund' and one 'guarantee_refund' per event.
# So a retry, a double click or two requests racing can't charge, refund or fund twice.
#
# Refunds put back exactly the lines the payment took (each bucket gets back what came out of it).
# There are no refunds once the bracket is published, except when the event is cancelled.

ENTRY_FEE_MIN_CENTS = 100
ENTRY_FEE_MAX_CENTS = 50_000
HOST_CUT_MAX_BPS = 5_000
MAX_PAID_PLACES = 16
AUTO_PAY_DAYS = (7, 30, 90)
MONEY_FIELDS = ("entry_fee_cents", "prize_funding", "guarantee_cents", "host_cut_bps", "payout_structure")
AUTO_CANCEL_BATCH = 50

POOL = "event_pool"
GUARANTEE = "event_guarantee"

def _now() -> datetime:
    return datetime.now(timezone.utc)

def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"

def _names(names: list[str]) -> str:
    names = sorted(set(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

# ---- settings ----------------------------------------------------------------------------

def _check_payout_structure(shares) -> list[int]:
    if not isinstance(shares, list) or not 1 <= len(shares) <= MAX_PAID_PLACES:
        raise HTTPException(status_code=422, detail=f"Pay between 1 and {MAX_PAID_PLACES} places.")
    if any(not isinstance(s, int) or isinstance(s, bool) or s < 100 for s in shares):
        raise HTTPException(status_code=422, detail="Every paid place gets at least 1% of the prize pool, in whole hundredths of a percent.")
    if any(b > a for a, b in zip(shares, shares[1:])):
        raise HTTPException(status_code=422, detail="A place can't be paid more than the place above it.")
    if sum(shares) != 10_000:
        raise HTTPException(status_code=422, detail="The payout percentages have to add up to 100%.")
    return shares

def money_settings(values: dict, current: BracketEvents | None = None, payments: dict | None = None) -> dict:
    """
    The five money settings after applying `values` (snake_case, any subset) to the event's
    current ones, checked and tidied, as column values. The same rules as Postgres's
    ck_bracket_events_money, with messages a host can act on. 0 or null entry fee = free.
    """

    merged = {f: getattr(current, f) if current is not None else None for f in MONEY_FIELDS}
    merged.update({k: v for k, v in values.items() if k in MONEY_FIELDS})
    fee = merged["entry_fee_cents"] or None
    funding = merged["prize_funding"] or None

    if fee is not None and not ENTRY_FEE_MIN_CENTS <= fee <= ENTRY_FEE_MAX_CENTS:
        raise HTTPException(status_code=422, detail=f"Entry fees are {money(ENTRY_FEE_MIN_CENTS)} to {money(ENTRY_FEE_MAX_CENTS)} per player.")
    if fee is not None and (payments or {}).get("enabled"):
        raise HTTPException(status_code=422, detail="An event can take wallet entry fees or sell passes, not both.")

    if funding is None:
        if fee is not None:
            raise HTTPException(status_code=422, detail="Choose how the prize is paid: guaranteed by you, or from the entry fees.")
        return {f: None for f in MONEY_FIELDS}
    if funding == "entry_funded":
        if fee is None:
            raise HTTPException(status_code=422, detail="A prize paid from entry fees needs an entry fee.")
        cut = merged["host_cut_bps"] or 0
        if not 0 <= cut <= HOST_CUT_MAX_BPS:
            raise HTTPException(status_code=422, detail="Your cut can be 0% to 50% of the entry fees.")
        return {"entry_fee_cents": fee, "prize_funding": funding, "guarantee_cents": None, "host_cut_bps": cut,
                "payout_structure": _check_payout_structure(merged["payout_structure"])}
    if funding == "guaranteed":
        guarantee = merged["guarantee_cents"]
        if not guarantee or guarantee <= 0 or guarantee % 100:
            raise HTTPException(status_code=422, detail="A guaranteed prize is a whole number of dollars.")
        return {"entry_fee_cents": fee, "prize_funding": funding, "guarantee_cents": guarantee, "host_cut_bps": None,
                "payout_structure": _check_payout_structure(merged["payout_structure"])}
    raise HTTPException(status_code=422, detail="Unknown prize funding.")

def platform_fee_bps() -> int:
    return PLATFORM_FEE_PERCENT * 100

async def has_money(event_id, db: AsyncSession) -> bool:
    """
    Whether money has ever moved for the event: its ledger accounts open on the first movement.
    """

    return (await db.execute(select(LedgerAccounts.id).where(LedgerAccounts.event_id == event_id).limit(1))).first() is not None

async def check_settings_editable(event: BracketEvents, db: AsyncSession) -> None:
    """
    Money settings change only before anyone signs up and before any money moves. After that the
    host cancels the event (everyone's refunded) and posts a new one.
    """

    signed_up = (await db.execute(select(BracketEntries.id).where(BracketEntries.event_id == event.id).limit(1))).first()
    if signed_up or await has_money(event.id, db):
        raise HTTPException(status_code=409, detail=(
            "The entry fee and prize settings are locked once anyone signs up or money is paid in. "
            "To change them, cancel the event (everyone is refunded) and post a new one."
        ))

# ---- accounts and wallets ----------------------------------------------------------------

async def event_accounts(event_id, db: AsyncSession) -> dict[str, LedgerAccounts]:
    """
    { 'event_pool': account, 'event_guarantee': account }, opened if this is the first movement.
    """

    await db.execute(
        pg_insert(LedgerAccounts).values([{"type": t, "event_id": event_id} for t in EVENT_ACCOUNT_TYPES])
        .on_conflict_do_nothing(index_elements = ["event_id", "type"], index_where = text("event_id IS NOT NULL"))
    )
    rows = (await db.execute(
        select(LedgerAccounts).where(LedgerAccounts.event_id == event_id).execution_options(populate_existing = True)
    )).scalars().all()
    return {a.type: a for a in rows}

async def _event_balances(event_id, db: AsyncSession) -> dict[str, int]:
    rows = (await db.execute(
        select(LedgerAccounts.type, LedgerAccounts.balance_cents).where(LedgerAccounts.event_id == event_id)
    )).all()
    return dict(rows)

async def _lock_wallets(account_ids, db: AsyncSession) -> dict[str, Wallets]:
    """
    The accounts' wallets, row-locked in id order (a fixed order, so two transactions locking
    overlapping wallets can't deadlock). Keyed by account id as a string.
    """

    ids = {str(a) for a in account_ids if a}
    if not ids:
        return {}
    rows = (await db.execute(
        select(Wallets).where(Wallets.account_id.in_(ids)).order_by(Wallets.id).with_for_update()
        .execution_options(populate_existing = True)
    )).scalars().all()
    return {str(w.account_id): w for w in rows}

async def _spendable(wallet: Wallets, db: AsyncSession) -> int:
    """
    What a wallet can pay right now: nothing while frozen, otherwise both buckets.
    """

    if wallet.frozen_at is not None:
        return 0
    accounts = await ledger.wallet_accounts(wallet.id, db)
    return sum(max(a.balance_cents, 0) for a in accounts.values())

def auto_pay_on(wallet: Wallets) -> bool:
    return wallet.auto_pay_until is not None and wallet.auto_pay_until > _now()

async def _take(wallet: Wallets, cents: int, into: LedgerAccounts, kind: str, db: AsyncSession, **link) -> None:
    """
    Moves `cents` from a wallet (Deposited Funds first, then Earnings) into an event account.
    """

    accounts = await ledger.wallet_accounts(wallet.id, db)
    await ledger.post(db, kind, ledger.take_from_buckets(accounts, cents) + [(into.id, cents)], **link)

async def _reverse(original: LedgerTransactions, kind: str, db: AsyncSession, **link) -> None:
    """
    Posts the exact opposite of a transaction: every bucket gets back what came out of it.
    """

    lines = (await db.execute(
        select(LedgerLines.ledger_account_id, LedgerLines.amount_cents).where(LedgerLines.transaction_id == original.id)
    )).all()
    await ledger.post(db, kind, [(account_id, -cents) for account_id, cents in lines], **link)

# ---- entry fees --------------------------------------------------------------------------

@dataclass
class Charge:
    wallet: Wallets
    covers: str  # 'team' or 'player'
    profile_id: uuid.UUID | None
    cents: int

async def plan_charges(event: BracketEvents, pay_mode: str | None, user_id: str, players: list[Profiles], db: AsyncSession) -> list[Charge]:
    """
    Who pays what for an entry (or for the players newly picked into one), checked before
    anything is written. Locks the payers' wallets. Pay for Team: the signed in captain pays
    fee x team size. Each Pays Own: every player pays the fee from their own wallet, which needs
    Auto-Pay on unless it's the signed in account's own. Errors name the players who can't pay,
    never anyone's balance.
    """

    fee = event.entry_fee_cents
    if not fee:
        return []
    if pay_mode == "team":
        wallet = (await _lock_wallets([user_id], db)).get(str(user_id))
        cents = fee * event.team_size
        if wallet is None:
            raise HTTPException(status_code=409, detail="Make a player profile first: your wallet comes with it.")
        if wallet.frozen_at is not None:
            raise HTTPException(status_code=409, detail="Your wallet is frozen after a disputed deposit, so it can't pay entry fees. Contact uSync support.")
        if await _spendable(wallet, db) < cents:
            raise HTTPException(status_code=409, detail=f"Your wallet doesn't have enough to pay the team's {money(cents)} entry. Add funds, then sign up.")
        return [Charge(wallet, "team", None, cents)]
    if pay_mode != "each":
        raise HTTPException(status_code=422, detail="Choose who pays the entry fee: you for the whole team, or each player.")

    wallets = await _lock_wallets([p.account_id for p in players], db)
    needed: dict[str, int] = {}
    for p in players:
        needed[str(p.account_id)] = needed.get(str(p.account_id), 0) + fee
    no_auto_pay, short = [], []
    for p in players:
        wallet = wallets.get(str(p.account_id))
        if wallet is None:
            short.append(p.username)
        elif str(p.account_id) != str(user_id) and not auto_pay_on(wallet):
            no_auto_pay.append(p.username)
        elif await _spendable(wallet, db) < needed[str(p.account_id)]:
            short.append(p.username)
    problems = []
    if no_auto_pay:
        problems.append(f"{_names(no_auto_pay)} {'needs' if len(set(no_auto_pay)) == 1 else 'need'} to turn on Auto-Pay")
    if short:
        problems.append(f"{_names(short)} {'doesn' if len(set(short)) == 1 else 'don'}'t have enough in their wallet for the {money(fee)} entry fee")
    if problems:
        raise HTTPException(status_code=409, detail="Can't take everyone's entry fee: " + "; ".join(problems) + ".")
    return [Charge(wallets[str(p.account_id)], "player", p.id, fee) for p in players]

async def apply_charges(event: BracketEvents, entry: BracketEntries, charges: list[Charge], db: AsyncSession) -> None:
    """
    Takes the planned entry fees: an entry_payments row and an 'entry_fee' ledger transaction
    each. A unique violation here (the same player or entry paid twice) aborts the whole sign-up.
    """

    if not charges:
        return
    pool = (await event_accounts(event.id, db))[POOL]
    for c in charges:
        payment = EntryPayments(id = uuid.uuid4(), event_id = event.id, entry_id = entry.id, profile_id = c.profile_id,
                                payer_wallet_id = c.wallet.id, covers = c.covers, amount_cents = c.cents, status = "paid")
        db.add(payment)
        await db.flush()
        await _take(c.wallet, c.cents, pool, "entry_fee", db, entry_payment_id = payment.id, event_id = event.id)
    # A receipt for each payer, with what they paid.
    paid: dict = {}
    for c in charges:
        paid[c.wallet.account_id] = paid.get(c.wallet.account_id, 0) + c.cents
    for account_id, cents in paid.items():
        notify(db, [account_id], "entry_fee_paid", {
            "event_id": event.public_id, "event_name": event.name, "team_name": entry.name, "amount_cents": cents,
        })

async def refund_payment(payment_id, event: BracketEvents, db: AsyncSession) -> int:
    """
    Refunds one payment if it's still paid; returns the cents refunded (0 if it already was).
    """

    payment = (await db.execute(
        select(EntryPayments).where(EntryPayments.id == payment_id).with_for_update().execution_options(populate_existing = True)
    )).scalars().first()
    if payment is None or payment.status != "paid":
        return 0
    original = (await db.execute(
        select(LedgerTransactions).where(LedgerTransactions.entry_payment_id == payment.id).where(LedgerTransactions.kind == "entry_fee")
    )).scalars().one()
    await _reverse(original, "entry_refund", db, entry_payment_id = payment.id, event_id = event.id)
    payment.status, payment.refunded_at = "refunded", _now()
    await db.flush()
    account_id = (await db.execute(select(Wallets.account_id).where(Wallets.id == payment.payer_wallet_id))).scalar()
    notify(db, [account_id], "entry_refunded", {"event_id": event.public_id, "event_name": event.name, "amount_cents": payment.amount_cents})
    return payment.amount_cents

async def _paid_ids(db: AsyncSession, *conditions) -> list:
    return list((await db.execute(
        select(EntryPayments.id).where(EntryPayments.status == "paid").where(*conditions).order_by(EntryPayments.id)
    )).scalars().all())

async def refund_entry(event: BracketEvents, entry: BracketEntries, db: AsyncSession) -> int:
    """
    Everything paid for an entry goes back (it's leaving the event).
    """

    total = 0
    for payment_id in await _paid_ids(db, EntryPayments.entry_id == entry.id):
        total += await refund_payment(payment_id, event, db)
    return total

async def refund_unpicked(event: BracketEvents, entry: BracketEntries, db: AsyncSession) -> int:
    """
    Each Pays Own: a player who's no longer picked (swapped out, left the team, deleted their
    profile) gets their entry fee back. Pay for Team's one payment covers whoever plays.
    """

    if entry.pay_mode != "each":
        return 0
    picked = select(BracketEntryPlayers.profile_id).where(BracketEntryPlayers.entry_id == entry.id) \
        .where(BracketEntryPlayers.profile_id.is_not(None))
    total = 0
    for payment_id in await _paid_ids(db, EntryPayments.entry_id == entry.id, EntryPayments.covers == "player",
                                      or_(EntryPayments.profile_id.is_(None), EntryPayments.profile_id.not_in(picked))):
        total += await refund_payment(payment_id, event, db)
    return total

async def unpaid_players(entry: BracketEntries, players: list[Profiles], db: AsyncSession) -> list[Profiles]:
    """
    Each Pays Own: the picked players who haven't paid for this entry yet.
    """

    if entry.pay_mode != "each":
        return []
    paid = set((await db.execute(
        select(EntryPayments.profile_id).where(EntryPayments.entry_id == entry.id).where(EntryPayments.status == "paid")
    )).scalars().all())
    return [p for p in players if p.id not in paid]

# ---- guaranteed prizes -------------------------------------------------------------------

async def _guarantee_txn(event: BracketEvents, kind: str, db: AsyncSession) -> LedgerTransactions | None:
    return (await db.execute(
        select(LedgerTransactions).where(LedgerTransactions.event_id == event.id).where(LedgerTransactions.kind == kind)
    )).scalars().first()

async def host_account(event: BracketEvents, db: AsyncSession):
    if event.host_profile_id is None:
        return None
    return (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()

async def fund_guarantee(event: BracketEvents, db: AsyncSession) -> None:
    """
    A guaranteed event's prize moves from the host's wallet into the event when sign-ups open.
    Once per event (unique per event and kind); doing it again is a no-op.
    """

    if event.prize_funding != "guaranteed" or await _guarantee_txn(event, "guarantee_fund", db):
        return
    account_id = await host_account(event, db)
    wallet = (await _lock_wallets([account_id], db)).get(str(account_id))
    cents = event.guarantee_cents
    if wallet is None or wallet.frozen_at is not None or await _spendable(wallet, db) < cents:
        raise HTTPException(status_code=409, detail=(
            f"Sign-ups open once your {money(cents)} guaranteed prize is paid in, and your wallet doesn't have enough. "
            "Add funds, then open sign-ups."
        ))
    held = (await event_accounts(event.id, db))[GUARANTEE]
    await _take(wallet, cents, held, "guarantee_fund", db, event_id = event.id)

async def refund_guarantee(event: BracketEvents, db: AsyncSession) -> int:
    funded = await _guarantee_txn(event, "guarantee_fund", db)
    if funded is None or await _guarantee_txn(event, "guarantee_refund", db):
        return 0
    await _reverse(funded, "guarantee_refund", db, event_id = event.id)
    return event.guarantee_cents or 0

# ---- cancelling ----------------------------------------------------------------------------

async def cancel(event: BracketEvents, reason: str, db: AsyncSession) -> None:
    """
    Calls the event off: every paid entry fee and the guarantee go back to whoever paid them,
    sign-ups close, and everyone entered and running it is told. The event stays (cancelled). A
    second call does nothing. The caller has locked the event and commits.
    """

    if event.cancelled_at is not None:
        return
    for payment_id in await _paid_ids(db, EntryPayments.event_id == event.id):
        await refund_payment(payment_id, event, db)
    await refund_guarantee(event, db)
    left = {t: c for t, c in (await _event_balances(event.id, db)).items() if c}
    if left:
        # Only possible if money moved outside this module: stop rather than strand it.
        raise RuntimeError(f"Event {event.id} still holds money after cancelling: {left}")
    event.cancelled_at, event.cancel_reason, event.registration_open = _now(), reason, False
    entries = await access.entry_accounts(await access.entries_of(event.id, db), db)
    people = set().union(*entries.values()) if entries else set()
    notify(db, people | set(await access.manager_accounts(event, db)), "event_cancelled", {
        "event_id": event.public_id, "event_name": event.name, "reason": reason,
    })

async def auto_cancel_due(db: AsyncSession) -> int:
    """
    The sweeper: events with money whose start time has come with fewer signed up than their
    minimum (and no published bracket) are cancelled and refunded. Locks each event with SKIP
    LOCKED, so it never waits on (or fights) a request working on the same event.
    """

    published = select(Brackets.id).where(Brackets.event_id == BracketEvents.id).where(Brackets.status != "draft")
    due = (await db.execute(
        select(BracketEvents)
        .where(BracketEvents.prize_funding.is_not(None)).where(BracketEvents.cancelled_at.is_(None))
        .where(BracketEvents.min_entrants.is_not(None)).where(BracketEvents.starts_at <= _now())
        .where(~exists(published))
        .order_by(BracketEvents.starts_at).limit(AUTO_CANCEL_BATCH)
        .with_for_update(skip_locked = True)
    )).scalars().all()
    cancelled = 0
    for event in due:
        signed_up = (await db.execute(
            select(func.count()).select_from(BracketEntries).where(BracketEntries.event_id == event.id).where(BracketEntries.status == "signed_up")
        )).scalar() or 0
        if signed_up < event.min_entrants:
            await cancel(event, "min_entrants", db)
            cancelled += 1
    return cancelled

# ---- reading -------------------------------------------------------------------------------

async def event_money_out(event: BracketEvents, db: AsyncSession) -> dict:
    balances = await _event_balances(event.id, db) if event.prize_funding else {}
    funded = bool(event.prize_funding == "guaranteed" and await _guarantee_txn(event, "guarantee_fund", db))
    return {
        "entryFeeCents": event.entry_fee_cents,
        "prizeFunding": event.prize_funding,
        "guaranteeCents": event.guarantee_cents,
        "guaranteeFunded": funded,
        "hostCutBps": event.host_cut_bps,
        "payoutStructure": event.payout_structure,
        "platformFeeBps": event.platform_fee_bps,
        "poolCents": balances.get(POOL, 0),
        "cancelledAt": event.cancelled_at.isoformat() if event.cancelled_at else None,
        "cancelReason": event.cancel_reason,
    }

async def entry_payments_out(entry_ids, db: AsyncSession) -> dict:
    """
    { entry id: [profile ids who've paid (Each Pays Own)] or 'team' }, for showing who's paid.
    """

    out: dict = {}
    if not entry_ids:
        return out
    for entry_id, covers, profile_id in (await db.execute(
        select(EntryPayments.entry_id, EntryPayments.covers, EntryPayments.profile_id)
        .where(EntryPayments.entry_id.in_(entry_ids)).where(EntryPayments.status == "paid")
    )).all():
        if covers == "team":
            out[entry_id] = "team"
        elif out.get(entry_id) != "team":
            out.setdefault(entry_id, []).append(profile_id)
    return out

# ---- deleting profiles and accounts ---------------------------------------------------------

def _unfinished():
    complete = select(Brackets.id).where(Brackets.event_id == BracketEvents.id).where(Brackets.status == "complete")
    return and_(BracketEvents.cancelled_at.is_(None), ~exists(complete))

async def check_hosts_can_go(profile_ids, db: AsyncSession) -> None:
    """
    A host profile can't be deleted while any event it hosts hasn't finished or been cancelled.
    """

    names = (await db.execute(
        select(BracketEvents.name).where(BracketEvents.host_profile_id.in_(profile_ids)).where(_unfinished()).limit(5)
    )).scalars().all()
    if names:
        raise HTTPException(status_code=409, detail=(
            f"You're hosting events that haven't finished: {_names(list(names))}. Finish or cancel them first."
        ))

async def delete_free_hosted_events(profile_ids, db: AsyncSession) -> None:
    """
    Events the profiles host that never held money go with them (as they always have); events
    that held money stay, hostless, so their ledger history stays whole.
    """

    held_money = select(LedgerAccounts.id).where(LedgerAccounts.event_id == BracketEvents.id)
    await db.execute(delete(BracketEvents).where(BracketEvents.host_profile_id.in_(profile_ids)).where(~exists(held_money)))
    await db.flush()

async def check_players_can_go(profile_ids, db: AsyncSession) -> None:
    """
    A profile playing in an event with money whose bracket is live (rosters locked) can't be
    deleted until the event finishes: its share of a prize would have nowhere to go.
    """

    live = select(Brackets.id).where(Brackets.event_id == BracketEvents.id).where(Brackets.status != "draft")
    names = (await db.execute(
        select(BracketEvents.name).join(BracketEntryPlayers, BracketEntryPlayers.event_id == BracketEvents.id)
        .where(BracketEntryPlayers.profile_id.in_(profile_ids)).where(BracketEvents.prize_funding.is_not(None))
        .where(_unfinished()).where(exists(live)).limit(5)
    )).scalars().all()
    if names:
        raise HTTPException(status_code=409, detail=(
            f"You're playing in events with prize money that haven't finished: {_names(list(names))}. Wait until they're done."
        ))

async def check_account_can_go(user_id: str, db: AsyncSession) -> None:
    """
    Before an account is deleted: nothing it hosts is unfinished, none of its profiles plays in a
    live event with money, and it has no entry fee paid into an event that hasn't finished or been
    cancelled (withdraw from it first; the refund then has to be spent or withdrawn too).
    """

    profile_ids = set((await db.execute(select(Profiles.id).where(Profiles.account_id == user_id))).scalars().all())
    if profile_ids:
        await check_hosts_can_go(profile_ids, db)
        await check_players_can_go(profile_ids, db)
    names = (await db.execute(
        select(BracketEvents.name).join(EntryPayments, EntryPayments.event_id == BracketEvents.id)
        .join(Wallets, Wallets.id == EntryPayments.payer_wallet_id)
        .where(Wallets.account_id == user_id).where(EntryPayments.status == "paid").where(_unfinished()).limit(5)
    )).scalars().all()
    if names:
        raise HTTPException(status_code=409, detail=(
            f"You've paid entry fees for events that haven't finished: {_names(list(names))}. "
            "Withdraw from them (you'll be refunded) or wait until they're done."
        ))

# ---- Auto-Pay ------------------------------------------------------------------------------

async def set_auto_pay(user_id: str, days: int, db: AsyncSession) -> datetime | None:
    """
    Turns Auto-Pay on for 7, 30 or 90 days from now, or off (0).
    """

    if days and days not in AUTO_PAY_DAYS:
        raise HTTPException(status_code=422, detail="Auto-Pay lasts 7, 30 or 90 days.")
    wallet = await ledger.wallet_of(user_id, db, lock = True)
    wallet.auto_pay_until = _now() + timedelta(days = days) if days else None
    await db.commit()
    return wallet.auto_pay_until
