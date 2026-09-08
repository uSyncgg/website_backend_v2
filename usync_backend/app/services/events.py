from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.host_data import LanEvents, LeagueEvents, WagerEvents, XpEvents, LeagueParentEvents
from app.schemas.events import LeagueParentsOut
from collections.abc import Sequence
from collections import defaultdict
from fastapi import HTTPException
from cachetools import TTLCache
from app.services import GAMES

EVENT_TYPE_LOOKUP = {
    "leagues": LeagueEvents,
    "wagers": WagerEvents,
    "lans": LanEvents,
    "head-to-head": XpEvents
}

TABLE_NAME_LOOKUP = {
    "league_parent_events": LeagueParentEvents,
    "league_events": LeagueEvents,
    "wager_events": WagerEvents,
    "lan_events": LanEvents,
    "xp_events": XpEvents
}

type EventUnion = (
    LanEvents |
    LeagueEvents |
    WagerEvents |
    XpEvents
)

def league_nesting(leagues: Sequence[LeagueEvents], league_parents: Sequence[LeagueParentEvents]) -> Sequence[LeagueEvents | LeagueParentsOut]:
    """
    Modify the Sequence of leagues to have nested leagues specifically for those with matching groups.

    ::param leagues the Sequence of leagues

    ::return the updated Sequence of leagues
    """

    grouped = defaultdict(list)
    standalone = []

    for league in leagues:
        if league.group is None:
            standalone.append(league)
        else:
            grouped[league.group].append(league)

    nested = [
        LeagueParentsOut(
            name = parent.name,
            banner_img = parent.banner_img,
            header_img = parent.header_img,
            verified = parent.verified,
            path = parent.path,
            game = parent.game,
            leagues = grouped[parent.name],
            is_hs = parent.is_hs,
            is_college = parent.is_college
        )
        for parent in league_parents
        if parent.name in grouped
    ]

    fin_list = nested + standalone
    fin_list.sort(key=lambda item: item.verified, reverse = True)

    return fin_list

async def get_league_information(path: str, game: str,  db: AsyncSession, parent: bool = True):
    """
    
    """

    if parent is False:
        path = f"/{path}"

    table = LeagueParentEvents if parent else LeagueEvents

    stmt = (
        select(table)
        .where(table.path == path, table.game == game, table.status != "pending")
    )

    result = await db.execute(stmt)
    event = result.scalars().first()

    if event is None and parent is False:
        raise HTTPException(status_code=404, detail=f"Event not found: {path}")

    return event


async def get_lan_information(path: str, db: AsyncSession) -> LanEvents:
    """
    
    """

    path = f"/{path}"

    stmt = (
        select(LanEvents)
        .where(LanEvents.path == path)
    )

    result = await db.execute(stmt)
    event = result.scalars().first()

    if event is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {path}")

    return event

async def get_event_by_path(event_type: str, game: str, path: str, db: AsyncSession) -> EventUnion:
    """
    
    """

    event_class = EVENT_TYPE_LOOKUP.get(event_type)
    path = f"/{path}"

    if event_class is None:
        raise HTTPException(status_code=404, detail=f"Unknown event type: {event_type}")

    stmt = (
        select(event_class)
        .where(event_class.path == path, event_class.game == game)
    )

    result = await db.execute(stmt)
    event = result.scalars().first()

    if event is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {path}")

    return event

async def get_event(event_type: str, game: str, event_name: str, db: AsyncSession) -> EventUnion:
    """
    
    """

    event_class = EVENT_TYPE_LOOKUP.get(event_type)

    if event_class is None:
      raise HTTPException(status_code=404, detail=f"Unknown event type: {event_type}")

    stmt = (
        select(event_class)
        .where(event_class.name == event_name, event_class.game == game)
    )

    result = await db.execute(stmt)
    event = result.scalars().first()

    if event is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_name}")

    return event

async def get_league_parents(game: str, db: AsyncSession) -> Sequence[LeagueParentEvents]:
    """
    
    """

    stmt = (
        select(LeagueParentEvents)
        .where(LeagueParentEvents.status != "pending", LeagueParentEvents.game == game)
        .order_by(LeagueParentEvents.verified.desc())
    )
    
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_league_children(game: str, parent: str, db: AsyncSession) -> Sequence[LeagueEvents]:
    """
    
    """

    stmt = (
        select(LeagueEvents)
        .where(LeagueEvents.status != "pending", LeagueEvents.game == game, LeagueEvents.group == parent)
    )

    result = await db.execute(stmt)
    events = result.scalars().all()

    if len(events) == 0:
        raise HTTPException(status_code=404, detail=f"Parent does not exist: {parent}")

    return events

async def get_leagues(game: str, db: AsyncSession) -> Sequence[LeagueEvents]:
    """
    
    """

    stmt = (
        select(LeagueEvents)
        .where(LeagueEvents.status != "pending", LeagueEvents.game == game)
        .order_by(LeagueEvents.verified.desc())
    )
    
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_lans(game: str, db: AsyncSession) -> Sequence[LanEvents]:
    """
    
    """

    stmt = (
        select(LanEvents)
        .where(LanEvents.status != "pending", LanEvents.archived.is_(False), LanEvents.game == game)
        .order_by(LanEvents.verified.desc())
    )
    
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_all_lans(db: AsyncSession) -> Sequence[LanEvents]:
    """
    
    """

    stmt = (
        select(LanEvents)
        .where(LanEvents.status != "pending", LanEvents.archived.is_(False))
        .order_by(LanEvents.verified.desc())
    )

    result = await db.execute(stmt)
    return result.scalars().all()

async def get_wagers(game: str, db: AsyncSession) -> Sequence[WagerEvents]:
    """
    
    """

    stmt = (
        select(WagerEvents)
        .where(WagerEvents.status != "pending", WagerEvents.game == game)
        .order_by(WagerEvents.verified.desc())
    )
    
    result = await db.execute(stmt)
    return result.scalars().all()

async def get_xps(game: str, db: AsyncSession) -> Sequence[XpEvents]:
    """
    
    """

    stmt = (
        select(XpEvents)
        .where(XpEvents.status != "pending", XpEvents.game == game)
        .order_by(XpEvents.verified.desc())
    )
    
    result = await db.execute(stmt)
    return result.scalars().all()

async def _build_verified_event(db: AsyncSession, game: str) -> dict[str, Sequence[EventUnion]]:
    """
    
    """

    verified_events: dict[str, list[EventUnion]] = {}
    parent_names: set[str] = set()

    for table_name in ["league_parent_events", "league_events", "wager_events", "xp_events", "lan_events"]:
        table = TABLE_NAME_LOOKUP[table_name]

        stmt = (
            select(table)
            .where(table.status != "pending", table.game == game, table.verified.is_(True))
        )

        result = await db.execute(stmt)
        events = result.scalars().all()

        if table_name == "league_parent_events":
            parent_names.update(event.name for event in events)
        elif table_name == "league_events":
            events = [event for event in events if event.group not in parent_names]

        verified_events[table_name] = events

    return verified_events

async def invalidate_verified_events_cache(cache: TTLCache, game: str) -> None:
    cache.pop(game, None)

    return None

async def get_verified_events(game: str, db: AsyncSession, cache: TTLCache) -> dict[str, Sequence[EventUnion]]:
    """
    
    """

    if (verified_events := cache.get(game)) is not None:
        return verified_events

    verified_events = await _build_verified_event(db, game)

    cache[game] = verified_events

    return verified_events

async def populate_verified_events_cache(db: AsyncSession, cache: TTLCache) -> None:
    for game in GAMES:
        await get_verified_events(game, db, cache)

    return None
