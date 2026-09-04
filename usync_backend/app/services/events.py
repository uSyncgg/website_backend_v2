from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.host_data import LanEvents, LeagueEvents, WagerEvents, XpEvents, LeagueParentEvents
from app.schemas.events import LeagueParentsOut
from collections.abc import Sequence
from collections import defaultdict
from fastapi import HTTPException

EVENT_TYPE_LOOKUP = {
    "leagues": LeagueEvents,
    "wagers": WagerEvents,
    "lans": LanEvents,
    "head-to-head": XpEvents
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