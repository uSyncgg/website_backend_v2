import asyncio
import logging
import uuid

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import AsyncSessionLocal
from app.models.users import Profiles
from app.models.brackets import BracketEvents, BracketEntries
from app.models.wallet import BracketEntryPayments, PlayerEventResults
from app.services import bracket_engine as engine
from app.services import brackets as brk
from app.services import entry_payments as pay
from app.services import wallet
from app.services.notifications import notify

logger = logging.getLogger(__name__)

# Closing an event, paying it out and recording how everyone did.
#
# 1. The host cancels the event: everything paid in is refunded (cancel_event).
# 2. Otherwise, once the bracket is decided the host closes the event (close_event). That starts a
#    45 minute window in which results can still be fixed (reopen_event undoes the close).
# 3. When the window is over the payout job (run_payout_sweeper) settles the event once:
#    - Each podium place wins its prize, split evenly between the players on that entry and credited
#      to their balances. A place is either a fixed amount or a percent of the entry revenue; if the
#      prizes add up to more than was collected they're scaled down, so uSync never pays out more
#      than players paid in.
#    - The host gets the entry revenue minus the prizes paid out, less a 2.5% uSync fee on that profit.
#    - Every player in the bracket gets a row in player_event_results (placement, wins, losses,
#      earnings) - the numbers profile stats are built from.
# Revenue is the pass prices only; the 5% fee players pay on top of the price is uSync's.

PAYOUT_DELAY_MINUTES = 45
HOST_FEE_PERMILLE = 25  # 2.5% of the host's profit
SWEEP_INTERVAL_SECONDS = 60
PLACE_NAMES = {1: "1st", 2: "2nd", 3: "3rd"}

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None

# ---- the money -------------------------------------------------------------

def plan_prizes(prizes: list[dict], collected_cents: int, placed: list | None = None) -> tuple[list[tuple[int, int]], bool]:
    """
    [(place, cents)] for the prizes that will be paid, and whether they had to be scaled down.
    A prize is { place, amountCents } or { place, percent } (of the entry revenue). When `placed`
    (the podium, [1st, 2nd, 3rd]) is given, places nobody finished in aren't paid.
    """

    wanted = []
    for prize in sorted(prizes or [], key = lambda p: p["place"]):
        if placed is not None and (prize["place"] > len(placed) or not placed[prize["place"] - 1]):
            continue
        if prize.get("amountCents") is not None:
            cents = int(prize["amountCents"])
        else:
            cents = int(Decimal(collected_cents) * Decimal(str(prize.get("percent") or 0)) / 100)
        if cents > 0:
            wanted.append((prize["place"], cents))

    total = sum(c for _, c in wanted)
    if total <= collected_cents:
        return wanted, False
    return [(place, cents * collected_cents // total) for place, cents in wanted], True

def host_split(collected_cents: int, prizes_cents: int) -> dict:
    """
    The host's cut: entry revenue minus prizes, less uSync's 2.5% of that profit. Never negative.
    """

    profit = max(0, collected_cents - prizes_cents)
    fee = (profit * HOST_FEE_PERMILLE + 500) // 1000
    return {"hostProfitCents": profit, "usyncFeeCents": fee, "hostNetCents": profit - fee}

# ---- cancel, close, reopen -------------------------------------------------

async def cancel_event(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    Cancels the event and gives every player back everything they paid, fee included.
    """

    event = await brk._owner_event(user_id, public_id, db, lock = True)
    if event.cancelled_at:
        raise HTTPException(status_code = 409, detail = "This event is already cancelled.")
    if event.paid_out_at:
        raise HTTPException(status_code = 409, detail = "This event has already been paid out.")

    event.cancelled_at = _now()
    event.registration_open = False
    event.closed_at = None
    event.payout_due_at = None
    refunded = await pay.refund_event(event, db, "The event was cancelled.")

    entries = await brk._entries(event.id, db)
    accounts = await brk._entry_accounts(entries, db)
    notify(db, set().union(*accounts.values()) if accounts else set(), "event_cancelled", {
        "event_id": event.public_id, "event_name": event.name, "refunded": refunded > 0,
    })
    await db.commit()
    return await brk._event_out(event, user_id, db)

async def close_event(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    The host's "we're done": the winners are in. Payouts follow 45 minutes later.
    """

    event = await brk._host_event(user_id, public_id, db, lock = True)
    if event.cancelled_at:
        raise HTTPException(status_code = 409, detail = "This event was cancelled.")
    if event.closed_at:
        raise HTTPException(status_code = 409, detail = "This event is already closed.")
    if not brk._published(event):
        raise HTTPException(status_code = 409, detail = "Publish the bracket and play it out before closing the event.")

    built = brk._built(event)
    done, total = engine.progress(built)
    if not total or done < total or engine.placements(event.format, event.bracket["seeds"], built) is None:
        raise HTTPException(status_code = 409, detail = "Every match needs a result before the event can be closed.")

    event.closed_at = _now()
    event.payout_due_at = event.closed_at + timedelta(minutes = PAYOUT_DELAY_MINUTES)

    entries = await brk._entries(event.id, db)
    accounts = await brk._entry_accounts(entries, db)
    notify(db, set().union(*accounts.values()) if accounts else set(), "event_closed", {
        "event_id": event.public_id, "event_name": event.name, "payout_at": _iso(event.payout_due_at), "prizes": bool(event.prizes),
    })
    await db.commit()
    return await brk._event_out(event, user_id, db)

async def reopen_event(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    Takes back a close while the 45 minutes are still running, so results can be corrected.
    """

    event = await brk._host_event(user_id, public_id, db, lock = True)
    if event.paid_out_at:
        raise HTTPException(status_code = 409, detail = "This event has already been paid out.")
    if not event.closed_at:
        raise HTTPException(status_code = 409, detail = "This event isn't closed.")

    event.closed_at = None
    event.payout_due_at = None
    await db.commit()
    return await brk._event_out(event, user_id, db)

# ---- settling --------------------------------------------------------------

async def _result_members(event: BracketEvents, entry: BracketEntries, db: AsyncSession) -> list:
    """
    The players an entry's prize and result belong to: the ones who paid for the spot in a paid
    event (so a roster change after the bracket ended can't move the money), otherwise the entry's
    players now.
    """

    paid = await pay.active_payments(entry.id, db)
    members = await pay.entry_members(event, entry, db)
    if not paid:
        return members
    paid_ids = [p.member_profile_id for p in paid]
    rows = (await db.execute(select(Profiles.id, Profiles.username, Profiles.account_id).where(Profiles.id.in_(paid_ids)))).all()
    by_id = {r[0]: pay.Member(*r) for r in rows}
    return [by_id[i] for i in paid_ids if i in by_id] or members

async def settle_event(event: BracketEvents, db: AsyncSession) -> bool:
    """
    Pays out one closed event whose 45 minutes are up, once. Returns False when there was nothing
    to do (already paid, cancelled, or the results changed so it isn't decided any more and the
    close was undone). Commits.
    """

    if event.paid_out_at or event.cancelled_at or not event.closed_at:
        return False

    built = brk._built(event) if brk._published(event) else None
    placed = engine.placements(event.format, event.bracket["seeds"], built) if built else None
    if placed is None:
        event.closed_at = None
        event.payout_due_at = None
        notify(db, await brk._manager_accounts(event, db), "event_reopened", {"event_id": event.public_id, "event_name": event.name})
        await db.commit()
        return False

    revenue = await pay.event_revenue(event.id, db)
    collected = revenue["collectedCents"]
    prizes, scaled = plan_prizes(event.prizes, collected, placed)
    prize_by_place = dict(prizes)
    entries = {str(e.id): e for e in await brk._entries(event.id, db)}

    earnings: dict = {}  # profile id -> cents
    paid_out = 0
    for place, entry_id in enumerate(placed, start = 1):
        cents = prize_by_place.get(place)
        entry = entries.get(entry_id) if entry_id else None
        if not cents or not entry:
            continue
        members = await _result_members(event, entry, db)
        if not members:
            continue
        share, extra = divmod(cents, len(members))
        for i, m in enumerate(members):
            amount = share + (extra if i == 0 else 0)
            if amount <= 0:
                continue
            await wallet.credit(
                m.account_id, amount, "prize", f"Prize: {PLACE_NAMES.get(place, place)} place - {event.name}", db,
                event_public_id = event.public_id, entry_id = entry.id,
            )
            notify(db, [m.account_id], "prize_won", {"event_id": event.public_id, "event_name": event.name, "place": place, "amount_cents": amount})
            earnings[m.profile_id] = amount
            paid_out += amount

    # Everyone in the bracket gets a results row: placement, wins, losses and what they earned.
    for seed in event.bracket["seeds"]:
        entry = entries.get(seed)
        if not entry:
            continue
        wins, losses = engine.match_record(built, seed)
        place = placed.index(seed) + 1 if seed in placed else None
        for m in await _result_members(event, entry, db):
            db.add(PlayerEventResults(
                id = uuid.uuid4(), event_id = event.id, entry_id = entry.id, profile_id = m.profile_id,
                placement = place, wins = wins, losses = losses, earnings_cents = earnings.get(m.profile_id, 0),
            ))

    split = host_split(collected, paid_out)
    host_account = (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()
    if split["hostNetCents"] > 0 and host_account:
        await wallet.credit(
            host_account, split["hostNetCents"], "host_payout", f"Event revenue: {event.name}", db,
            fee_cents = split["usyncFeeCents"], event_public_id = event.public_id,
        )
        notify(db, [host_account], "host_payout", {
            "event_id": event.public_id, "event_name": event.name, "amount_cents": split["hostNetCents"], "collected_cents": collected,
        })

    event.paid_out_at = _now()
    event.payout_summary = {
        "collectedCents": collected, "prizesCents": paid_out, "prizesScaledDown": scaled, **split,
        "podium": [entry_id for entry_id in placed], "paidOutAt": _iso(event.paid_out_at),
    }
    await db.commit()
    return True

async def settle_due_events() -> int:
    """
    The payout job: settles every closed event whose 45 minutes are up. Each event is its own
    transaction, locked while it settles, so running this twice never pays anything twice.
    """

    async with AsyncSessionLocal() as db:
        due = (await db.execute(
            select(BracketEvents.id)
            .where(BracketEvents.payout_due_at <= _now()).where(BracketEvents.paid_out_at.is_(None)).where(BracketEvents.cancelled_at.is_(None))
            .limit(50)
        )).scalars().all()

    settled = 0
    for event_id in due:
        async with AsyncSessionLocal() as db:
            try:
                event = (await db.execute(select(BracketEvents).where(BracketEvents.id == event_id).with_for_update())).scalars().first()
                if event and await settle_event(event, db):
                    settled += 1
            except Exception:
                await db.rollback()
                logger.exception("Payout for event %s failed", event_id)
    return settled

async def run_payout_sweeper() -> None:
    """
    Background loop started in main.py's lifespan: pays out closed events and marks net 30
    withdrawals paid, once a minute.
    """

    while True:
        try:
            events = await settle_due_events()
            if events:
                logger.info("Payout job paid out %d event(s)", events)
            withdrawals = await wallet.settle_due_withdrawals()
            if withdrawals:
                logger.info("Payout job paid %d withdrawal(s)", withdrawals)
        except Exception:
            logger.exception("Payout job run failed")

        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

# ---- host view -------------------------------------------------------------

async def event_finances(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    The host's view of entry revenue: what's been collected, what the prizes will take, and what
    the host keeps after uSync's 2.5%. Projected until the event is paid out, then the final numbers.
    """

    event = await brk._host_event(user_id, public_id, db)
    revenue = await pay.event_revenue(event.id, db)
    built = brk._built(event) if brk._published(event) else None
    placed = engine.placements(event.format, event.bracket["seeds"], built) if built else None

    if event.paid_out_at:
        stage = "paid_out"
    elif event.cancelled_at:
        stage = "cancelled"
    elif event.closed_at:
        stage = "closed"
    else:
        stage = "collecting"

    prizes, scaled = plan_prizes(event.prizes, revenue["collectedCents"], placed)
    prizes_cents = sum(c for _, c in prizes)
    split = host_split(revenue["collectedCents"], prizes_cents)
    summary = event.payout_summary or {}

    return {
        "stage": stage,
        "final": bool(event.paid_out_at),
        **revenue,
        "prizesCents": summary.get("prizesCents", prizes_cents),
        "prizesScaledDown": summary.get("prizesScaledDown", scaled),
        "prizePlaces": [{"place": place, "cents": cents} for place, cents in prizes],
        "hostProfitCents": summary.get("hostProfitCents", split["hostProfitCents"]),
        "usyncFeeCents": summary.get("usyncFeeCents", split["usyncFeeCents"]),
        "hostNetCents": summary.get("hostNetCents", split["hostNetCents"]),
        "payoutDueAt": _iso(event.payout_due_at),
        "paidOutAt": _iso(event.paid_out_at),
        "hostFeePercent": HOST_FEE_PERMILLE / 10,
        "payoutDelayMinutes": PAYOUT_DELAY_MINUTES,
    }
