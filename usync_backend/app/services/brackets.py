import math
import uuid
import secrets

from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.teams import Teams, TeamMembers
from app.models.brackets import BracketEvents, BracketEntries, BracketEventAdmins
from app.services import bracket_engine as engine
from app.services.notifications import notify
from app.services.naming import new_public_id, account_profile_ids, moderate_name
from openai import AsyncOpenAI
from app.schemas.brackets import EventCreate, EventUpdate, EventDelay, Registration, MatchScore, AdminInvite

# Bracket hosting: events a host or venue profile runs, their sign-ups and their brackets.
#
# Entrants are a player on their own (1v1) or a team (from the Teams API). Rules:
# - A team needs the event's team size in players to be signed up. Until then (or while the event
#   is full) it's pending: it doesn't take a spot and isn't seeded. Pending teams move up in
#   sign-up order as rosters fill and spots free up.
# - A team's game must match the event's game.
# - Only the team's owner or a captain can enter it, unregister it or report its scores. Who
#   that is comes from the signed in account's current team role, never from who registered.
# - The host and the event's admins can't play in it, and an account can only be in an event once.
# - Rosters of teams seeded in a live bracket freeze until every match is played (see
#   assert_roster_unlocked, called from app.services.teams before any roster change).
# - Only the host and admins see an unpublished bracket, the payment setup and sign-up emails.
#
# Responses use the camelCase shape the frontend's bracket pages are built on.

HOSTING_TYPES = ("host", "venue")
MAX_EVENTS_PER_HOST = 200

def _new_public_id() -> str:
    return new_public_id("evt-")

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None

def _canonical_game(game: str | None) -> str:
    return "".join((game or "").split()).casefold()

def _log_id() -> str:
    return f"log-{secrets.token_hex(6)}"

# ---- loading ---------------------------------------------------------------

async def _event_by_public_id(public_id: str, db: AsyncSession, lock: bool = False) -> BracketEvents:
    query = select(BracketEvents).where(BracketEvents.public_id == public_id)
    if lock:
        query = query.with_for_update()
    event = (await db.execute(query)).scalars().first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    return event

async def _entries(event_id, db: AsyncSession) -> list[BracketEntries]:
    return list((await db.execute(
        select(BracketEntries).where(BracketEntries.event_id == event_id).order_by(BracketEntries.registered_at, BracketEntries.id)
    )).scalars().all())

_my_profile_ids = account_profile_ids

async def _my_team_roles(user_id: str | None, db: AsyncSession) -> dict:
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

async def _admin_event_ids(user_id: str | None, db: AsyncSession) -> set:
    """
    Events one of the signed in account's profiles has accepted an admin invite for.
    """

    if not user_id:
        return set()
    mine = select(Profiles.id).where(Profiles.account_id == user_id)
    return set((await db.execute(
        select(BracketEventAdmins.event_id).where(BracketEventAdmins.profile_id.in_(mine)).where(BracketEventAdmins.status == "accepted")
    )).scalars().all())

async def _host_event(user_id: str, public_id: str, db: AsyncSession, lock: bool = False) -> BracketEvents:
    """
    An event run by one of the signed in account's profiles, or one they're an admin of; else a 404.
    """

    event = await _event_by_public_id(public_id, db, lock)
    if event.host_profile_id not in await _my_profile_ids(user_id, db) and event.id not in await _admin_event_ids(user_id, db):
        raise HTTPException(status_code=404, detail="Event not found.")
    return event

async def _owner_event(user_id: str, public_id: str, db: AsyncSession, lock: bool = False) -> BracketEvents:
    """
    An event run by one of the signed in account's own profiles (not as an admin), else a 404.
    """

    event = await _event_by_public_id(public_id, db, lock)
    if event.host_profile_id not in await _my_profile_ids(user_id, db):
        raise HTTPException(status_code=404, detail="Event not found.")
    return event

async def _team_rosters(team_ids, db: AsyncSession) -> dict:
    """
    { team_id: [usernames] } - captains first, then by join date.
    """

    team_ids = [t for t in team_ids if t]
    rosters = {t: [] for t in team_ids}
    if not team_ids:
        return rosters
    rows = await db.execute(
        select(TeamMembers.team_id, Profiles.username)
        .join(Profiles, Profiles.id == TeamMembers.profile_id)
        .where(TeamMembers.team_id.in_(team_ids))
        .order_by(TeamMembers.role, TeamMembers.joined_at)
    )
    for team_id, username in rows.all():
        rosters[team_id].append(username)
    return rosters

async def _entry_accounts(entries: list[BracketEntries], db: AsyncSession) -> dict:
    """
    { entry_id: {account ids} } - who to notify about an entrant: the player for a solo entry;
    the team's owner and every player on it for a team.
    """

    out = {e.id: set() for e in entries}
    if not entries:
        return out
    profile_accounts = dict((await db.execute(
        select(Profiles.id, Profiles.account_id).where(Profiles.id.in_({e.profile_id for e in entries}))
    )).all())
    team_ids = {e.team_id for e in entries if e.team_id}
    team_accounts: dict = {t: set() for t in team_ids}
    if team_ids:
        for team_id, account_id in (await db.execute(
            select(Teams.id, Profiles.account_id).join(Profiles, Profiles.id == Teams.owner_profile_id).where(Teams.id.in_(team_ids))
        )).all():
            team_accounts[team_id].add(account_id)
        for team_id, account_id in (await db.execute(
            select(TeamMembers.team_id, Profiles.account_id).join(Profiles, Profiles.id == TeamMembers.profile_id).where(TeamMembers.team_id.in_(team_ids))
        )).all():
            team_accounts[team_id].add(account_id)
    for e in entries:
        out[e.id].add(profile_accounts.get(e.profile_id))
        out[e.id] |= team_accounts.get(e.team_id, set())
        out[e.id].discard(None)
    return out

async def _manager_accounts(event: BracketEvents, db: AsyncSession) -> list:
    """
    The host's account and every accepted admin's: who hears about disputes and no-shows.
    """

    host = (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()
    admins = (await db.execute(
        select(Profiles.account_id).join(BracketEventAdmins, BracketEventAdmins.profile_id == Profiles.id)
        .where(BracketEventAdmins.event_id == event.id).where(BracketEventAdmins.status == "accepted")
    )).scalars().all()
    return [host, *admins]

async def _staff_accounts(event: BracketEvents, db: AsyncSession) -> set:
    """
    Accounts that run the event (the host, and admins whether or not they've accepted yet).
    None of them can play in it.
    """

    host = (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()
    admins = (await db.execute(
        select(Profiles.account_id).join(BracketEventAdmins, BracketEventAdmins.profile_id == Profiles.id)
        .where(BracketEventAdmins.event_id == event.id)
    )).scalars().all()
    return {str(a) for a in (host, *admins) if a}

async def _entry_player_accounts(entries: list[BracketEntries], db: AsyncSession) -> dict:
    """
    { entry_id: {account ids} } of the people who'd actually play: the player for a solo entry,
    the team's players for a team (an org that owns a team doesn't play).
    """

    out = {e.id: set() for e in entries}
    solo = {e.profile_id: e.id for e in entries if e.team_id is None}
    if solo:
        for profile_id, account_id in (await db.execute(select(Profiles.id, Profiles.account_id).where(Profiles.id.in_(solo)))).all():
            out[solo[profile_id]].add(str(account_id))
    by_team = {e.team_id: e.id for e in entries if e.team_id}
    if by_team:
        for team_id, account_id in (await db.execute(
            select(TeamMembers.team_id, Profiles.account_id).join(Profiles, Profiles.id == TeamMembers.profile_id).where(TeamMembers.team_id.in_(by_team))
        )).all():
            out[by_team[team_id]].add(str(account_id))
    return out

def _viewer_entry(entries: list[BracketEntries], mine: set, team_roles: dict):
    """
    The signed in account's entrant in an event and whether it can act for it, as (entry, can_act),
    or None. Acting (reporting, unregistering) is for the solo player, or a team's current owner or
    captain - worked out from the account's own team roles, not from whoever registered the team,
    so a captain who's been removed or demoted loses it straight away.
    """

    found = None
    for x in entries:
        if x.team_id is None:
            is_mine = can_act = x.profile_id in mine
        else:
            role = team_roles.get(x.team_id)
            is_mine = role is not None
            can_act = role in ("owner", "captain")
        if is_mine and (found is None or (can_act and not found[1])):
            found = (x, can_act)
    return found

def _sync_rosters(entries: list[BracketEntries], rosters: dict) -> None:
    for e in entries:
        if e.team_id in rosters:
            e.roster = rosters[e.team_id]

async def _refresh_and_notify(event: BracketEvents, entries: list[BracketEntries], db: AsyncSession) -> None:
    """
    Re-works out who's signed up and who's pending, and tells teams that moved. The caller commits.
    """

    changes = _refresh_statuses(event, entries)
    if changes:
        _notify_status_changes(db, event, changes, await _entry_accounts([c[0] for c in changes], db))

async def _assert_no_conflicts(event: BracketEvents, entries: list[BracketEntries], db: AsyncSession) -> None:
    """
    409 if anyone running the event is on an entrant, or one account plays for two entrants.
    """

    staff = await _staff_accounts(event, db)
    seen: dict = {}
    for entry_id, accounts in (await _entry_player_accounts(entries, db)).items():
        name = next(e.name for e in entries if e.id == entry_id)
        if accounts & staff:
            raise HTTPException(status_code=409, detail=f"{name} has a player who's running this event. Hosts and admins can't play in it.")
        for account in accounts:
            if account in seen and seen[account] != name:
                raise HTTPException(status_code=409, detail=f"A player is on both {seen[account]} and {name}. Each player can only be in an event once.")
            seen[account] = name

# ---- life cycle ------------------------------------------------------------

def _built(event: BracketEvents) -> dict | None:
    b = event.bracket
    if not b:
        return None
    return engine.build_bracket(b["format"], b["seeds"], b.get("results", {}))

def is_live(event: BracketEvents, built: dict | None = None) -> bool:
    """
    Published and not every match played yet - rosters are frozen. Pass `built` when the caller
    has already built the bracket, to skip building it again.
    """

    if not (event.bracket and event.bracket.get("published")):
        return False
    done, total = engine.progress(built or _built(event))
    return not (total > 0 and done == total)

def _published(event: BracketEvents) -> bool:
    return bool(event.bracket and event.bracket.get("published"))

def _refresh_statuses(event: BracketEvents, entries: list[BracketEntries]) -> list[tuple]:
    """
    Works out who's signed up and who's pending, in sign-up order. Signed-up entries keep their
    spot while their roster is still full; pending teams with a full roster take any spot left.
    Nothing moves once the bracket is published. Returns [(entry, old_status, new_status)].
    """

    if _published(event):
        return []
    changes = []

    def full(e):
        return e.team_id is None or len(e.roster or []) >= event.team_size

    for e in entries:
        if e.status == "signed_up" and not full(e):
            changes.append((e, "signed_up", "pending"))
            e.status = "pending"

    taken = sum(1 for e in entries if e.status == "signed_up")
    for e in entries:
        if e.status == "pending" and full(e) and (event.max_entrants is None or taken < event.max_entrants):
            changes.append((e, "pending", "signed_up"))
            e.status = "signed_up"
            taken += 1
    return changes

def _notify_status_changes(db: AsyncSession, event: BracketEvents, changes: list[tuple], accounts: dict) -> None:
    for entry, _, new in changes:
        if entry.team_id is None:
            continue
        data = {"event_id": event.public_id, "event_name": event.name, "team_name": entry.name}
        if new == "signed_up":
            notify(db, accounts.get(entry.id, ()), "team_signed_up", data)
        else:
            notify(db, accounts.get(entry.id, ()), "team_pending", {**data, "have": len(entry.roster or []), "need": event.team_size})

# ---- output ----------------------------------------------------------------

async def _events_out(events: list[BracketEvents], user_id: str | None, db: AsyncSession, built: dict | None = None) -> list[dict]:
    """
    The events as the bracket pages use them. `built` ({event id: built bracket}) reuses brackets
    the caller has already built, so a write doesn't build the same bracket again for its response.
    """

    if not events:
        return []
    event_ids = [e.id for e in events]
    entries = list((await db.execute(
        select(BracketEntries).where(BracketEntries.event_id.in_(event_ids)).order_by(BracketEntries.registered_at, BracketEntries.id)
    )).scalars().all())
    by_event: dict = {e.id: [] for e in events}
    for entry in entries:
        by_event[entry.event_id].append(entry)

    profiles = {
        p.id: p for p in (await db.execute(
            select(Profiles).where(Profiles.id.in_({e.host_profile_id for e in events} | {x.profile_id for x in entries}))
        )).scalars().all()
    }
    team_ids = {x.team_id for x in entries if x.team_id}
    team_public = dict((await db.execute(select(Teams.id, Teams.public_id).where(Teams.id.in_(team_ids)))).all()) if team_ids else {}
    mine = await _my_profile_ids(user_id, db)
    team_roles = await _my_team_roles(user_id, db)
    admin_rows = (await db.execute(
        select(BracketEventAdmins, Profiles).join(Profiles, Profiles.id == BracketEventAdmins.profile_id)
        .where(BracketEventAdmins.event_id.in_(event_ids)).order_by(BracketEventAdmins.created_at)
    )).all()
    admins_by_event: dict = {e.id: [] for e in events}
    for admin, prof in admin_rows:
        admins_by_event[admin.event_id].append((admin, prof))

    out = []
    for event in events:
        host = profiles[event.host_profile_id]
        is_host = event.host_profile_id in mine
        is_admin = any(a.status == "accepted" and p.id in mine for a, p in admins_by_event[event.id])
        can_manage = is_host or is_admin
        built_now = (built or {}).get(event.id)
        built_bracket = (built_now or _built(event)) if _published(event) else None
        viewer_entry = _viewer_entry(by_event[event.id], mine, team_roles)
        entrants = []
        for x in by_event[event.id]:
            can_act = bool(viewer_entry and viewer_entry[0] is x and viewer_entry[1])
            captain = profiles[x.profile_id].username if x.profile_id in profiles else ""
            roster = list(x.roster or [])
            entrant = {
                "id": str(x.id),
                "name": x.name,
                "captain": captain,
                "rating": x.rating,
                "registeredAt": _iso(x.registered_at),
                "status": x.status,
                "teamId": team_public.get(x.team_id),
                "roster": roster,
                "rosterSize": len(roster),
                "teammates": [n for n in roster if n != captain],
            }
            if can_manage or can_act:
                entrant.update({"email": x.email, "answers": x.answers or {}, "pass": x.pass_name})
            entrants.append(entrant)

        out.append({
            "id": event.public_id,
            "name": event.name,
            "eventType": event.event_type,
            "game": event.game,
            "gameTitle": event.game_title,
            "format": event.format,
            "teamSize": f"{event.team_size}v{event.team_size}",
            "bestOf": event.best_of,
            "finalBestOf": event.final_best_of,
            "maxEntrants": event.max_entrants,
            "minEntrants": event.min_entrants,
            "platform": event.platform,
            "restrictions": event.restrictions or [],
            "startsAt": _iso(event.starts_at),
            "locationType": event.location_type,
            "venue": event.venue,
            "entryFee": event.entry_fee,
            "prize": event.prize,
            "description": event.description,
            "rules": event.rules,
            "registrationOpen": event.registration_open,
            "noShowMinutes": event.no_show_minutes,
            "payments": _payments_out(event.payments, can_manage),
            "signupForm": event.signup_form or {"playerFields": [], "questions": []},
            "delays": event.delays or [],
            # Drafts are the host's and admins' business until the bracket is published.
            "bracket": event.bracket if can_manage or _published(event) else None,
            "createdAt": _iso(event.created_at),
            "hostProfile": host.public_id,
            "host": {"type": host.type, "username": host.username, "public_id": host.public_id, "profile_picture": host.profile_picture},
            "entrants": entrants,
            "canManage": can_manage,
            "isHost": is_host,
            "admins": [
                {"id": str(a.id), "status": a.status, "username": p.username, "public_id": p.public_id, "profile_picture": p.profile_picture}
                for a, p in admins_by_event[event.id]
            ] if can_manage else [],
            "viewerEntrantId": str(viewer_entry[0].id) if viewer_entry else None,
            "viewerCanAct": bool(viewer_entry and viewer_entry[1]),
            "rostersLocked": is_live(event, built_bracket),
            # When each playable match's no-show button unlocks (worked out here, not in the browser).
            "noShowUnlocks": {
                m["id"]: _iso(no_show_unlocks_at(event, m)) for m in engine.ready_matches(built_bracket)
            } if built_bracket and event.no_show_minutes is not None else {},
        })
    return out

def _payments_out(payments: dict | None, can_manage: bool) -> dict:
    """
    The payment setup as each viewer may see it: the host and admins get all of it; everyone else
    only whether fees are collected and the passes on sale (never the host's notification email).
    """

    payments = payments or {}
    if can_manage:
        return payments or {"enabled": False}
    return {
        "enabled": bool(payments.get("enabled")),
        "tiers": [
            {"name": t.get("name"), "price": t.get("price"), "capacity": t.get("capacity")}
            for t in payments.get("tiers") or [] if isinstance(t, dict)
        ],
    }

async def _event_out(event: BracketEvents, user_id: str | None, db: AsyncSession, built: dict | None = None) -> dict:
    return (await _events_out([event], user_id, db, {event.id: built} if built else None))[0]

# ---- reads -----------------------------------------------------------------

async def get_event(public_id: str, user_id: str | None, db: AsyncSession) -> dict:
    return await _event_out(await _event_by_public_id(public_id, db), user_id, db)

async def hosting_events(user_id: str, db: AsyncSession) -> list[dict]:
    """
    Events any of the signed in account's profiles run or are an admin of, newest first.
    """

    admin_of = await _admin_event_ids(user_id, db)
    condition = BracketEvents.host_profile_id.in_(select(Profiles.id).where(Profiles.account_id == user_id))
    if admin_of:
        condition = condition | BracketEvents.id.in_(admin_of)
    events = (await db.execute(
        select(BracketEvents)
        .where(condition)
        .order_by(BracketEvents.created_at.desc())
    )).scalars().all()
    return await _events_out(list(events), user_id, db)

async def entered_events(user_id: str, db: AsyncSession) -> list[dict]:
    """
    Events the account is in: its own solo entries, and entries of teams it owns or plays on.
    """

    mine = select(Profiles.id).where(Profiles.account_id == user_id)
    team_ids = list((await _my_team_roles(user_id, db)).keys())
    condition = BracketEntries.profile_id.in_(mine)
    if team_ids:
        condition = condition | BracketEntries.team_id.in_(team_ids)
    events = (await db.execute(
        select(BracketEvents)
        .where(BracketEvents.id.in_(select(BracketEntries.event_id).where(condition)))
        .order_by(BracketEvents.starts_at)
    )).scalars().all()
    return await _events_out(list(events), user_id, db)

async def profile_events(public_id: str, user_id: str | None, db: AsyncSession) -> list[dict]:
    """
    Events a host or venue profile runs, for its public profile page.
    """

    events = (await db.execute(
        select(BracketEvents)
        .join(Profiles, Profiles.id == BracketEvents.host_profile_id)
        .where(Profiles.public_id == public_id)
        .order_by(BracketEvents.starts_at.desc())
    )).scalars().all()
    return await _events_out(list(events), user_id, db)

# ---- host: the event -------------------------------------------------------

async def create_event(user_id: str, body: EventCreate, db: AsyncSession, openai: AsyncOpenAI | None = None) -> dict:
    await moderate_name(body.name, openai, "event name")
    host = (await db.execute(
        select(Profiles).where(Profiles.public_id == body.host_profile).where(Profiles.account_id == user_id)
    )).scalars().first()
    if not host or host.type not in HOSTING_TYPES:
        raise HTTPException(status_code=403, detail="Post events from a host or venue profile.")
    if not host.bracket_hosting:
        raise HTTPException(status_code=403, detail="Bracket hosting is turned off for this profile.")

    count = len((await db.execute(select(BracketEvents.id).where(BracketEvents.host_profile_id == host.id))).all())
    if count >= MAX_EVENTS_PER_HOST:
        raise HTTPException(status_code=409, detail=f"A profile can have up to {MAX_EVENTS_PER_HOST} events.")

    size_a, size_b = body.team_size.split("v")
    if size_a != size_b:
        raise HTTPException(status_code=422, detail="Both sides need the same team size.")
    # LANs are in person; everything else is online.
    location_type = "in_person" if body.event_type == "lan" else "online"
    _check_limits(body.min_entrants, body.max_entrants, body.final_best_of)

    event = BracketEvents(
        id = uuid.uuid4(),
        public_id = _new_public_id(),
        host_profile_id = host.id,
        name = body.name.strip(),
        event_type = body.event_type,
        game = body.game.strip(),
        game_title = body.game_title.strip(),
        format = body.format,
        team_size = int(size_a),
        best_of = body.best_of,
        final_best_of = body.final_best_of if body.format in engine.FINAL_FORMATS else None,
        max_entrants = body.max_entrants,
        min_entrants = body.min_entrants,
        platform = body.platform,
        restrictions = body.restrictions,
        starts_at = body.starts_at,
        location_type = location_type,
        venue = body.venue.strip() if location_type == "in_person" else "",
        entry_fee = body.entry_fee,
        prize = body.prize.strip(),
        description = body.description,
        rules = body.rules,
        registration_open = body.registration_open,
        no_show_minutes = body.no_show_minutes,
        payments = body.payments,
        signup_form = body.signup_form.model_dump(by_alias = True),
        delays = [],
        bracket = None,
    )
    db.add(event)
    await db.flush()
    for profile_public_id in dict.fromkeys(body.admins):
        await _invite_admin(event, host, profile_public_id, db)
    await db.commit()
    return await _event_out(event, user_id, db)

def _check_limits(min_entrants, max_entrants, final_best_of) -> None:
    if min_entrants is not None and max_entrants is not None and min_entrants > max_entrants:
        raise HTTPException(status_code=422, detail="The minimum can't be more than the maximum.")
    if final_best_of is not None and final_best_of % 2 == 0:
        raise HTTPException(status_code=422, detail="Finals need an odd best of.")

async def update_event(user_id: str, public_id: str, body: EventUpdate, db: AsyncSession, openai: AsyncOpenAI | None = None) -> dict:
    values = body.model_dump(exclude_unset = True)
    if values.get("name"):
        # Before taking the row lock: the moderation call is slow.
        await moderate_name(values["name"], openai, "event name")
    event = await _host_event(user_id, public_id, db, lock = True)
    entries = await _entries(event.id, db)
    if _published(event):
        values.pop("max_entrants", None)
        values.pop("min_entrants", None)
        if values.get("registration_open"):
            raise HTTPException(status_code=409, detail="The bracket is live, so sign-ups can't reopen.")
    if "final_best_of" in values and event.format not in engine.FINAL_FORMATS:
        values["final_best_of"] = None
    _check_limits(values.get("min_entrants", event.min_entrants), values.get("max_entrants", event.max_entrants), values.get("final_best_of"))
    new_max = values.get("max_entrants", event.max_entrants)
    signed_up = sum(1 for e in entries if e.status == "signed_up")
    if "max_entrants" in values and new_max is not None and new_max < signed_up:
        raise HTTPException(status_code=409, detail=f"{signed_up} are already signed up, so the maximum can't go below {signed_up}.")
    for key, value in values.items():
        setattr(event, key, value.strip() if isinstance(value, str) and key != "description" and key != "rules" else value)

    await _refresh_and_notify(event, entries, db)
    await db.commit()
    return await _event_out(event, user_id, db)

async def delete_event(user_id: str, public_id: str, db: AsyncSession) -> None:
    event = await _owner_event(user_id, public_id, db)
    await db.delete(event)
    await db.commit()

async def delay_start(user_id: str, public_id: str, body: EventDelay, db: AsyncSession) -> dict:
    """
    Moves the start time and tells everyone entered.
    """

    event = await _host_event(user_id, public_id, db, lock = True)
    event.delays = [*(event.delays or []), {"from": _iso(event.starts_at), "to": _iso(body.starts_at), "reason": body.reason.strip(), "at": _iso(_now())}]
    event.starts_at = body.starts_at

    entries = await _entries(event.id, db)
    accounts = await _entry_accounts(entries, db)
    notify(db, set().union(*accounts.values()) if accounts else set(), "event_delayed", {
        "event_id": event.public_id, "event_name": event.name, "starts_at": _iso(body.starts_at), "reason": body.reason.strip(),
    })
    await db.commit()
    return await _event_out(event, user_id, db)

# ---- sign-ups --------------------------------------------------------------

async def register(user_id: str, public_id: str, body: Registration, db: AsyncSession) -> dict:
    event = await _event_by_public_id(public_id, db, lock = True)
    if _published(event) or not event.registration_open:
        raise HTTPException(status_code=409, detail="Sign-ups are closed.")
    entries = await _entries(event.id, db)
    signed_up = sum(1 for e in entries if e.status == "signed_up")
    has_room = event.max_entrants is None or signed_up < event.max_entrants

    if event.team_size == 1:
        if body.team_id:
            raise HTTPException(status_code=422, detail="This is a 1v1 event - sign up on your own.")
        player = (await db.execute(
            select(Profiles).where(Profiles.account_id == user_id).where(Profiles.type == "player")
        )).scalars().first()
        if not player:
            raise HTTPException(status_code=403, detail="You need a player profile to sign up.")
        if not has_room:
            raise HTTPException(status_code=409, detail="This event is full.")
        entry = BracketEntries(
            id = uuid.uuid4(), event_id = event.id, profile_id = player.id, team_id = None, name = player.username,
            status = "signed_up", email = body.email, answers = body.answers, pass_name = body.pass_name, roster = [],
        )
    else:
        if not body.team_id:
            raise HTTPException(status_code=422, detail=f"This is a {event.team_size}v{event.team_size} event - pick a team.")
        team = (await db.execute(select(Teams).where(Teams.public_id == body.team_id))).scalars().first()
        role = (await _my_team_roles(user_id, db)).get(team.id) if team else None
        if not team or role is None:
            raise HTTPException(status_code=404, detail="Team not found.")
        if role not in ("owner", "captain"):
            raise HTTPException(status_code=403, detail="Only the team's owner or a captain can enter it.")
        if _canonical_game(team.game) != _canonical_game(event.game):
            detail = f"{team.name} doesn't have a game set." if not team.game else f"{team.name} plays {team.game}, not {event.game}."
            raise HTTPException(status_code=409, detail=f"{detail} Set the team's game to {event.game} to enter.")
        if role == "owner":
            profile_id = team.owner_profile_id
        else:
            profile_id = (await db.execute(
                select(TeamMembers.profile_id)
                .join(Profiles, Profiles.id == TeamMembers.profile_id)
                .where(TeamMembers.team_id == team.id).where(Profiles.account_id == user_id)
            )).scalar()
        roster = (await _team_rosters([team.id], db))[team.id]
        entry = BracketEntries(
            id = uuid.uuid4(), event_id = event.id, profile_id = profile_id, team_id = team.id, name = team.name,
            status = "signed_up" if len(roster) >= event.team_size and has_room else "pending",
            email = body.email, answers = body.answers, pass_name = body.pass_name, roster = roster,
        )

    db.add(entry)
    await db.flush()
    # Hosts and admins can't play, and nobody can be in the event twice (on two teams, say).
    await _assert_no_conflicts(event, [*entries, entry], db)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already signed up.")
    return await _event_out(event, user_id, db)

async def _remove_entry(event: BracketEvents, entry: BracketEntries, db: AsyncSession) -> None:
    await db.delete(entry)
    await db.flush()
    await _refresh_and_notify(event, await _entries(event.id, db), db)
    await db.commit()

async def unregister(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    Takes the signed in player (or a team they own or captain) out of the event.
    """

    event = await _event_by_public_id(public_id, db, lock = True)
    if _published(event):
        raise HTTPException(status_code=409, detail="The bracket is live - you can't unregister now.")
    viewer = _viewer_entry(await _entries(event.id, db), await _my_profile_ids(user_id, db), await _my_team_roles(user_id, db))
    if not viewer:
        raise HTTPException(status_code=404, detail="You're not signed up.")
    if not viewer[1]:
        raise HTTPException(status_code=403, detail="Only the team's owner or a captain can unregister it.")
    await _remove_entry(event, viewer[0], db)
    return await _event_out(event, user_id, db)

async def remove_entrant(user_id: str, public_id: str, entrant_id: uuid.UUID, db: AsyncSession) -> dict:
    event = await _host_event(user_id, public_id, db, lock = True)
    if _published(event):
        raise HTTPException(status_code=409, detail="The bracket is live, so the entrant list is locked.")
    entry = await db.get(BracketEntries, entrant_id)
    if not entry or entry.event_id != event.id:
        raise HTTPException(status_code=404, detail="Entrant not found.")
    await _remove_entry(event, entry, db)
    return await _event_out(event, user_id, db)

# ---- rosters (called from app.services.teams) ------------------------------

async def _live_events_for_team(team_id, db: AsyncSession) -> list[BracketEvents]:
    """
    Published events the team is seeded in that aren't finished. Unpublished events are filtered
    out in SQL; a pending team (never seeded) isn't playing, so its roster isn't frozen by them.
    """

    rows = (await db.execute(
        select(BracketEvents, BracketEntries.id)
        .join(BracketEntries, BracketEntries.event_id == BracketEvents.id)
        .where(BracketEntries.team_id == team_id)
        .where(BracketEvents.bracket["published"].as_boolean().is_(True))
    )).all()
    return [e for e, entry_id in rows if str(entry_id) in e.bracket.get("seeds", []) and is_live(e)]

async def assert_roster_unlocked(team_id, db: AsyncSession) -> None:
    """
    Raises 409 while the team is playing in a live bracket - rosters freeze until it's over.
    """

    live = await _live_events_for_team(team_id, db)
    if live:
        raise HTTPException(status_code=409, detail=f"This team's roster is locked while {live[0].name} is live.")

async def sync_team(team_id, db: AsyncSession) -> None:
    """
    After a team's roster or name changes: updates its entries in events that haven't started
    and moves them between pending and signed up. Adds notifications; the caller commits.
    """

    team = await db.get(Teams, team_id)
    if not team:
        return
    roster = (await _team_rosters([team_id], db))[team_id]
    events = (await db.execute(
        select(BracketEvents).where(BracketEvents.id.in_(select(BracketEntries.event_id).where(BracketEntries.team_id == team_id)))
    )).scalars().all()
    for event in events:
        if _published(event):
            continue
        entries = await _entries(event.id, db)
        for e in entries:
            if e.team_id == team_id:
                e.roster = roster
                e.name = team.name
        await _refresh_and_notify(event, entries, db)

async def before_team_deleted(team_id, db: AsyncSession) -> list:
    """
    Blocks deleting a team that's in a live bracket, and drops its sign-ups for events that
    haven't started (finished events keep the entry, with the team unlinked). Returns the event
    ids whose statuses need refreshing after the delete.
    """

    await assert_roster_unlocked(team_id, db)
    events = (await db.execute(
        select(BracketEvents).where(BracketEvents.id.in_(select(BracketEntries.event_id).where(BracketEntries.team_id == team_id)))
    )).scalars().all()
    upcoming = [e.id for e in events if not _published(e)]
    if upcoming:
        await db.execute(delete(BracketEntries).where(BracketEntries.team_id == team_id).where(BracketEntries.event_id.in_(upcoming)))
    return upcoming

async def refresh_events(event_ids, db: AsyncSession) -> None:
    for event_id in event_ids:
        event = await db.get(BracketEvents, event_id)
        if not event:
            continue
        await _refresh_and_notify(event, await _entries(event.id, db), db)

# ---- host: seeding and publishing -----------------------------------------

def _empty_bracket(fmt: str, seeds: list[str]) -> dict:
    return {"format": fmt, "seeds": seeds, "results": {}, "reports": {}, "noShows": {}, "log": [], "published": False, "generatedAt": _iso(_now())}

async def save_draft(user_id: str, public_id: str, seeds: list[str], db: AsyncSession) -> dict:
    event = await _host_event(user_id, public_id, db, lock = True)
    if _published(event):
        raise HTTPException(status_code=409, detail="The bracket is already live. Reset it to reseed.")
    entries = await _entries(event.id, db)
    allowed = {str(e.id) for e in entries if e.status == "signed_up"}
    if len(set(seeds)) != len(seeds) or not set(seeds) <= allowed:
        raise HTTPException(status_code=422, detail="Seeds must be signed-up entrants, each once. Pending teams can't be seeded.")
    if len(seeds) < 2:
        raise HTTPException(status_code=422, detail="You need at least 2 entrants to build a bracket.")
    event.bracket = _empty_bracket(event.format, seeds)
    await db.commit()
    return await _event_out(event, user_id, db)

async def _notify_new_ready(db: AsyncSession, event: BracketEvents, before: dict | None, after: dict, entries: dict | None = None) -> None:
    """
    Tells both sides of every match that just became playable, and stamps when it did: that's the
    match's start, which its no-show timer counts from. `entries` ({id: entry}) skips a reload
    when the caller already has them.
    """

    old = {m["id"]: (m["a"], m["b"]) for m in engine.ready_matches(before)} if before else {}
    fresh = [m for m in engine.ready_matches(after) if old.get(m["id"]) != (m["a"], m["b"])]
    if not fresh:
        return
    now = _iso(_now())
    event.bracket = {**event.bracket, "readyAt": {
        **(event.bracket.get("readyAt") or {}),
        **{m["id"]: {"a": m["a"], "b": m["b"], "at": now} for m in fresh},
    }}
    if entries is None:
        entries = {str(e.id): e for e in await _entries(event.id, db)}
    sides = [entries[x] for m in fresh for x in (m["a"], m["b"]) if x in entries]
    accounts = await _entry_accounts(sides, db)
    for m in fresh:
        for side, other in ((m["a"], m["b"]), (m["b"], m["a"])):
            if side in entries:
                notify(db, accounts.get(entries[side].id, ()), "match_ready", {
                    "event_id": event.public_id, "event_name": event.name, "match_id": m["id"],
                    "opponent": entries[other].name if other in entries else "",
                })

async def publish(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    Posts the bracket: sign-ups close, rosters freeze, and everyone with a match is told.
    """

    event = await _host_event(user_id, public_id, db, lock = True)
    if not event.bracket:
        raise HTTPException(status_code=409, detail="Save a seeding first.")
    if _published(event):
        return await _event_out(event, user_id, db)

    entries = await _entries(event.id, db)
    _sync_rosters(entries, await _team_rosters({e.team_id for e in entries if e.team_id}, db))
    await _refresh_and_notify(event, entries, db)
    signed_up = [e for e in entries if e.status == "signed_up"]

    if event.min_entrants is not None and len(signed_up) < event.min_entrants:
        raise HTTPException(status_code=409, detail=(
            f"Only {len(signed_up)} signed up, and this event needs at least {event.min_entrants} to start. "
            "It can start once more sign up."
        ))
    await _assert_no_conflicts(event, signed_up, db)

    # The field changed since the draft was saved (someone withdrew, signed up or dropped to
    # pending): rebuild the draft from the current entrants and send it back for review rather
    # than publishing a bracket with the wrong people in it. Kept seeds keep their order; new
    # entrants go to the bottom in sign-up order.
    current = [str(e.id) for e in signed_up]
    seeds = event.bracket["seeds"]
    if set(seeds) != set(current):
        kept = [x for x in seeds if x in set(current)]
        event.bracket = _empty_bracket(event.format, kept + [x for x in current if x not in set(kept)])
        await db.commit()
        raise HTTPException(status_code=409, detail=(
            "The entrants changed since this bracket was seeded, so it's been rebuilt with the current field. "
            "Review the new bracket, then publish again."
        ))

    event.bracket = {**event.bracket, "published": True}
    event.registration_open = False
    await _notify_new_ready(db, event, None, _built(event))
    await db.commit()
    return await _event_out(event, user_id, db)

async def reset_bracket(user_id: str, public_id: str, db: AsyncSession) -> dict:
    event = await _host_event(user_id, public_id, db, lock = True)
    event.bracket = None
    entries = await _entries(event.id, db)
    _sync_rosters(entries, await _team_rosters({e.team_id for e in entries if e.team_id}, db))
    await _refresh_and_notify(event, entries, db)
    await db.commit()
    return await _event_out(event, user_id, db)

# ---- results ---------------------------------------------------------------

def _live_bracket(event: BracketEvents) -> dict:
    if not _published(event):
        raise HTTPException(status_code=409, detail="The bracket isn't live yet.")
    return event.bracket

def _ready_match(built: dict, match_id: str) -> dict:
    match = engine.find_match(built, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found.")
    return match

def match_best_of(event: BracketEvents, built: dict, match: dict) -> int:
    """
    The finals best of for the deciding match when one is set, else the event's best of.
    """

    if event.final_best_of and engine.is_final_match(event.format, built, match):
        return event.final_best_of
    return event.best_of

def _check_score(event: BracketEvents, built: dict, match: dict, body: MatchScore) -> None:
    """
    A best of N is won by the first to ceil(N / 2) games: the winner must have exactly that many,
    the loser fewer (a Bo3 ends 2-0 or 2-1).
    """

    best_of = match_best_of(event, built, match)
    needed = math.ceil(best_of / 2)
    high, low = max(body.score_a, body.score_b), min(body.score_a, body.score_b)
    if high != needed or low >= needed:
        raise HTTPException(status_code=422, detail=f"This match is best of {best_of}: the winner needs exactly {needed}, the loser fewer.")

def _without(mapping: dict | None, key: str) -> dict:
    return {k: v for k, v in (mapping or {}).items() if k != key}

def _log_entry(match: dict, score_a, score_b, source: str, prev, extra: dict | None = None) -> dict:
    return {
        "id": _log_id(), "matchId": match["id"], "a": match["a"], "b": match["b"], "scoreA": score_a, "scoreB": score_b,
        "source": source, "prev": prev, "at": _iso(_now()), "reverted": False, **(extra or {}),
    }

def _prior_meta(bracket: dict, match_id: str) -> dict:
    return {"prevReports": (bracket.get("reports") or {}).get(match_id), "prevNoShow": (bracket.get("noShows") or {}).get(match_id)}

def _result(match: dict, score_a: int, score_b: int, forfeit: bool = False) -> dict:
    return {"a": match["a"], "b": match["b"], "scoreA": score_a, "scoreB": score_b, **({"forfeit": True} if forfeit else {})}

async def _commit_bracket(event: BracketEvents, bracket: dict, before: dict, user_id: str, db: AsyncSession) -> dict:
    event.bracket = bracket
    after = _built(event)
    await _notify_new_ready(db, event, before, after)
    await db.commit()
    return await _event_out(event, user_id, db, after)

async def host_report(user_id: str, public_id: str, match_id: str, body: MatchScore, db: AsyncSession) -> dict:
    """
    The host's call on a match is final (also how a forfeit or a no-show is settled).
    """

    event = await _host_event(user_id, public_id, db, lock = True)
    b = _live_bracket(event)
    before = _built(event)
    match = _ready_match(before, match_id)
    if match["status"] not in ("ready", "complete"):
        raise HTTPException(status_code=409, detail="That match isn't ready to report.")
    _check_score(event, before, match, body)
    bracket = {
        **b,
        "results": {**b.get("results", {}), match_id: _result(match, body.score_a, body.score_b, body.forfeit)},
        "reports": _without(b.get("reports"), match_id),
        "noShows": _without(b.get("noShows"), match_id),
        "log": [*b.get("log", []), _log_entry(match, body.score_a, body.score_b, "forfeit" if body.forfeit else "host",
                                               b.get("results", {}).get(match_id), _prior_meta(b, match_id))],
    }
    return await _commit_bracket(event, bracket, before, user_id, db)

async def _acting_entry(user_id: str, event: BracketEvents, db: AsyncSession) -> tuple[str, dict]:
    """
    The signed in account's entrant in this event, if it may act for it (the solo player, or the
    team's current owner or captain). Returns (entrant id, {id: entry}) so callers reuse the
    entries instead of loading them again; 403 otherwise.
    """

    entries = await _entries(event.id, db)
    viewer = _viewer_entry(entries, await _my_profile_ids(user_id, db), await _my_team_roles(user_id, db))
    if not viewer or not viewer[1]:
        raise HTTPException(status_code=403, detail="Only a player in this match (or the team's owner or captain) can do that.")
    return str(viewer[0].id), {str(e.id): e for e in entries}

async def player_report(user_id: str, public_id: str, match_id: str, body: MatchScore, db: AsyncSession) -> dict:
    """
    A side reports its score. When both sides agree on the winner the result is confirmed and
    the winner advances; if they disagree it's disputed and the host decides.

    Loads the entries once, builds the bracket once before the change (and once after, to find
    newly playable matches), and only looks up the accounts of the two sides.
    """

    event = await _event_by_public_id(public_id, db, lock = True)
    b = _live_bracket(event)
    me, entries = await _acting_entry(user_id, event, db)
    before = _built(event)
    match = _ready_match(before, match_id)
    if match["status"] != "ready" or me not in (match["a"], match["b"]):
        raise HTTPException(status_code=409, detail="You can only report your own match while it's being played.")
    _check_score(event, before, match, body)

    opponent_id = match["b"] if me == match["a"] else match["a"]
    reports = {k: v for k, v in ((b.get("reports") or {}).get(match_id) or {}).items() if engine.same_matchup(v, match)}
    theirs = reports.get(opponent_id)
    mine = {"a": match["a"], "b": match["b"], "scoreA": body.score_a, "scoreB": body.score_b, "at": _iso(_now())}

    if theirs and (theirs["scoreA"] > theirs["scoreB"]) == (body.score_a > body.score_b):
        event.bracket = {
            **b,
            "results": {**b.get("results", {}), match_id: _result(match, body.score_a, body.score_b)},
            "reports": _without(b.get("reports"), match_id),
            "noShows": _without(b.get("noShows"), match_id),
            # Both reports go in the log, so reverting this result can restore them.
            "log": [*b.get("log", []), _log_entry(match, body.score_a, body.score_b, "players", b.get("results", {}).get(match_id),
                                                   {"prevReports": {**reports, me: mine}})],
        }
        after = _built(event)
        await _notify_new_ready(db, event, before, after, entries)
        await db.commit()
        return await _event_out(event, user_id, db, after)

    # Reports don't change the matches, so the bracket built above is still right for the response.
    event.bracket = {**b, "reports": {**(b.get("reports") or {}), match_id: {**reports, me: mine}}}
    accounts = await _entry_accounts([entries[me], entries[opponent_id]], db)
    data = {"event_id": event.public_id, "event_name": event.name, "match_id": match_id}
    if theirs:
        for side, other in ((me, opponent_id), (opponent_id, me)):
            notify(db, accounts.get(entries[side].id, ()), "score_disputed", {**data, "opponent": entries[other].name})
        notify(db, await _manager_accounts(event, db), "score_disputed", {**data, "opponent": f"{entries[me].name} vs {entries[opponent_id].name}", "host": True})
    else:
        notify(db, accounts.get(entries[opponent_id].id, ()), "opponent_reported", {**data, "opponent": entries[me].name})
    await db.commit()
    return await _event_out(event, user_id, db, before)

def no_show_unlocks_at(event: BracketEvents, match: dict) -> datetime | None:
    """
    When a side may report the other as a no-show: the no-show window after the match started.
    A match starts when it becomes playable (both sides known), and never before the event does.
    """

    if event.no_show_minutes is None:
        return None
    start = event.starts_at
    ready = (event.bracket.get("readyAt") or {}).get(match["id"])
    if ready and engine.same_matchup(ready, match):
        start = max(start, datetime.fromisoformat(ready["at"]))
    return start + timedelta(minutes = event.no_show_minutes)

async def report_no_show(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    event = await _event_by_public_id(public_id, db, lock = True)
    b = _live_bracket(event)
    me, entries = await _acting_entry(user_id, event, db)
    match = _ready_match(_built(event), match_id)
    if match["status"] != "ready" or me not in (match["a"], match["b"]):
        raise HTTPException(status_code=409, detail="You can only report a no-show in your own match.")
    unlocks = no_show_unlocks_at(event, match)
    if unlocks is None:
        raise HTTPException(status_code=409, detail="This event doesn't use a no-show timer.")
    if _now() < unlocks:
        raise HTTPException(status_code=409, detail="No-shows can't be reported yet.")

    against = match["b"] if me == match["a"] else match["a"]
    event.bracket = {**b, "noShows": {**(b.get("noShows") or {}), match_id: {
        "a": match["a"], "b": match["b"], "reportedBy": me, "against": against, "at": _iso(_now()),
    }}}
    accounts = await _entry_accounts([entries[against]], db)
    data = {"event_id": event.public_id, "event_name": event.name, "match_id": match_id}
    notify(db, accounts.get(entries[against].id, ()), "no_show_reported", {**data, "opponent": entries[me].name, "against_you": True})
    notify(db, await _manager_accounts(event, db), "no_show_reported", {**data, "opponent": entries[against].name, "against_you": False, "host": True})
    await db.commit()
    return await _event_out(event, user_id, db)

async def dismiss_no_show(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    event = await _host_event(user_id, public_id, db, lock = True)
    b = _live_bracket(event)
    event.bracket = {**b, "noShows": _without(b.get("noShows"), match_id)}
    await db.commit()
    return await _event_out(event, user_id, db)

async def clear_match(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    event = await _host_event(user_id, public_id, db, lock = True)
    b = _live_bracket(event)
    before = _built(event)
    match = _ready_match(before, match_id)
    bracket = {
        **b,
        "results": _without(b.get("results"), match_id),
        "reports": _without(b.get("reports"), match_id),
        "noShows": _without(b.get("noShows"), match_id),
        "log": [*b.get("log", []), _log_entry(match, None, None, "cleared", b.get("results", {}).get(match_id), _prior_meta(b, match_id))],
    }
    return await _commit_bracket(event, bracket, before, user_id, db)

async def revert_decision(user_id: str, public_id: str, entry_id: str, db: AsyncSession) -> dict:
    """
    Puts a match back how it was before one of the decisions in the log.
    """

    event = await _host_event(user_id, public_id, db, lock = True)
    b = _live_bracket(event)
    entry = next((l for l in b.get("log", []) if l["id"] == entry_id), None)
    if not entry or entry.get("reverted"):
        raise HTTPException(status_code=404, detail="Nothing to revert.")
    before = _built(event)
    mid = entry["matchId"]

    def restore(mapping, value):
        return {**(mapping or {}), mid: value} if value else _without(mapping, mid)

    bracket = {
        **b,
        "results": restore(b.get("results"), entry.get("prev")),
        "reports": restore(b.get("reports"), entry.get("prevReports")),
        "noShows": restore(b.get("noShows"), entry.get("prevNoShow")),
        "log": [{**l, "reverted": True, "revertedAt": _iso(_now())} if l["id"] == entry_id else l for l in b.get("log", [])],
    }
    return await _commit_bracket(event, bracket, before, user_id, db)

# ---- admins ----------------------------------------------------------------
#
# The host (only the host, not other admins) invites player profiles to help run an event. The
# pending invite itself is what shows in the player's bell (with Accept / Decline), so there's no
# separate notification row to go stale; once accepted, the event appears on their Hosting tab
# and they can manage it like the host, except deleting it or changing admins. Someone playing
# in the event can't be an admin of it.

MAX_ADMINS = 10

async def _invite_admin(event: BracketEvents, host: Profiles, profile_public_id: str, db: AsyncSession) -> None:
    profile = (await db.execute(select(Profiles).where(Profiles.public_id == profile_public_id))).scalars().first()
    if not profile or profile.type != "player":
        raise HTTPException(status_code=404, detail="Player not found.")
    if profile.account_id == host.account_id:
        raise HTTPException(status_code=409, detail="You already run this event.")
    existing = (await db.execute(
        select(BracketEventAdmins).where(BracketEventAdmins.event_id == event.id).where(BracketEventAdmins.profile_id == profile.id)
    )).scalars().first()
    if existing:
        state = "an admin" if existing.status == "accepted" else "invited"
        raise HTTPException(status_code=409, detail=f"{profile.username} is already {state}.")
    count = len((await db.execute(select(BracketEventAdmins.id).where(BracketEventAdmins.event_id == event.id))).all())
    if count >= MAX_ADMINS:
        raise HTTPException(status_code=409, detail=f"An event can have up to {MAX_ADMINS} admins.")
    await _assert_not_playing(event, profile.account_id, f"{profile.username} is signed up to play in this event. They need to withdraw before you can invite them as an admin.", db)
    db.add(BracketEventAdmins(id = uuid.uuid4(), event_id = event.id, profile_id = profile.id, status = "pending"))

async def _assert_not_playing(event: BracketEvents, account_id, message: str, db: AsyncSession) -> None:
    playing = await _entry_player_accounts(await _entries(event.id, db), db)
    if any(str(account_id) in accounts for accounts in playing.values()):
        raise HTTPException(status_code=409, detail=message)

async def invite_admin(user_id: str, public_id: str, body: AdminInvite, db: AsyncSession) -> dict:
    event = await _owner_event(user_id, public_id, db, lock = True)
    host = (await db.execute(select(Profiles).where(Profiles.id == event.host_profile_id))).scalars().one()
    await _invite_admin(event, host, body.profile, db)
    await db.commit()
    return await _event_out(event, user_id, db)

async def remove_admin(user_id: str, public_id: str, admin_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    The host removes an admin (or cancels an invite), or an admin steps down.
    """

    event = await _event_by_public_id(public_id, db, lock = True)
    admin = (await db.execute(
        select(BracketEventAdmins).where(BracketEventAdmins.id == admin_id).where(BracketEventAdmins.event_id == event.id)
    )).scalars().first()
    mine = await _my_profile_ids(user_id, db)
    if not admin or (event.host_profile_id not in mine and admin.profile_id not in mine):
        raise HTTPException(status_code=404, detail="Admin not found.")
    await db.delete(admin)
    await db.commit()
    return await _event_out(event, user_id, db)

async def my_admin_invites(user_id: str, db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        select(BracketEventAdmins, BracketEvents, Profiles)
        .join(BracketEvents, BracketEvents.id == BracketEventAdmins.event_id)
        .join(Profiles, Profiles.id == BracketEvents.host_profile_id)
        .where(BracketEventAdmins.profile_id.in_(select(Profiles.id).where(Profiles.account_id == user_id)))
        .where(BracketEventAdmins.status == "pending")
        .order_by(BracketEventAdmins.created_at.desc())
    )).all()
    return [
        {"id": str(a.id), "eventId": e.public_id, "eventName": e.name, "game": e.game, "startsAt": _iso(e.starts_at),
         "host": {"username": h.username, "public_id": h.public_id, "type": h.type}}
        for a, e, h in rows
    ]

async def respond_admin_invite(user_id: str, invite_id: uuid.UUID, accept: bool, db: AsyncSession) -> list[dict]:
    invite = (await db.execute(
        select(BracketEventAdmins).where(BracketEventAdmins.id == invite_id)
        .where(BracketEventAdmins.profile_id.in_(select(Profiles.id).where(Profiles.account_id == user_id)))
        .where(BracketEventAdmins.status == "pending")
    )).scalars().first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found.")
    if accept:
        event = (await db.execute(select(BracketEvents).where(BracketEvents.id == invite.event_id).with_for_update())).scalars().one()
        me = (await db.execute(select(Profiles).where(Profiles.id == invite.profile_id))).scalars().one()
        # They may have signed up to play since they were invited.
        await _assert_not_playing(event, me.account_id, "You're playing in this event, so you can't be an admin of it. Unregister first.", db)
        invite.status = "accepted"
        host = (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()
        notify(db, [host], "admin_joined", {"event_id": event.public_id, "event_name": event.name, "player": me.username})
    else:
        await db.delete(invite)
    await db.commit()
    return await my_admin_invites(user_id, db)
