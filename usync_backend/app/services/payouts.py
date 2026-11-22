import logging
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from sqlalchemy import select, exists, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.brackets import BracketEvents, BracketEntryPlayers, Brackets, EventIssues, EventStandings
from app.models.wallet import LedgerTransactions
from app.services import ledger
from app.services import event_money
from app.services.bracket_sync import PAYOUT_DELAY
from app.services.notifications import notify
from app.services.wallet import _alert

logger = logging.getLogger(__name__)

# Event payouts (GLOSSARY.md: Event Pool, Payout Structure). Once an event with money has had its
# final standings for 24 hours (bracket_sync.record_standings starts the clock, and every
# correction restarts it) and nothing is open in its admin queue, the sweeper pays it out:
#
#   pool P (entry fees) and guarantee G (a guaranteed prize) are emptied, as ONE ledger transaction
#   ('event_payout', unique per event in Postgres) with these lines:
#   - uSync's platform fee: P x platform_fee_bps, rounded half up (never taken from a guarantee);
#   - the host's Earnings: entry-funded, (P - fee) x host_cut_bps rounded half up;
#                          guaranteed, all of P - fee (the host keeps the entry fees);
#   - each player's Earnings: their share of the prize (entry-funded: P - fee - host cut;
#                             guaranteed: G).
#
# The prize is split by the payout structure (basis points per place). Entrants sharing a
# placement split the places they cover equally (two 3rds share 3rd + 4th). Places nobody reached
# (a 16-place structure with 6 entrants) are spread over the reached ones in proportion. A team's
# share is split equally among its locked roster. Leftover cents go to the best placed entry first,
# then within a team by profile id: uSync never keeps a rounding penny.
#
# Safety: the event row is locked (SKIP LOCKED, so the sweeper never waits on a request), and
# paid_out_at plus the unique event_payout transaction make a second payout impossible. If any
# winner can't be paid (no profile or wallet) the whole payout is held, uSync is emailed, and it's
# retried 24 hours later; nothing is paid partly.

PAYOUT_BATCH = 20

class PayoutHeld(Exception):
    """The event can't be paid out as it stands; uSync is told and it's retried later."""

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _bps(cents: int, bps: int) -> int:
    """cents x bps / 10,000, rounded half up."""
    return (cents * bps + 5_000) // 10_000

def split_even(cents: int, keys: list) -> dict:
    """
    `cents` split equally over `keys` in the order given; the leftover cents go one each to the
    first keys.
    """

    if not keys:
        return {}
    each, left = divmod(cents, len(keys))
    return {k: each + (1 if i < left else 0) for i, k in enumerate(keys)}

def split_prize(prize: int, shares: list[int], standings: list[tuple]) -> dict:
    """
    {entry id: cents} for every entrant (0 for those outside the paid places). `shares` is the
    payout structure in basis points, best place first; `standings` is [(entry id, placement)].
    """

    paid_places = min(len(shares), len(standings))
    reached = shares[:paid_places]
    total = sum(reached)
    by_place: dict[int, list] = {}
    for entry_id, placement in standings:
        by_place.setdefault(placement, []).append(entry_id)

    weights: dict = {}
    for placement, entries in by_place.items():
        covered = sum(reached[i] for i in range(placement - 1, placement - 1 + len(entries)) if i < paid_places)
        for entry_id in entries:
            weights[entry_id] = Fraction(covered, len(entries))

    out = {entry_id: int(prize * w / total) if total else 0 for entry_id, w in weights.items()}
    # Leftover cents: best placed first (ties by entry id), only to entries in the paid places.
    winners = sorted((e for e, w in weights.items() if w), key = lambda e: (dict(standings)[e], str(e)))
    for i in range(prize - sum(out.values())):
        out[winners[i % len(winners)]] += 1
    return out

@dataclass
class Plan:
    fee: int
    host: int
    pool: int
    guarantee: int
    prizes: dict          # {entry id: cents}
    players: dict         # {account id (str): cents}
    host_account: str | None
    placements: dict      # {entry id: placement}
    player_places: dict   # {account id (str): best placement they were paid for}

async def plan_payout(event: BracketEvents, db: AsyncSession) -> Plan:
    """
    Works out who gets what, without moving anything. Raises PayoutHeld if a winner or the host
    can't be paid.
    """

    balances = await event_money._event_balances(event.id, db)
    pool, guarantee = balances.get(event_money.POOL, 0), balances.get(event_money.GUARANTEE, 0)
    fee = _bps(pool, event.platform_fee_bps)
    if event.prize_funding == "entry_funded":
        host = _bps(pool - fee, event.host_cut_bps or 0)
        prize = pool - fee - host
    else:
        host = pool - fee
        prize = guarantee

    standings = (await db.execute(
        select(EventStandings.entry_id, EventStandings.placement).where(EventStandings.event_id == event.id)
    )).all()
    if not standings:
        raise PayoutHeld("No final standings are stored.")
    standings = [tuple(r) for r in standings]
    prizes = split_prize(prize, list(event.payout_structure or []), standings)
    placements = dict(standings)

    winners = [e for e, cents in prizes.items() if cents]
    rosters: dict = {e: [] for e in winners}
    for entry_id, profile_id, account_id in (await db.execute(
        select(BracketEntryPlayers.entry_id, BracketEntryPlayers.profile_id, Profiles.account_id)
        .outerjoin(Profiles, Profiles.id == BracketEntryPlayers.profile_id)
        .where(BracketEntryPlayers.entry_id.in_(winners))
    )).all() if winners else []:
        rosters[entry_id].append((profile_id, account_id))

    players: dict = {}
    player_places: dict = {}
    for entry_id in sorted(winners, key = lambda e: (placements[e], str(e))):
        roster = rosters[entry_id]
        if not roster or any(p is None or a is None for p, a in roster):
            raise PayoutHeld(f"Entry {entry_id} (placement {placements[entry_id]}) has a player with no profile or account.")
        shares = split_even(prizes[entry_id], sorted(roster, key = lambda r: str(r[0])))
        for (_, account_id), cents in shares.items():
            players[str(account_id)] = players.get(str(account_id), 0) + cents
            player_places.setdefault(str(account_id), placements[entry_id])

    host_account = await event_money.host_account(event, db)
    if host and host_account is None:
        raise PayoutHeld("The host's profile is gone, so the host's share has nowhere to go.")
    return Plan(fee, host, pool, guarantee, prizes, players, str(host_account) if host_account else None, placements, player_places)

async def pay_out(event: BracketEvents, db: AsyncSession) -> Plan | None:
    """
    Pays the event out in one ledger transaction. The caller has locked the event row and
    commits. Returns None if it was already paid out.
    """

    if event.paid_out_at is not None or (await db.execute(
        select(LedgerTransactions.id).where(LedgerTransactions.event_id == event.id).where(LedgerTransactions.kind == "event_payout")
    )).first():
        return None
    plan = await plan_payout(event, db)
    event_accts = await event_money.event_accounts(event.id, db)
    revenue = await ledger.system_account("platform_revenue", db)

    payees = {a: c for a, c in plan.players.items() if c}
    if plan.host:
        payees[plan.host_account] = payees.get(plan.host_account, 0) + plan.host
    wallets = await event_money._lock_wallets(payees.keys(), db)
    missing = [a for a in payees if a not in wallets]
    if missing:
        raise PayoutHeld(f"No wallet for accounts {missing}.")

    lines = [(event_accts[event_money.POOL].id, -plan.pool), (event_accts[event_money.GUARANTEE].id, -plan.guarantee), (revenue.id, plan.fee)]
    for account_id, cents in payees.items():
        earnings = (await ledger.wallet_accounts(wallets[account_id].id, db))[ledger.EARNINGS]
        lines.append((earnings.id, cents))
    await ledger.post(db, "event_payout", lines, event_id = event.id)

    for entry_id, cents in plan.prizes.items():
        await db.execute(update(EventStandings).where(EventStandings.event_id == event.id)
                         .where(EventStandings.entry_id == entry_id).values(prize_cents = cents))
    event.paid_out_at = _now()

    base = {"event_id": event.public_id, "event_name": event.name}
    for account_id, cents in plan.players.items():
        if cents:
            notify(db, [account_id], "prize_paid", {**base, "amount_cents": cents, "placement": plan.player_places.get(account_id)})
    if plan.host:
        notify(db, [plan.host_account], "host_payout", {**base, "amount_cents": plan.host})
    return plan

async def pay_out_due(db: AsyncSession) -> tuple[int, list[tuple[str, str, str]]]:
    """
    The sweeper: pays out every event whose payout is due, whose bracket is complete and whose
    admin queue has nothing open or claimed. Each event in its own savepoint, so one held payout
    doesn't stop the rest; a held one is retried 24 hours later. Returns
    (paid, [(event public id, name, why held)]).
    The caller commits, then alerts uSync about the held ones.
    """

    complete = select(Brackets.id).where(Brackets.event_id == BracketEvents.id).where(Brackets.status == "complete")
    open_issue = select(EventIssues.id).where(EventIssues.event_id == BracketEvents.id).where(EventIssues.status.in_(("open", "claimed")))
    due = (await db.execute(
        select(BracketEvents)
        .where(BracketEvents.payout_due_at <= _now()).where(BracketEvents.paid_out_at.is_(None))
        .where(BracketEvents.prize_funding.is_not(None)).where(BracketEvents.cancelled_at.is_(None))
        .where(exists(complete)).where(~exists(open_issue))
        .order_by(BracketEvents.payout_due_at).limit(PAYOUT_BATCH)
        .with_for_update(skip_locked = True)
    )).scalars().all()
    paid, held = 0, []
    for event in due:
        public_id, name = event.public_id, event.name
        try:
            async with db.begin_nested():
                if await pay_out(event, db):
                    paid += 1
        except PayoutHeld as why:
            event.payout_due_at = _now() + PAYOUT_DELAY
            held.append((public_id, name, str(why)))
    await db.flush()
    return paid, held

async def alert_held(held: list[tuple[str, str, str]]) -> None:
    """Emails uSync about each held payout (after the sweep has committed)."""
    for public_id, name, why in held:
        logger.error("Payout held for event %s: %s", public_id, why)
        await _alert(f"Payout held: {name}", {"Event": public_id, "Why": why, "Retry": "in 24 hours"})
