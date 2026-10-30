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

EVENT_TYPE_STRING_LOOKUP = {
    "leagues": "league_events",
    "wagers": "wager_events",
    "lans": "lan_events",
    "head-to-head": "xp_events"
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
            is_college = parent.is_college,
            seo_title = parent.seo_title,
            seo_description = parent.seo_description
        )
        for parent in league_parents
        if parent.name in grouped
    ]

    fin_list = nested + standalone
    fin_list.sort(key=lambda item: item.verified, reverse = True)

    return fin_list

async def get_league_information(path: str, game: str, db: AsyncSession, parent: bool = True) -> LeagueEvents | LeagueParentEvents:
    """
    Asynchronous function to get league or league parent information based on provided params.

    ::param path the string param containing the path for the event
    ::param game the string containing the game of the event
    ::param db the Asynchronous database Session
    ::param parent a bool indicating whther we are looking for the parent or child information

    ::return either a league event or league parent event validated by either LeagueEvents or LeagueParentEvents
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
    Asynchronous function to get lan information based on provided params.

    ::param path the string containing the path of the lan
    ::param db the Asynchronous database Session

    ::return a lan event validated by the LanEvents schema
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
    Asynchronous function to get an event by its path.

    ::param event_type the string containing the event type
    ::param game the string containing the game
    ::param path the string containing the path
    ::param db the Asynchronous database Session

    ::return an event validated by its respective schema in the EventUnion
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

async def get_league_parents(game: str, db: AsyncSession) -> Sequence[LeagueParentEvents]:
    """
    Asynchronous function to get league parents for a game.

    ::param game the string containing the game
    ::param db the Asynchronous database Session

    ::return a sequnce containing all league parents for a game validated by LeagueParentEvents
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
    Asynchronous function to get all league children based on a parent.

    ::param game the string containing the game
    ::param parent the string containing the parent
    ::param db the Asynchronous database Session

    ::return a sequence containing league events validated by the LeagueEvents schema
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
    Asynchronous function to get all league events based on the game.

    ::param game the string containing the game
    ::param db the Asynchronous database Session

    ::return all league events validated by the LeagueEvents schema
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
    Asynchronous function to get all lans based on the game.

    ::param game the string containing the game
    ::param db the Asynchronous database Session

    ::return all lan events validated by the LanEvents schema
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
    Asynchronous function to get all non-archived lans stored in the db.

    ::param db the Asynchronous database Session

    ::return all lan events validated by the LanEvents schema
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
    Asynchronous function to get all wagers based on the game.

    ::param game the string containing the game
    ::param db the Asynchronous database Session

    ::return all wager events validated by the WagerEvents schema
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
    Asynchronous function to get all xp events based on the game.

    ::param game the string containing the game
    ::param db the Asynchronous database Session

    ::return all xp events validated by the XpEvents schema

    NOTE: XP events = Head-to-Head events
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
    Asynchronous function to build all verified events based on the game.

    ::param db the Asynchronous database Session
    ::param game the string containing the game

    ::return a dictionary where the key is the table name and the value are the verified events from that table
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
    """
    Asynchronous function to invalidate the verified games TTL cache.

    ::param cache the TTLCache to invalidate
    ::param game the string containing the game to invalidate
    """

    cache.pop(game, None)

    return None

async def get_verified_events(game: str, db: AsyncSession, cache: TTLCache) -> dict[str, Sequence[EventUnion]]:
    """
    Asynchronous function to get all verified events based on a game.

    ::param game the string containing the game
    ::param db the Asynchronous database Session
    ::param cache the TTLCache to populate with the verified events

    ::return a dictionary where the keys are the table names and the values are sequences of validated events from that table
    """

    if (verified_events := cache.get(game)) is not None:
        return verified_events

    verified_events = await _build_verified_event(db, game)

    cache[game] = verified_events

    return verified_events

async def populate_verified_events_cache(db: AsyncSession, cache: TTLCache) -> None:
    """
    Asynchronous function to populate the verified events cache on startup of the backend.

    ::param db the Asynchronous database Session
    ::param cache the TTLCache to populate    
    """


    for game in GAMES:
        await get_verified_events(game, db, cache)

    return None

async def _query_table(db: AsyncSession, table_name: str) -> Sequence[EventUnion]:
    """
    
    """

    table = TABLE_NAME_LOOKUP[table_name]

    stmt = (
        select(table)
        .where(table.status != "pending", table.verified.is_(True))
    )

    result = await db.execute(stmt)
    return result.scalars().all()

async def _build_verified_event_type(db: AsyncSession, table_name: str) -> dict[str, Sequence[EventUnion]]:
    """
    Asynchronous function to build all verified events based on the event type.

    ::param db the Asynchronous database Session
    ::param table_name the string containing the table name

    ::return a dictionary where the key is the table name and the value are the verified events from that table
    """

    verified_events: dict[str, list[EventUnion]] = {}
    parent_names: set[str] = set()

    if table_name == "league_events":
        parent_events = await _query_table(db, "league_parent_events")
        parent_names.update(event.name for event in parent_events)

        events = await _query_table(db, table_name)
        events = [event for event in events if event.group not in parent_names]
    else:
        events = await _query_table(db, table_name)

    verified_events[table_name] = events

    return verified_events

async def invalidate_verified_events_type_cache(cache: TTLCache, table_name: str) -> None:
    """
    Asynchronous function to invalidate the verified games TTL cache by event type.

    ::param cache the TTLCache to invalidate
    ::param table_name the string containing the table name to invalidate
    """

    cache.pop(table_name, None)

    return None

async def get_verified_events_type(event_type: str, db: AsyncSession, cache: TTLCache) -> dict[str, Sequence[EventUnion]]:
    """
    Asynchronous function to get all verified events based on event type.

    ::param event_type the string containing the event_type
    ::param db the Asynchronous database Session
    ::param cache the TTLCache to populate with the verified events

    ::return a dictionary where the keys are the table names and the values are sequences of validated events from that table
    """
    table_name = EVENT_TYPE_STRING_LOOKUP[event_type]

    if (verified_events := cache.get(table_name)) is not None:
        return verified_events

    verified_events = await _build_verified_event_type(db, table_name)

    cache[table_name] = verified_events

    return verified_events

async def populate_verified_events_type_cache(db: AsyncSession, cache: TTLCache) -> None:
    """
    Asynchronous function to populate the verified events cache on startup of the backend based on event type.

    ::param db the Asynchronous database Session
    ::param cache the TTLCache to populate    
    """

    for event_type in EVENT_TYPE_STRING_LOOKUP:
        await get_verified_events_type(event_type, db, cache)

    return None