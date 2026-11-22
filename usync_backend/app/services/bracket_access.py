from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.teams import Teams, TeamMembers
from app.models.brackets import BracketEvents, BracketEntries, BracketEntryPlayers, BracketEventAdmins
from app.services.naming import account_profile_ids

# Who can see and do what in a bracket event, and the lookups every bracket service needs. This
# module only reads; it imports no other bracket service, so they can all import it.
#
# - Managers: the host's account (any of its profiles) and accepted admins. They run the event.
# - Staff: the host and every invited admin, accepted or not. Staff can't play in the event.
# - An entrant can be acted for (reporting, unregistering, picking players) by its team's current
#   owner or captain, or in a solo event also by the player - from the signed in account's own team role, never from
#   whoever happened to register the team.

my_profile_ids = account_profile_ids

async def event_by_public_id(public_id: str, db: AsyncSession, lock: bool = False, allow_cancelled: bool = False) -> BracketEvents:
    """
    The event, row-locked when lock = True. Every change to an event locks it first, so a locked
    read of a cancelled or paid-out event is refused here (409): both are read-only. Cancelling
    itself passes allow_cancelled so a repeat is a no-op (a paid-out event is refused even then).
    """

    query = select(BracketEvents).where(BracketEvents.public_id == public_id)
    if lock:
        query = query.with_for_update()
    event = (await db.execute(query)).scalars().first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    if lock and event.cancelled_at is not None and not allow_cancelled:
        raise HTTPException(status_code=409, detail="This event was cancelled.")
    if lock and event.paid_out_at is not None:
        raise HTTPException(status_code=409, detail="This event has paid out its prizes, so it can't be changed.")
    return event

async def admin_event_ids(user_id: str | None, db: AsyncSession) -> set:
    """
    Events one of the signed in account's profiles has accepted an admin invite for.
    """

    if not user_id:
        return set()
    mine = select(Profiles.id).where(Profiles.account_id == user_id)
    return set((await db.execute(
        select(BracketEventAdmins.event_id).where(BracketEventAdmins.profile_id.in_(mine)).where(BracketEventAdmins.status == "accepted")
    )).scalars().all())

async def is_manager(user_id: str | None, event: BracketEvents, db: AsyncSession) -> bool:
    if not user_id:
        return False
    return event.host_profile_id in await my_profile_ids(user_id, db) or event.id in await admin_event_ids(user_id, db)

async def managed_event(user_id: str, public_id: str, db: AsyncSession, lock: bool = False) -> BracketEvents:
    """
    An event the signed in account hosts or is an accepted admin of, or a 404 (so it doesn't
    reveal that an event someone can't manage exists in a manageable way).
    """

    event = await event_by_public_id(public_id, db, lock)
    if not await is_manager(user_id, event, db):
        raise HTTPException(status_code=404, detail="Event not found.")
    return event

async def owned_event(user_id: str, public_id: str, db: AsyncSession, lock: bool = False) -> BracketEvents:
    """
    An event run by one of the signed in account's own profiles (not as an admin), else a 404.
    """

    event = await event_by_public_id(public_id, db, lock)
    if event.host_profile_id not in await my_profile_ids(user_id, db):
        raise HTTPException(status_code=404, detail="Event not found.")
    return event

async def my_team_roles(user_id: str | None, db: AsyncSession) -> dict:
    """
    { team_id: 'owner' | 'captain' | 'player' } for every team the account owns or plays on.
    """

    if not user_id:
        return {}
    mine = select(Profiles.id).where(Profiles.account_id == user_id)
    roles = {
        team_id: role for team_id, role in (await db.execute(
            select(TeamMembers.team_id, TeamMembers.role).where(TeamMembers.profile_id.in_(mine))
        )).all()
    }
    for team_id in (await db.execute(select(Teams.id).where(Teams.owner_profile_id.in_(mine)))).scalars().all():
        roles[team_id] = "owner"
    return roles

async def entries_of(event_id, db: AsyncSession, lock: bool = False) -> list[BracketEntries]:
    query = select(BracketEntries).where(BracketEntries.event_id == event_id).order_by(BracketEntries.registered_at, BracketEntries.id)
    if lock:
        query = query.with_for_update()
    return list((await db.execute(query)).scalars().all())

async def entry_players(entry_ids, db: AsyncSession) -> dict:
    """
    { entry_id: [(BracketEntryPlayers, Profiles | None)] } in the order they were picked.
    """

    entry_ids = list(entry_ids)
    out: dict = {e: [] for e in entry_ids}
    if not entry_ids:
        return out
    rows = (await db.execute(
        select(BracketEntryPlayers, Profiles)
        .outerjoin(Profiles, Profiles.id == BracketEntryPlayers.profile_id)
        .where(BracketEntryPlayers.entry_id.in_(entry_ids))
        .order_by(BracketEntryPlayers.added_at, BracketEntryPlayers.id)
    )).all()
    for player, profile in rows:
        out[player.entry_id].append((player, profile))
    return out

async def entry_accounts(entries: list[BracketEntries], db: AsyncSession) -> dict:
    """
    { entry_id: {account ids} } - who hears about an entrant: its players, plus for a team its
    owner and captains (who run its sign-up even when not playing).
    """

    out = {e.id: set() for e in entries}
    if not entries:
        return out
    for entry_id, account_id in (await db.execute(
        select(BracketEntryPlayers.entry_id, Profiles.account_id)
        .join(Profiles, Profiles.id == BracketEntryPlayers.profile_id)
        .where(BracketEntryPlayers.entry_id.in_(out))
    )).all():
        out[entry_id].add(str(account_id))
    by_team: dict = {}
    for e in entries:
        if e.team_id:
            by_team.setdefault(e.team_id, []).append(e.id)
    if by_team:
        leaders = [
            *(await db.execute(
                select(Teams.id, Profiles.account_id).join(Profiles, Profiles.id == Teams.owner_profile_id).where(Teams.id.in_(by_team))
            )).all(),
            *(await db.execute(
                select(TeamMembers.team_id, Profiles.account_id).join(Profiles, Profiles.id == TeamMembers.profile_id)
                .where(TeamMembers.team_id.in_(by_team)).where(TeamMembers.role == "captain")
            )).all(),
        ]
        for team_id, account_id in leaders:
            for entry_id in by_team[team_id]:
                out[entry_id].add(str(account_id))
    return out

async def player_accounts(entries: list[BracketEntries], db: AsyncSession) -> dict:
    """
    { entry_id: {account ids} } of only the people playing for each entry.
    """

    out = {e.id: set() for e in entries}
    if not entries:
        return out
    for entry_id, account_id in (await db.execute(
        select(BracketEntryPlayers.entry_id, Profiles.account_id)
        .join(Profiles, Profiles.id == BracketEntryPlayers.profile_id)
        .where(BracketEntryPlayers.entry_id.in_(out))
    )).all():
        out[entry_id].add(str(account_id))
    return out

async def manager_accounts(event: BracketEvents, db: AsyncSession) -> list:
    """
    The host's account and every accepted admin's: who hears about the event's queue.
    """

    host = (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()
    admins = (await db.execute(
        select(Profiles.account_id).join(BracketEventAdmins, BracketEventAdmins.profile_id == Profiles.id)
        .where(BracketEventAdmins.event_id == event.id).where(BracketEventAdmins.status == "accepted")
    )).scalars().all()
    return [str(a) for a in (host, *admins) if a]

async def staff_accounts(event: BracketEvents, db: AsyncSession) -> set:
    """
    The host's account and every invited admin's (accepted or not). None of them can play.
    """

    host = (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()
    admins = (await db.execute(
        select(Profiles.account_id).join(BracketEventAdmins, BracketEventAdmins.profile_id == Profiles.id)
        .where(BracketEventAdmins.event_id == event.id)
    )).scalars().all()
    return {str(a) for a in (host, *admins) if a}

async def viewer_entry(user_id: str | None, entries: list[BracketEntries], db: AsyncSession, team_roles: dict | None = None,
                       solo: bool = False):
    """
    The signed in account's entrant in an event and whether it can act for it, as (entry, can_act),
    or None. It's theirs if they play for it, or own / captain / play on its team. In a solo event
    a team can have several entries, so the one they play for wins, and the player can act for
    their own entry as well as the team's owner and captains.
    """

    if not user_id or not entries:
        return None
    mine = await my_profile_ids(user_id, db)
    roles = team_roles if team_roles is not None else await my_team_roles(user_id, db)
    playing = set((await db.execute(
        select(BracketEntryPlayers.entry_id)
        .where(BracketEntryPlayers.entry_id.in_([e.id for e in entries]))
        .where(BracketEntryPlayers.profile_id.in_(mine))
    )).scalars().all()) if mine else set()
    found = None

    def rank(entry, can_act):
        return (entry.id in playing, can_act)

    for x in entries:
        if x.team_id is None:
            is_mine = can_act = x.profile_id in mine or x.id in playing
        else:
            role = roles.get(x.team_id)
            is_mine = role is not None or x.id in playing
            can_act = role in ("owner", "captain") or (solo and x.id in playing)
        if is_mine and (found is None or rank(x, can_act) > rank(*found)):
            found = (x, can_act)
    return found

async def acting_entry(user_id: str, event: BracketEvents, db: AsyncSession, among=None) -> BracketEntries:
    """
    The entrant the signed in account may act for in this event, or a 403. `among` narrows it to
    some entries (a match's two sides, or the entrant the request names): a captain can have
    several entries in a solo event.
    """

    entries = await entries_of(event.id, db)
    if among is not None:
        among = {str(a) for a in among if a}
        entries = [e for e in entries if str(e.id) in among]
    found = await viewer_entry(user_id, entries, db, solo = event.team_size == 1)
    if not found or not found[1]:
        raise HTTPException(status_code=403, detail="Only a player in this match (or the team's owner or captain) can do that.")
    return found[0]
