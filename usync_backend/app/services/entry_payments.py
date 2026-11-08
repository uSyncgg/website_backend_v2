import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.teams import TeamMembers
from app.models.brackets import BracketEvents, BracketEntries, Notifications
from app.models.wallet import BracketEntryPayments
from app.services import wallet
from app.services.notifications import notify

# Paying for paid events. Every player in an entry has their own share (the pass price + the 5%
# uSync fee), recorded in bracket_entry_payments:
#
# - Whoever signs a team up pays their own share and can cover any teammates they pick; the rest
#   are charged to their own balance. A solo entry is just the one share.
# - An entry only counts as signed up once every player's share is paid (entry.fully_paid). A
#   teammate without enough in their balance leaves the team pending and gets a notification;
#   the share is taken as soon as they have it (a deposit, or a roster change, retries it).
# - Leaving (or being removed from) an event before registration closes gives the money back to
#   whoever paid. Once registration is closed a player who leaves forfeits it - the host keeps it -
#   unless the host removes them or cancels the event, which always refunds.

PASS_FEE_PERCENT = 5

@dataclass
class Member:
    profile_id: uuid.UUID
    username: str
    account_id: uuid.UUID

def _now() -> datetime:
    return datetime.now(timezone.utc)

def is_published(event: BracketEvents) -> bool:
    return bool(event.bracket and event.bracket.get("published"))

def refundable(event: BracketEvents) -> bool:
    """
    Whether someone leaving the event now gets their money back.
    """

    return bool(event.registration_open) and not is_published(event) and event.cancelled_at is None

def entry_cost(event: BracketEvents, pass_name: str | None) -> tuple[int, int, str] | None:
    """
    (price_cents, fee_cents, pass name) for one player's spot, or None when the event is free.
    Events with uSync payments turned on charge the chosen pass's price plus the 5% uSync fee.
    """

    payments = event.payments or {}
    tiers = payments.get("tiers") or []
    if not payments.get("enabled") or not tiers:
        return None

    tier = next((t for t in tiers if t.get("name") == pass_name), None) if pass_name else tiers[0]
    if tier is None:
        raise HTTPException(status_code = 422, detail = "Pick one of the event's passes.")

    price = wallet._cents(Decimal(str(tier.get("price") or 0)))
    if price <= 0:
        return None

    return price, wallet.fee_for(price, PASS_FEE_PERCENT), tier.get("name", "")

async def entry_members(event: BracketEvents, entry: BracketEntries, db: AsyncSession) -> list[Member]:
    """
    The players who each owe a share: the player for a solo entry, or the team's first team-size
    players (captains first, then by join date).
    """

    if entry.team_id is None:
        query = select(Profiles.id, Profiles.username, Profiles.account_id).where(Profiles.id == entry.profile_id)
    else:
        query = (
            select(Profiles.id, Profiles.username, Profiles.account_id)
            .join(TeamMembers, TeamMembers.profile_id == Profiles.id)
            .where(TeamMembers.team_id == entry.team_id)
            .order_by(TeamMembers.role, TeamMembers.joined_at)
        )
    rows = (await db.execute(query)).all()
    return [Member(*r) for r in rows][:event.team_size]

async def active_payments(entry_id, db: AsyncSession) -> list[BracketEntryPayments]:
    return list((await db.execute(
        select(BracketEntryPayments).where(BracketEntryPayments.entry_id == entry_id).where(BracketEntryPayments.status == "paid")
    )).scalars().all())

async def _notify_once(db: AsyncSession, account_id, type_: str, data: dict) -> None:
    """
    Like notify, but skips it when the same unread notification is already waiting.
    """

    existing = (await db.execute(
        select(Notifications.id)
        .where(Notifications.account_id == account_id).where(Notifications.type == type_).where(Notifications.read.is_(False))
        .where(Notifications.data["event_id"].astext == data["event_id"]).limit(1)
    )).first()
    if not existing:
        notify(db, [account_id], type_, data)

async def release_payments(event: BracketEvents, payments: list[BracketEntryPayments], db: AsyncSession, *, refund: bool, reason: str) -> int:
    """
    Gives each payment back to whoever paid it (refund=True) or marks it forfeited. Payments that
    are already refunded are skipped. Doesn't commit. Returns the cents refunded.
    """

    returned_by_payer: dict = {}
    for p in payments:
        if p.status == "refunded" or (p.status == "forfeited" and not refund):
            continue
        if not refund:
            p.status = "forfeited"
            continue
        total = p.amount_cents + p.fee_cents
        await wallet.credit(
            p.payer_account_id, total, "entry_refund", f"Refund: {event.name}", db,
            fee_cents = p.fee_cents, event_public_id = event.public_id, entry_id = p.entry_id, related_id = p.charge_tx_id,
        )
        p.status = "refunded"
        p.refunded_at = _now()
        returned_by_payer[p.payer_account_id] = returned_by_payer.get(p.payer_account_id, 0) + total

    for account_id, cents in returned_by_payer.items():
        notify(db, [account_id], "entry_refunded", {"event_id": event.public_id, "event_name": event.name, "amount_cents": cents, "reason": reason})
    return sum(returned_by_payer.values())

async def release_entry(event: BracketEvents, entry: BracketEntries, db: AsyncSession, *, refund: bool, reason: str) -> None:
    await release_payments(event, await active_payments(entry.id, db), db, refund = refund, reason = reason)

async def refund_event(event: BracketEvents, db: AsyncSession, reason: str) -> int:
    """
    Gives back everything paid into the event - including money forfeited by players who left
    after registration closed - for a cancelled or deleted event. Doesn't commit.
    """

    payments = list((await db.execute(
        select(BracketEntryPayments).where(BracketEntryPayments.event_id == event.id).where(BracketEntryPayments.status.in_(("paid", "forfeited")))
    )).scalars().all())
    return await release_payments(event, payments, db, refund = True, reason = reason)

async def settle_entry(
    event: BracketEvents, entry: BracketEntries, cost: tuple[int, int, str], db: AsyncSession, *,
    payer_user_id=None, cover: set[str] | None = None, quiet: bool = False,
) -> list[Member]:
    """
    Brings an entry's payments up to date and sets entry.fully_paid. Doesn't commit.

    - Players who left the team get their share back while registration is open, or forfeit it.
    - The signed in account (payer_user_id) pays its own share and the shares of the usernames in
      `cover`; if it can't afford those the sign-up fails with a 402 and nothing is charged.
    - Everyone else's share comes from their own balance when they have it. Those who don't are
      returned as unpaid (and told, unless quiet).
    """

    price, fee, pass_name = cost
    total = price + fee
    members = await entry_members(event, entry, db)
    by_profile = {m.profile_id: m for m in members}
    paid = await active_payments(entry.id, db)

    left = [p for p in paid if p.member_profile_id not in by_profile]
    if left:
        await release_payments(event, left, db, refund = refundable(event), reason = "A player left the team before registration closed.")
    paid_profiles = {p.member_profile_id for p in paid if p.member_profile_id in by_profile}

    cover = {c.lower() for c in (cover or set())}
    unknown = cover - {m.username.lower() for m in members}
    if unknown:
        raise HTTPException(status_code = 422, detail=f"{', '.join(sorted(unknown))} isn't on this team.")

    def payer_of(m: Member):
        if payer_user_id and (m.account_id == payer_user_id or str(m.account_id) == str(payer_user_id) or m.username.lower() in cover):
            return payer_user_id
        return None

    owed = [m for m in members if m.profile_id not in paid_profiles]
    mine = [m for m in owed if payer_of(m)]
    if mine:
        need = total * len(mine)
        have = await wallet.balance_of(payer_user_id, db)
        if have < need:
            players = f"{len(mine)} players" if len(mine) > 1 else "your spot"
            raise HTTPException(
                status_code = 402,
                detail = f"Entering {event.name} for {players} costs {wallet.money(need)} ({wallet.money(price)} + {wallet.money(fee)} fee each) "
                         f"and you have {wallet.money(have)}. Add funds on the Bank page.",
            )

    unpaid: list[Member] = []
    for m in owed:
        payer = payer_of(m) or m.account_id
        try:
            tx = await wallet.debit(
                payer, total, "entry_fee", f"Entry: {event.name}" + (f" ({pass_name})" if pass_name else "") + ("" if str(payer) == str(m.account_id) else f" - for {m.username}"),
                db, fee_cents = fee, event_public_id = event.public_id, entry_id = entry.id,
            )
        except wallet.InsufficientFunds:
            if payer_of(m):
                raise HTTPException(status_code = 402, detail = "Your balance changed while signing up. Add funds on the Bank page and try again.")
            unpaid.append(m)
            continue

        db.add(BracketEntryPayments(
            id = uuid.uuid4(), event_id = event.id, entry_id = entry.id, member_profile_id = m.profile_id, member_username = m.username,
            payer_account_id = payer, amount_cents = price, fee_cents = fee, status = "paid", charge_tx_id = tx.id,
        ))
        if str(payer) != str(m.account_id):
            payer_name = next((x.username for x in members if str(x.account_id) == str(payer)), None)
            notify(db, [m.account_id], "entry_paid_for_you", {"event_id": event.public_id, "event_name": event.name, "payer": payer_name, "amount_cents": total})

    entry.fully_paid = not unpaid
    if unpaid and not quiet:
        for m in unpaid:
            await _notify_once(db, m.account_id, "entry_payment_due", {
                "event_id": event.public_id, "event_name": event.name, "team_name": entry.name, "amount_cents": total,
            })
    return unpaid

async def event_revenue(event_id, db: AsyncSession) -> dict:
    """
    What players have paid into the event: pass prices kept (paid + forfeited), the 5% uSync fees
    charged on top of them, what's been refunded, and how many player shares are paid.
    """

    row = (await db.execute(
        select(
            func.coalesce(func.sum(BracketEntryPayments.amount_cents).filter(BracketEntryPayments.status.in_(("paid", "forfeited"))), 0),
            func.coalesce(func.sum(BracketEntryPayments.fee_cents).filter(BracketEntryPayments.status.in_(("paid", "forfeited"))), 0),
            func.coalesce(func.sum(BracketEntryPayments.amount_cents + BracketEntryPayments.fee_cents).filter(BracketEntryPayments.status == "refunded"), 0),
            func.count().filter(BracketEntryPayments.status == "paid"),
            func.count().filter(BracketEntryPayments.status == "forfeited"),
        ).where(BracketEntryPayments.event_id == event_id)
    )).one()
    return {"collectedCents": int(row[0]), "feesCents": int(row[1]), "refundedCents": int(row[2]), "paidPlayers": row[3], "forfeitedPlayers": row[4]}
