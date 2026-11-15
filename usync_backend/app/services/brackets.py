import uuid

from datetime import datetime, timezone
from fastapi import HTTPException
from openai import AsyncOpenAI
from sqlalchemy import select, delete, func, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.teams import Teams, TeamMembers, TeamGames
from app.models.games import Games, GameTitles, GameModes
from app.models.brackets import (
    BracketEvents, BracketEntries, BracketEntryPlayers, BracketEventAdmins, Brackets, BracketSeeds,
    BracketMatches, BracketFormats, MatchReports, MatchDecisions, EventIssues,
)
from app.schemas.brackets import EventCreate, EventUpdate, EventDelay, Registration, AdminInvite
from app.services import bracket_engine as engine
from app.services import bracket_access as access
from app.services import bracket_sync as bsync
from app.services import event_issues as issues
from app.services import lookups
from app.services import matches as play
from app.services.naming import new_public_id, moderate_name
from app.services.notifications import notify

# Bracket hosting: events a host or venue profile runs, their sign-ups and their brackets.
#
# - Sign-ups: a solo player (1v1), or a team entered by its owner or a captain, who picks which of
#   its players play (bracket_entry_players). A team is pending - no spot, not seeded - until the
#   event's team size in players is picked, or while the event is full. A player can only be in an
#   event once, can't play two events that start at the same time, and the host and admins can't
#   play at all. The captain can change the picks until the bracket is published; then the event's
#   roster is locked. The team itself never freezes.
# - The team must play the event's game (team_games).
# - Bracket: a draft (seeds, previewed with the engine) the host and admins can see, then published
#   (match rows created; see bracket_sync and matches) and finally complete.
# - Only the host and admins see an unpublished bracket, the payment setup, sign-up emails and
#   answers; each entrant's own captain / player sees theirs.
#
# Responses use the camelCase shape the frontend's bracket pages are built on (EventOut).

HOSTING_TYPES = ("host", "venue")
MAX_EVENTS_PER_HOST = 200
MAX_ADMINS = 10
PAGE_SIZE = 50

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None

def _new_public_id() -> str:
    return new_public_id("evt-")

async def _bracket(event_id, db: AsyncSession, lock: bool = False) -> Brackets | None:
    query = select(Brackets).where(Brackets.event_id == event_id)
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalars().first()

def _stage(event: BracketEvents, bracket: Brackets | None) -> str:
    if bracket is None:
        return "registration" if event.registration_open else "closed"
    return {"draft": "seeding", "published": "live", "complete": "complete"}[bracket.status]

def _payments_out(payments: dict | None, can_manage: bool) -> dict:
    """
    The host and admins see the whole payment setup; everyone else only whether fees are
    collected and the passes on sale (never the host's notification email).
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

def _profile_ref(p: Profiles | None) -> dict | None:
    if p is None:
        return None
    return {"type": p.type, "username": p.username, "public_id": p.public_id, "profile_picture": p.profile_picture}

# ---- the full event --------------------------------------------------------------

async def _match_views(event: BracketEvents, rows: list, viewer: tuple | None, can_manage: bool, db: AsyncSession) -> dict:
    """
    { match code: what the viewer sees and may do there } for every match with both sides known.
    """

    ids = [r.id for r in rows]
    reports: dict = {}
    if ids:
        for r in (await db.execute(select(MatchReports).where(MatchReports.match_id.in_(ids)))).scalars().all():
            reports.setdefault(r.match_id, {})[r.entry_id] = r
    open_issues: dict = {}
    for i in (await db.execute(
        select(EventIssues).where(EventIssues.event_id == event.id).where(EventIssues.status.in_(("open", "claimed")))
    )).scalars().all():
        open_issues.setdefault(i.match_id, []).append(i)
    my_disputes = set()
    if viewer:
        my_disputes = set((await db.execute(
            select(EventIssues.match_id).where(EventIssues.event_id == event.id).where(EventIssues.raised_by_entry_id == viewer[0].id)
            .where(EventIssues.type == "post_advancement_dispute")
        )).scalars().all())

    now = _now()
    out = {}
    touched = {r.id for r in rows if r.status == "complete" or r.id in reports}
    for r in rows:
        if r.status not in ("ready", "complete"):
            continue
        mine = bool(viewer and viewer[0].id in (r.entry_a_id, r.entry_b_id))
        acting = mine and viewer[1]
        types = {i.type for i in open_issues.get(r.id, [])}
        rep = reports.get(r.id, {})
        if r.status == "complete":
            state = "complete"
        elif r.locked:
            state = "locked"
        elif "dispute" in types:
            state = "disputed"
        elif "no_show" in types:
            state = "no_show"
        elif len(rep) == 1:
            state = "awaiting"
        else:
            state = "ready"
        unlocks = play.no_show_unlocks_at(event, r) if r.status == "ready" else None
        closes = play.dispute_closes_at(r)
        following = {f.id for f in bsync.dependents(r, rows)}
        view = {
            "state": state,
            "reportedBy": [str(e) for e in rep],
            "issues": sorted(types),
            "noShowUnlocksAt": _iso(unlocks),
            "disputeClosesAt": _iso(closes),
            "canReport": acting and state in ("ready", "awaiting"),
            "canReportNoShow": acting and state in ("ready", "awaiting") and unlocks is not None and now >= unlocks,
            "canDispute": acting and closes is not None and now <= closes and r.id not in my_disputes and not (following & touched),
        }
        if can_manage or mine:
            view["reports"] = {str(e): {"scoreA": x.score_a, "scoreB": x.score_b, "at": _iso(x.created_at)} for e, x in rep.items()}
        out[r.code] = view
    return out

async def _event_out(event: BracketEvents, user_id: str | None, db: AsyncSession) -> dict:
    entries = await access.entries_of(event.id, db)
    players = await access.entry_players([e.id for e in entries], db)
    registrants = {
        p.id: p for p in (await db.execute(
            select(Profiles).where(Profiles.id.in_({event.host_profile_id} | {e.profile_id for e in entries if e.profile_id}))
        )).scalars().all()
    }
    team_ids = {e.team_id for e in entries if e.team_id}
    team_public = dict((await db.execute(select(Teams.id, Teams.public_id).where(Teams.id.in_(team_ids)))).all()) if team_ids else {}
    mine = await access.my_profile_ids(user_id, db)
    roles = await access.my_team_roles(user_id, db)
    is_host = event.host_profile_id in mine
    admin_rows = (await db.execute(
        select(BracketEventAdmins, Profiles).join(Profiles, Profiles.id == BracketEventAdmins.profile_id)
        .where(BracketEventAdmins.event_id == event.id).order_by(BracketEventAdmins.created_at)
    )).all()
    can_manage = is_host or any(a.status == "accepted" and p.id in mine for a, p in admin_rows)
    viewer = await access.viewer_entry(user_id, entries, db, roles)

    entrants = []
    for x in entries:
        picked = [p for _, p in players.get(x.id, []) if p is not None]
        registrant = registrants.get(x.profile_id)
        entrant = {
            "id": str(x.id),
            "name": x.name,
            "captain": registrant.username if registrant else "",
            "status": x.status,
            "registeredAt": _iso(x.registered_at),
            "teamId": team_public.get(x.team_id),
            "players": [_profile_ref(p) for p in picked],
            "roster": [p.username for p in picked],
            "rosterSize": len(picked),
        }
        if can_manage or (viewer and viewer[0] is x and viewer[1]):
            entrant.update({"email": x.email, "answers": x.answers or {}, "pass": x.pass_name})
        entrants.append(entrant)

    bracket = await _bracket(event.id, db)
    bracket_out = None
    if bracket and (can_manage or bracket.status != "draft"):
        seeds = await bsync.seeds_of(bracket, db)
        rows = await bsync.match_rows(bracket, db) if bracket.status != "draft" else []
        bracket_out = {
            "status": bracket.status,
            "format": bracket.format,
            "seeds": seeds,
            "built": bsync.built_with_rows(bracket.format, seeds, rows),
            "matches": await _match_views(event, rows, viewer, can_manage, db) if rows else {},
        }

    game = await db.get(Games, event.game_id)
    fmt = await lookups.bracket_format(event.format, db)
    title = await db.get(GameTitles, event.game_title_id) if event.game_title_id else None
    mode = await db.get(GameModes, event.game_mode_id) if event.game_mode_id else None
    host = registrants[event.host_profile_id]
    return {
        "id": event.public_id,
        "name": event.name,
        "eventType": event.event_type,
        "game": game.name,
        "gameKey": game.key,
        "gameTitle": title.name if title else "",
        "gameTitleKey": title.key if title else None,
        "gameMode": mode.name if mode else None,
        "gameModeKey": mode.key if mode else None,
        "format": event.format,
        "formatLabel": fmt.label,
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
        "createdAt": _iso(event.created_at),
        "stage": _stage(event, bracket),
        "hostProfile": host.public_id,
        "host": _profile_ref(host),
        "entrants": entrants,
        "canManage": can_manage,
        "isHost": is_host,
        "admins": [
            {"id": str(a.id), "status": a.status, "username": p.username, "public_id": p.public_id, "profile_picture": p.profile_picture}
            for a, p in admin_rows
        ] if can_manage else [],
        "openIssues": (await issues.open_counts([event.id], db)).get(event.id, 0) if can_manage else 0,
        "viewerEntrantId": str(viewer[0].id) if viewer else None,
        "viewerCanAct": bool(viewer and viewer[1]),
        "bracket": bracket_out,
    }

async def get_event(public_id: str, user_id: str | None, db: AsyncSession) -> dict:
    return await _event_out(await access.event_by_public_id(public_id, db), user_id, db)

# ---- lists: summaries, a page at a time ------------------------------------------

def _cursor(event: BracketEvents) -> str:
    return f"{_iso(event.starts_at)}|{event.id}"

def _after(cursor: str | None):
    """
    Keyset paging on (starts_at, id), latest first: the next page starts strictly after the last
    row of the previous one, so pages never repeat or skip rows when events are added.
    """

    if not cursor:
        return None
    try:
        at, eid = cursor.split("|", 1)
        at, eid = datetime.fromisoformat(at), uuid.UUID(eid)
    except ValueError:
        raise HTTPException(status_code=422, detail="Bad cursor.")
    return or_(BracketEvents.starts_at < at, and_(BracketEvents.starts_at == at, BracketEvents.id < eid))

async def _page(condition, user_id: str | None, db: AsyncSession, cursor: str | None, limit: int) -> dict:
    limit = max(1, min(limit, PAGE_SIZE))
    query = select(BracketEvents).where(condition)
    after = _after(cursor)
    if after is not None:
        query = query.where(after)
    events = list((await db.execute(query.order_by(BracketEvents.starts_at.desc(), BracketEvents.id.desc()).limit(limit + 1))).scalars().all())
    more = len(events) > limit
    events = events[:limit]
    return {"events": await _summaries(events, user_id, db), "nextCursor": _cursor(events[-1]) if more and events else None}

async def _summaries(events: list[BracketEvents], user_id: str | None, db: AsyncSession) -> list[dict]:
    """
    What the dashboard and profile cards show: counts, stage, and the viewer's entrant and match.
    Brackets are only built for finished events, to name the winner and the viewer's place.
    """

    if not events:
        return []
    ids = [e.id for e in events]
    games = dict((await db.execute(select(Games.id, Games.name))).all())
    format_labels = dict((await db.execute(select(BracketFormats.key, BracketFormats.label))).all())
    title_ids = {e.game_title_id for e in events} - {None}
    titles = dict((await db.execute(select(GameTitles.id, GameTitles.name).where(GameTitles.id.in_(title_ids)))).all()) if title_ids else {}
    counts: dict = {}
    for event_id, status, n in (await db.execute(
        select(BracketEntries.event_id, BracketEntries.status, func.count()).where(BracketEntries.event_id.in_(ids))
        .group_by(BracketEntries.event_id, BracketEntries.status)
    )).all():
        counts.setdefault(event_id, {})[status] = n
    brackets = {b.event_id: b for b in (await db.execute(select(Brackets).where(Brackets.event_id.in_(ids)))).scalars().all()}
    mine = await access.my_profile_ids(user_id, db)
    admin_of = await access.admin_event_ids(user_id, db)
    roles = await access.my_team_roles(user_id, db)
    open_counts = await issues.open_counts(ids, db)
    hosts = dict((await db.execute(select(Profiles.id, Profiles.public_id).where(Profiles.id.in_({e.host_profile_id for e in events})))).all())

    out = []
    for event in events:
        bracket = brackets.get(event.id)
        is_host = event.host_profile_id in mine
        can_manage = is_host or event.id in admin_of
        entries = await access.entries_of(event.id, db) if user_id else []
        viewer = await access.viewer_entry(user_id, entries, db, roles) if entries else None
        viewer_match, winner, place = None, None, None
        if bracket and bracket.status != "draft":
            if viewer:
                m = (await db.execute(
                    select(BracketMatches).where(BracketMatches.bracket_id == bracket.id).where(BracketMatches.status == "ready")
                    .where(or_(BracketMatches.entry_a_id == viewer[0].id, BracketMatches.entry_b_id == viewer[0].id))
                )).scalars().first()
                if m:
                    other = m.entry_b_id if m.entry_a_id == viewer[0].id else m.entry_a_id
                    viewer_match = {"id": m.code, "opponent": next((e.name for e in entries if e.id == other), "TBD")}
            if bracket.status == "complete":
                rows = await bsync.match_rows(bracket, db)
                places = engine.placements(bracket.format, bsync.build(bracket.format, await bsync.seeds_of(bracket, db), rows)) or []
                names = dict((await db.execute(select(BracketEntries.id, BracketEntries.name).where(BracketEntries.event_id == event.id))).all())
                winner = names.get(uuid.UUID(places[0])) if places and places[0] else None
                if viewer and str(viewer[0].id) in places:
                    place = places.index(str(viewer[0].id)) + 1
        out.append({
            "id": event.public_id,
            "name": event.name,
            "eventType": event.event_type,
            "game": games.get(event.game_id, ""),
            "gameTitle": titles.get(event.game_title_id, ""),
            "format": event.format,
            "formatLabel": format_labels.get(event.format, event.format),
            "teamSize": f"{event.team_size}v{event.team_size}",
            "startsAt": _iso(event.starts_at),
            "stage": _stage(event, bracket),
            "hostProfile": hosts.get(event.host_profile_id),
            "maxEntrants": event.max_entrants,
            "signedUp": counts.get(event.id, {}).get("signed_up", 0),
            "pending": counts.get(event.id, {}).get("pending", 0),
            "canManage": can_manage,
            "isHost": is_host,
            "openIssues": open_counts.get(event.id, 0) if can_manage else 0,
            "viewerEntrantId": str(viewer[0].id) if viewer else None,
            "viewerEntrantName": viewer[0].name if viewer else None,
            "viewerMatch": viewer_match,
            "winner": winner,
            "viewerPlace": place,
        })
    return out

async def hosting_events(user_id: str, db: AsyncSession, cursor: str | None = None, limit: int = PAGE_SIZE) -> dict:
    """
    Events any of the signed in account's profiles run or are an admin of, latest start first.
    """

    admin_of = await access.admin_event_ids(user_id, db)
    condition = BracketEvents.host_profile_id.in_(select(Profiles.id).where(Profiles.account_id == user_id))
    if admin_of:
        condition = condition | BracketEvents.id.in_(admin_of)
    return await _page(condition, user_id, db, cursor, limit)

async def entered_events(user_id: str, db: AsyncSession, cursor: str | None = None, limit: int = PAGE_SIZE) -> dict:
    """
    Events the account is in: playing for an entrant, or owning / captaining / on a team entered.
    """

    mine = select(Profiles.id).where(Profiles.account_id == user_id)
    team_ids = list((await access.my_team_roles(user_id, db)).keys())
    condition = BracketEntries.id.in_(select(BracketEntryPlayers.entry_id).where(BracketEntryPlayers.profile_id.in_(mine)))
    condition = condition | (BracketEntries.profile_id.in_(mine) & BracketEntries.team_id.is_(None))
    if team_ids:
        condition = condition | BracketEntries.team_id.in_(team_ids)
    return await _page(BracketEvents.id.in_(select(BracketEntries.event_id).where(condition)), user_id, db, cursor, limit)

async def profile_events(public_id: str, user_id: str | None, db: AsyncSession, cursor: str | None = None, limit: int = PAGE_SIZE) -> dict:
    """
    Events a host or venue profile runs, for its public profile page.
    """

    host = select(Profiles.id).where(Profiles.public_id == public_id)
    return await _page(BracketEvents.host_profile_id.in_(host), user_id, db, cursor, limit)

# ---- host: the event --------------------------------------------------------------

def _check_limits(min_entrants, max_entrants, final_best_of, format_min: int = 2) -> None:
    if min_entrants is not None and max_entrants is not None and min_entrants > max_entrants:
        raise HTTPException(status_code=422, detail="The minimum can't be more than the maximum.")
    if max_entrants is not None and max_entrants < format_min:
        raise HTTPException(status_code=422, detail=f"This format needs at least {format_min} entrants.")
    if final_best_of is not None and final_best_of % 2 == 0:
        raise HTTPException(status_code=422, detail="Finals need an odd best of.")

async def create_event(user_id: str, body: EventCreate, db: AsyncSession, openai: AsyncOpenAI | None = None) -> dict:
    await moderate_name(body.name, openai, "event name")
    host = (await db.execute(
        select(Profiles).where(Profiles.public_id == body.host_profile).where(Profiles.account_id == user_id)
    )).scalars().first()
    if not host or host.type not in HOSTING_TYPES:
        raise HTTPException(status_code=403, detail="Post events from a host or venue profile.")
    if not host.bracket_hosting:
        raise HTTPException(status_code=403, detail="Bracket hosting is turned off for this profile.")
    count = (await db.execute(select(func.count()).where(BracketEvents.host_profile_id == host.id))).scalar()
    if count >= MAX_EVENTS_PER_HOST:
        raise HTTPException(status_code=409, detail=f"A profile can have up to {MAX_EVENTS_PER_HOST} events.")
    if body.best_of % 2 == 0:
        raise HTTPException(status_code=422, detail="Best of has to be odd.")

    size_a, size_b = body.team_size.split("v")
    if size_a != size_b:
        raise HTTPException(status_code=422, detail="Both sides need the same team size.")
    fmt = await lookups.bracket_format(body.format, db)
    game, title, mode = await lookups.resolve_game(body.game, body.game_title, body.game_mode, db)
    _check_limits(body.min_entrants, body.max_entrants, body.final_best_of, fmt.min_entrants)
    location_type = "in_person" if body.event_type == "lan" else "online"
    if location_type == "in_person" and not body.venue.strip():
        raise HTTPException(status_code=422, detail="Add where the LAN is held.")

    event = BracketEvents(
        id = uuid.uuid4(), public_id = _new_public_id(), host_profile_id = host.id, name = body.name.strip(),
        event_type = body.event_type, game_id = game.id, game_title_id = title.id if title else None,
        game_mode_id = mode.id if mode else None, format = fmt.key, team_size = int(size_a), best_of = body.best_of,
        final_best_of = body.final_best_of if fmt.has_final else None, max_entrants = body.max_entrants,
        min_entrants = body.min_entrants, platform = body.platform, restrictions = body.restrictions,
        starts_at = body.starts_at, location_type = location_type,
        venue = body.venue.strip() if location_type == "in_person" else "", entry_fee = body.entry_fee,
        prize = body.prize.strip(), description = body.description, rules = body.rules,
        registration_open = body.registration_open, no_show_minutes = body.no_show_minutes, payments = body.payments,
        signup_form = body.signup_form.model_dump(by_alias = True), delays = [],
    )
    db.add(event)
    await db.flush()
    for profile_public_id in dict.fromkeys(body.admins):
        await _invite_admin(event, host, profile_public_id, db)
    await db.commit()
    return await _event_out(event, user_id, db)

async def update_event(user_id: str, public_id: str, body: EventUpdate, db: AsyncSession, openai: AsyncOpenAI | None = None) -> dict:
    values = body.model_dump(exclude_unset = True)
    if values.get("name"):
        # Before taking the row lock: the moderation call is slow.
        await moderate_name(values["name"], openai, "event name")
    event = await access.managed_event(user_id, public_id, db, lock = True)
    bracket = await _bracket(event.id, db)
    entries = await access.entries_of(event.id, db, lock = True)
    if bracket and bracket.status != "draft":
        for frozen in ("max_entrants", "min_entrants", "best_of", "final_best_of"):
            values.pop(frozen, None)
        if values.get("registration_open"):
            raise HTTPException(status_code=409, detail="The bracket is live, so sign-ups can't reopen.")
    fmt = await lookups.bracket_format(event.format, db)
    if "final_best_of" in values and not fmt.has_final:
        values["final_best_of"] = None
    if values.get("best_of") is not None and values["best_of"] % 2 == 0:
        raise HTTPException(status_code=422, detail="Best of has to be odd.")
    _check_limits(values.get("min_entrants", event.min_entrants), values.get("max_entrants", event.max_entrants), values.get("final_best_of"), fmt.min_entrants)
    signed_up = sum(1 for e in entries if e.status == "signed_up")
    if "max_entrants" in values and values["max_entrants"] is not None and values["max_entrants"] < signed_up:
        raise HTTPException(status_code=409, detail=f"{signed_up} are already signed up, so the maximum can't go below {signed_up}.")
    for key, value in values.items():
        setattr(event, key, value.strip() if isinstance(value, str) and key not in ("description", "rules") else value)
    await _refresh_and_notify(event, entries, db)
    await db.commit()
    return await _event_out(event, user_id, db)

async def delete_event(user_id: str, public_id: str, db: AsyncSession) -> None:
    event = await access.owned_event(user_id, public_id, db)
    await db.delete(event)
    await db.commit()

async def delay_start(user_id: str, public_id: str, body: EventDelay, db: AsyncSession) -> dict:
    """
    Moves the start time and tells everyone entered.
    """

    event = await access.managed_event(user_id, public_id, db, lock = True)
    event.delays = [*(event.delays or []), {"from": _iso(event.starts_at), "to": _iso(body.starts_at), "reason": body.reason.strip(), "at": _iso(_now())}]
    event.starts_at = body.starts_at
    entries = await access.entries_of(event.id, db)
    accounts = await access.entry_accounts(entries, db)
    notify(db, set().union(*accounts.values()) if accounts else set(), "event_delayed", {
        "event_id": event.public_id, "event_name": event.name, "starts_at": _iso(body.starts_at), "reason": body.reason.strip(),
    })
    await db.commit()
    return await _event_out(event, user_id, db)

# ---- sign-ups ----------------------------------------------------------------------

def _refresh_statuses(event: BracketEvents, entries: list[BracketEntries], picked: dict) -> list[tuple]:
    """
    Who's signed up and who's pending, in sign-up order. An entry is signed up once it has the
    team size in players and there's a spot; signed-up entries keep their spot while they still
    have enough players. Returns [(entry, new_status)] for the ones that moved.
    """

    changes = []

    def full(e):
        return picked.get(e.id, 0) >= event.team_size

    for e in entries:
        if e.status == "signed_up" and not full(e):
            e.status = "pending"
            changes.append((e, "pending"))
    taken = sum(1 for e in entries if e.status == "signed_up")
    for e in entries:
        if e.status == "pending" and full(e) and (event.max_entrants is None or taken < event.max_entrants):
            e.status = "signed_up"
            changes.append((e, "signed_up"))
            taken += 1
    return changes

async def _refresh_and_notify(event: BracketEvents, entries: list[BracketEntries], db: AsyncSession) -> None:
    """
    Re-works out signed up / pending (not once the bracket is live) and tells teams that moved.
    The caller commits.
    """

    bracket = await _bracket(event.id, db)
    if bracket and bracket.status != "draft":
        return
    picked = dict((await db.execute(
        select(BracketEntryPlayers.entry_id, func.count()).where(BracketEntryPlayers.event_id == event.id)
        .where(BracketEntryPlayers.profile_id.is_not(None)).group_by(BracketEntryPlayers.entry_id)
    )).all())
    changes = _refresh_statuses(event, entries, picked)
    if not changes:
        return
    accounts = await access.entry_accounts([e for e, _ in changes], db)
    for entry, new in changes:
        if entry.team_id is None:
            continue
        data = {"event_id": event.public_id, "event_name": event.name, "team_name": entry.name}
        if new == "signed_up":
            notify(db, accounts.get(entry.id, ()), "team_signed_up", data)
        else:
            notify(db, accounts.get(entry.id, ()), "team_pending", {**data, "have": picked.get(entry.id, 0), "need": event.team_size})

def _check_signup(event: BracketEvents, body: Registration) -> None:
    """
    The sign-up form's answers and pass, checked against what the host asked for.
    """

    form = event.signup_form or {}
    fields = set(form.get("playerFields") or [])
    questions = {q.get("label"): q for q in form.get("questions") or [] if isinstance(q, dict)}
    unknown = set(body.answers) - fields - set(questions)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unexpected sign-up answers: {', '.join(sorted(unknown))}.")
    for label, q in questions.items():
        answer = body.answers.get(label, "")
        if q.get("required") and not answer:
            raise HTTPException(status_code=422, detail=f"Answer \"{label}\".")
        options = [o.strip() for o in (q.get("options") or "").split(",") if o.strip()]
        if q.get("type") == "select" and answer and options and answer not in options:
            raise HTTPException(status_code=422, detail=f"Pick one of the options for \"{label}\".")
    tiers = [t.get("name") for t in (event.payments or {}).get("tiers") or [] if isinstance(t, dict)]
    if (event.payments or {}).get("enabled") and tiers and body.pass_name not in tiers:
        raise HTTPException(status_code=422, detail="Pick one of the passes.")

async def _check_players(event: BracketEvents, profiles: list[Profiles], db: AsyncSession, entry: BracketEntries | None = None) -> None:
    """
    Nobody running the event plays, nobody plays for two entrants (the unique index backs this
    up), and nobody plays two events that start at the exact same time.
    """

    staff = await access.staff_accounts(event, db)
    for p in profiles:
        if str(p.account_id) in staff:
            raise HTTPException(status_code=409, detail=f"{p.username} is helping run this event, so they can't play in it.")
    ids = [p.id for p in profiles]
    if not ids:
        return
    taken = select(Profiles.username).join(BracketEntryPlayers, BracketEntryPlayers.profile_id == Profiles.id) \
        .where(BracketEntryPlayers.event_id == event.id).where(BracketEntryPlayers.profile_id.in_(ids))
    if entry is not None:
        taken = taken.where(BracketEntryPlayers.entry_id != entry.id)
    taken = (await db.execute(taken)).scalars().first()
    if taken:
        raise HTTPException(status_code=409, detail=f"{taken} is already playing in this event.")
    clash = (await db.execute(
        select(Profiles.username, BracketEvents.name)
        .join(BracketEntryPlayers, BracketEntryPlayers.profile_id == Profiles.id)
        .join(BracketEvents, BracketEvents.id == BracketEntryPlayers.event_id)
        .where(BracketEntryPlayers.profile_id.in_(ids)).where(BracketEvents.id != event.id)
        .where(BracketEvents.starts_at == event.starts_at)
    )).first()
    if clash:
        raise HTTPException(status_code=409, detail=f"{clash[0]} is playing in {clash[1]}, which starts at the same time.")

async def _team_players(team: Teams, public_ids: list[str], limit: int, db: AsyncSession) -> list[Profiles]:
    if len(set(public_ids)) != len(public_ids):
        raise HTTPException(status_code=422, detail="Pick each player once.")
    if len(public_ids) > limit:
        raise HTTPException(status_code=422, detail=f"Pick up to {limit} players.")
    if not public_ids:
        return []
    rows = (await db.execute(
        select(Profiles).join(TeamMembers, TeamMembers.profile_id == Profiles.id)
        .where(TeamMembers.team_id == team.id).where(Profiles.public_id.in_(public_ids))
    )).scalars().all()
    if len(rows) != len(public_ids):
        raise HTTPException(status_code=422, detail=f"Pick players who are on {team.name}.")
    order = {pid: i for i, pid in enumerate(public_ids)}
    return sorted(rows, key=lambda p: order[p.public_id])

async def register(user_id: str, public_id: str, body: Registration, db: AsyncSession) -> dict:
    event = await access.event_by_public_id(public_id, db, lock = True)
    bracket = await _bracket(event.id, db)
    # Sign-ups stay open while the host drafts the seeding; publishing rechecks the field.
    if (bracket and bracket.status != "draft") or not event.registration_open:
        raise HTTPException(status_code=409, detail="Sign-ups are closed.")
    if str(user_id) in await access.staff_accounts(event, db):
        raise HTTPException(status_code=409, detail="You're helping run this event, so you can't sign up for it.")
    _check_signup(event, body)
    entries = await access.entries_of(event.id, db)
    has_room = event.max_entrants is None or sum(1 for e in entries if e.status == "signed_up") < event.max_entrants

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
        await _check_players(event, [player], db)
        entry = BracketEntries(id = uuid.uuid4(), event_id = event.id, profile_id = player.id, team_id = None, name = player.username,
                               status = "signed_up", email = body.email, answers = body.answers, pass_name = body.pass_name)
        chosen, team_id = [player], None
    else:
        if not body.team_id:
            raise HTTPException(status_code=422, detail=f"This is a {event.team_size}v{event.team_size} event - pick a team.")
        team = (await db.execute(select(Teams).where(Teams.public_id == body.team_id))).scalars().first()
        role = (await access.my_team_roles(user_id, db)).get(team.id) if team else None
        if not team or role is None:
            raise HTTPException(status_code=404, detail="Team not found.")
        if role not in ("owner", "captain"):
            raise HTTPException(status_code=403, detail="Only the team's owner or a captain can enter it.")
        plays = (await db.execute(select(TeamGames.game_id).where(TeamGames.team_id == team.id).where(TeamGames.game_id == event.game_id))).first()
        if not plays:
            game = await db.get(Games, event.game_id)
            raise HTTPException(status_code=409, detail=f"{team.name} doesn't play {game.name}. Add it to the team's games to enter.")
        chosen = await _team_players(team, body.players, event.team_size, db)
        await _check_players(event, chosen, db)
        registrant = team.owner_profile_id if role == "owner" else (await db.execute(
            select(Profiles.id).join(TeamMembers, TeamMembers.profile_id == Profiles.id)
            .where(TeamMembers.team_id == team.id).where(Profiles.account_id == user_id)
        )).scalar()
        entry = BracketEntries(id = uuid.uuid4(), event_id = event.id, profile_id = registrant, team_id = team.id, name = team.name,
                               status = "signed_up" if len(chosen) >= event.team_size and has_room else "pending",
                               email = body.email, answers = body.answers, pass_name = body.pass_name)
        team_id = team.id

    db.add(entry)
    try:
        await db.flush()
        db.add_all([BracketEntryPlayers(id = uuid.uuid4(), event_id = event.id, entry_id = entry.id, team_id = team_id, profile_id = p.id) for p in chosen])
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already signed up, or one of these players is already in this event.")
    return await _event_out(event, user_id, db)

async def set_players(user_id: str, public_id: str, player_ids: list[str], db: AsyncSession) -> dict:
    """
    The captain changes which of the team's players play, until the bracket is published.
    """

    event = await access.event_by_public_id(public_id, db, lock = True)
    bracket = await _bracket(event.id, db)
    if bracket and bracket.status != "draft":
        raise HTTPException(status_code=409, detail="The event has started, so its roster is locked.")
    entry = await access.acting_entry(user_id, event, db)
    if entry.team_id is None:
        raise HTTPException(status_code=422, detail="Only team entries pick players.")
    team = await db.get(Teams, entry.team_id)
    chosen = await _team_players(team, player_ids, event.team_size, db)
    await _check_players(event, chosen, db, entry)
    await db.execute(delete(BracketEntryPlayers).where(BracketEntryPlayers.entry_id == entry.id))
    db.add_all([BracketEntryPlayers(id = uuid.uuid4(), event_id = event.id, entry_id = entry.id, team_id = team.id, profile_id = p.id) for p in chosen])
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="One of these players is already in this event.")
    await _refresh_and_notify(event, await access.entries_of(event.id, db), db)
    await db.commit()
    return await _event_out(event, user_id, db)

async def _remove_entry(event: BracketEvents, entry: BracketEntries, db: AsyncSession) -> None:
    await db.delete(entry)
    await db.flush()
    await _refresh_and_notify(event, await access.entries_of(event.id, db), db)
    await db.commit()

async def unregister(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    Takes the signed in player out, or the team they own or captain.
    """

    event = await access.event_by_public_id(public_id, db, lock = True)
    bracket = await _bracket(event.id, db)
    if bracket and bracket.status != "draft":
        raise HTTPException(status_code=409, detail="The bracket is live - you can't unregister now.")
    found = await access.viewer_entry(user_id, await access.entries_of(event.id, db), db)
    if not found:
        raise HTTPException(status_code=404, detail="You're not signed up.")
    if not found[1]:
        raise HTTPException(status_code=403, detail="Only the team's owner or a captain can unregister it.")
    await _remove_entry(event, found[0], db)
    return await _event_out(event, user_id, db)

async def remove_entrant(user_id: str, public_id: str, entrant_id: uuid.UUID, db: AsyncSession) -> dict:
    event = await access.managed_event(user_id, public_id, db, lock = True)
    bracket = await _bracket(event.id, db)
    if bracket and bracket.status != "draft":
        raise HTTPException(status_code=409, detail="The bracket is live, so the entrant list is locked.")
    entry = await db.get(BracketEntries, entrant_id)
    if not entry or entry.event_id != event.id:
        raise HTTPException(status_code=404, detail="Entrant not found.")
    await _remove_entry(event, entry, db)
    return await _event_out(event, user_id, db)

# ---- teams (called from app.services.teams) ----------------------------------------

async def sync_team(team_id, db: AsyncSession) -> None:
    """
    After a team's members or name change: its entries in events that haven't started take the
    new name, drop picked players who left the team, and move between signed up and pending.
    Events that have started keep their rosters as they were. The caller commits.
    """

    team = await db.get(Teams, team_id)
    if not team:
        return
    members = set((await db.execute(select(TeamMembers.profile_id).where(TeamMembers.team_id == team_id))).scalars().all())
    for entry in (await db.execute(select(BracketEntries).where(BracketEntries.team_id == team_id))).scalars().all():
        bracket = await _bracket(entry.event_id, db)
        if bracket and bracket.status != "draft":
            continue
        entry.name = team.name
        gone = delete(BracketEntryPlayers).where(BracketEntryPlayers.entry_id == entry.id)
        if members:
            gone = gone.where(or_(BracketEntryPlayers.profile_id.is_(None), BracketEntryPlayers.profile_id.not_in(members)))
        await db.execute(gone)
        await db.flush()
        event = await db.get(BracketEvents, entry.event_id)
        await _refresh_and_notify(event, await access.entries_of(event.id, db), db)

async def before_team_deleted(team_id, db: AsyncSession) -> list:
    """
    Drops the team's sign-ups for events that haven't started; events that have started keep the
    entry (the team is unlinked). Returns the event ids whose statuses need refreshing.
    """

    upcoming = []
    for entry in (await db.execute(select(BracketEntries).where(BracketEntries.team_id == team_id))).scalars().all():
        bracket = await _bracket(entry.event_id, db)
        if not bracket or bracket.status == "draft":
            upcoming.append(entry.event_id)
            await db.delete(entry)
    await db.flush()
    return upcoming

async def refresh_events(event_ids, db: AsyncSession) -> None:
    for event_id in event_ids:
        event = await db.get(BracketEvents, event_id)
        if event:
            await _refresh_and_notify(event, await access.entries_of(event.id, db), db)

# ---- seeding and publishing ----------------------------------------------------------

async def save_draft(user_id: str, public_id: str, seeds: list[str], db: AsyncSession) -> dict:
    """
    Saves (or replaces) the draft seeding. The response carries the bracket it makes, so the
    seeding page previews it straight from the server.
    """

    event = await access.managed_event(user_id, public_id, db, lock = True)
    bracket = await _bracket(event.id, db, lock = True)
    if bracket and bracket.status != "draft":
        raise HTTPException(status_code=409, detail="The bracket is already live. Reset it to reseed.")
    entries = await access.entries_of(event.id, db)
    allowed = {str(e.id) for e in entries if e.status == "signed_up"}
    if len(set(seeds)) != len(seeds) or not set(seeds) <= allowed:
        raise HTTPException(status_code=422, detail="Seeds must be signed-up entrants, each once. Pending teams can't be seeded.")
    fmt = await lookups.bracket_format(event.format, db)
    if len(seeds) < fmt.min_entrants:
        raise HTTPException(status_code=422, detail=f"You need at least {fmt.min_entrants} entrants to build a bracket.")
    if bracket is None:
        bracket = Brackets(id = uuid.uuid4(), event_id = event.id, format = event.format, status = "draft",
                           created_by_profile_id = await issues.acting_profile(user_id, event, db))
        db.add(bracket)
        await db.flush()
    await _write_seeds(bracket, seeds, db)
    await db.commit()
    return await _event_out(event, user_id, db)

async def _write_seeds(bracket: Brackets, seeds: list[str], db: AsyncSession) -> None:
    await db.execute(delete(BracketSeeds).where(BracketSeeds.bracket_id == bracket.id))
    db.add_all([BracketSeeds(bracket_id = bracket.id, entry_id = uuid.UUID(s), seed = i + 1) for i, s in enumerate(seeds)])
    await db.flush()

async def publish(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    Posts the bracket: rechecks the field, closes sign-ups, locks the event's rosters, creates the
    matches and tells everyone with a match. If the field changed since the draft was seeded, the
    draft is rebuilt with the current entrants and sent back for review instead (409).
    """

    event = await access.managed_event(user_id, public_id, db, lock = True)
    bracket = await _bracket(event.id, db, lock = True)
    if not bracket:
        raise HTTPException(status_code=409, detail="Save a seeding first.")
    if bracket.status != "draft":
        return await _event_out(event, user_id, db)

    entries = await access.entries_of(event.id, db, lock = True)
    await _refresh_and_notify(event, entries, db)
    signed_up = [e for e in entries if e.status == "signed_up"]
    if event.min_entrants is not None and len(signed_up) < event.min_entrants:
        raise HTTPException(status_code=409, detail=(
            f"Only {len(signed_up)} signed up, and this event needs at least {event.min_entrants} to start. It can start once more sign up."
        ))
    staff = await access.staff_accounts(event, db)
    playing = await access.player_accounts(signed_up, db)
    for e in signed_up:
        if playing.get(e.id, set()) & staff:
            raise HTTPException(status_code=409, detail=f"{e.name} has a player who's running this event. Hosts and admins can't play in it.")

    current = [str(e.id) for e in signed_up]
    seeds = await bsync.seeds_of(bracket, db)
    if set(seeds) != set(current):
        kept = [x for x in seeds if x in set(current)]
        await _write_seeds(bracket, kept + [x for x in current if x not in set(kept)], db)
        await db.commit()
        raise HTTPException(status_code=409, detail=(
            "The entrants changed since this bracket was seeded, so it's been rebuilt with the current field. "
            "Review the new bracket, then publish again."
        ))

    bracket.status, bracket.published_at = "published", _now()
    event.registration_open = False
    await db.flush()
    await play.notify_ready(db, event, await bsync.create_matches(event, bracket, db))
    await db.commit()
    return await _event_out(event, user_id, db)

async def reset_bracket(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    Throws the bracket away (draft or live, with its matches, reports and queue items) so the
    event can be reseeded.
    """

    event = await access.managed_event(user_id, public_id, db, lock = True)
    bracket = await _bracket(event.id, db, lock = True)
    if bracket:
        await db.execute(delete(EventIssues).where(EventIssues.event_id == event.id))
        await db.delete(bracket)
        await db.flush()
    await _refresh_and_notify(event, await access.entries_of(event.id, db), db)
    await db.commit()
    return await _event_out(event, user_id, db)

# ---- admins ----------------------------------------------------------------------------
#
# The host (only the host) invites player profiles to help run an event. The pending invite is
# the bell item (Accept / Decline). Once accepted they manage the event like the host, except
# deleting it or changing admins. Someone signed up to play can't be invited; they withdraw first.

async def _assert_not_playing(event: BracketEvents, account_id, message: str, db: AsyncSession) -> None:
    entries = await access.entries_of(event.id, db)
    playing = await access.player_accounts(entries, db)
    registrants = set((await db.execute(
        select(Profiles.account_id).join(BracketEntries, BracketEntries.profile_id == Profiles.id).where(BracketEntries.event_id == event.id)
    )).scalars().all())
    if any(str(account_id) in accounts for accounts in playing.values()) or account_id in registrants:
        raise HTTPException(status_code=409, detail=message)

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
        raise HTTPException(status_code=409, detail=f"{profile.username} is already {'an admin' if existing.status == 'accepted' else 'invited'}.")
    count = (await db.execute(select(func.count()).where(BracketEventAdmins.event_id == event.id))).scalar()
    if count >= MAX_ADMINS:
        raise HTTPException(status_code=409, detail=f"An event can have up to {MAX_ADMINS} admins.")
    await _assert_not_playing(event, profile.account_id, f"{profile.username} is signed up to play in this event. They need to withdraw before you can invite them as an admin.", db)
    db.add(BracketEventAdmins(id = uuid.uuid4(), event_id = event.id, profile_id = profile.id, status = "pending"))

async def invite_admin(user_id: str, public_id: str, body: AdminInvite, db: AsyncSession) -> dict:
    event = await access.owned_event(user_id, public_id, db, lock = True)
    host = await db.get(Profiles, event.host_profile_id)
    await _invite_admin(event, host, body.profile, db)
    await db.commit()
    return await _event_out(event, user_id, db)

async def remove_admin(user_id: str, public_id: str, admin_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    The host removes an admin (or cancels an invite), or an admin steps down.
    """

    event = await access.event_by_public_id(public_id, db, lock = True)
    admin = (await db.execute(
        select(BracketEventAdmins).where(BracketEventAdmins.id == admin_id).where(BracketEventAdmins.event_id == event.id)
    )).scalars().first()
    mine = await access.my_profile_ids(user_id, db)
    if not admin or (event.host_profile_id not in mine and admin.profile_id not in mine):
        raise HTTPException(status_code=404, detail="Admin not found.")
    await db.delete(admin)
    await db.commit()
    return await _event_out(event, user_id, db)

async def my_admin_invites(user_id: str, db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        select(BracketEventAdmins, BracketEvents, Profiles, Games.name)
        .join(BracketEvents, BracketEvents.id == BracketEventAdmins.event_id)
        .join(Profiles, Profiles.id == BracketEvents.host_profile_id)
        .join(Games, Games.id == BracketEvents.game_id)
        .where(BracketEventAdmins.profile_id.in_(select(Profiles.id).where(Profiles.account_id == user_id)))
        .where(BracketEventAdmins.status == "pending")
        .order_by(BracketEventAdmins.created_at.desc())
    )).all()
    return [
        {"id": str(a.id), "eventId": e.public_id, "eventName": e.name, "game": game, "startsAt": _iso(e.starts_at), "host": _profile_ref(h)}
        for a, e, h, game in rows
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
        me = await db.get(Profiles, invite.profile_id)
        await _assert_not_playing(event, me.account_id, "You're signed up to play in this event, so you can't be an admin of it. Withdraw first.", db)
        invite.status = "accepted"
        host = (await db.execute(select(Profiles.account_id).where(Profiles.id == event.host_profile_id))).scalar()
        notify(db, [host], "admin_joined", {"event_id": event.public_id, "event_name": event.name, "player": me.username})
    else:
        await db.delete(invite)
    await db.commit()
    return await my_admin_invites(user_id, db)

# ---- playing (each returns the updated event) ---------------------------------------------

async def report(user_id: str, public_id: str, code: str, body, db: AsyncSession) -> dict:
    return await _event_out(await play.report(user_id, public_id, code, body, db), user_id, db)

async def report_no_show(user_id: str, public_id: str, code: str, db: AsyncSession) -> dict:
    return await _event_out(await play.report_no_show(user_id, public_id, code, db), user_id, db)

async def dispute(user_id: str, public_id: str, code: str, db: AsyncSession) -> dict:
    return await _event_out(await play.dispute_after_advancing(user_id, public_id, code, db), user_id, db)

async def rule(user_id: str, public_id: str, code: str, body, db: AsyncSession) -> dict:
    return await _event_out(await play.rule(user_id, public_id, code, body, db), user_id, db)

async def revert(user_id: str, public_id: str, decision_id: uuid.UUID, db: AsyncSession) -> dict:
    return await _event_out(await play.revert(user_id, public_id, decision_id, db), user_id, db)

async def decisions(user_id: str, public_id: str, db: AsyncSession) -> list[dict]:
    """
    The event's rulings, newest first, for the host's and admins' decision history.
    """

    event = await access.managed_event(user_id, public_id, db)
    bracket = await _bracket(event.id, db)
    if not bracket:
        return []
    rows = (await db.execute(
        select(MatchDecisions, BracketMatches.code, Profiles.username)
        .join(BracketMatches, BracketMatches.id == MatchDecisions.match_id)
        .outerjoin(Profiles, Profiles.id == MatchDecisions.decided_by_profile_id)
        .where(BracketMatches.bracket_id == bracket.id).order_by(MatchDecisions.created_at.desc())
    )).all()
    reverted = {d.reverts_decision_id for d, _, _ in rows if d.reverts_decision_id}

    def result(status, a, b, winner):
        return {"status": status, "scoreA": a, "scoreB": b, "winner": str(winner) if winner else None}

    return [
        {
            "id": str(d.id), "matchId": code, "action": d.action, "by": who, "at": _iso(d.created_at),
            "before": result(d.prev_status, d.prev_score_a, d.prev_score_b, d.prev_winner_entry_id),
            "after": result(d.new_status, d.new_score_a, d.new_score_b, d.new_winner_entry_id),
            "reverted": d.id in reverted,
            "canRevert": d.action in ("award", "forfeit", "clear") and d.id not in reverted,
        }
        for d, code, who in rows
    ]
