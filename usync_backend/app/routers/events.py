from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, get_verified_cache, verify_sitemap_token
from app.schemas.event_forms import FormReviewIn, FormReviewOut, FormSubmissionIn, FormSubmissionOut
from app.schemas.events import LeaguesOut, LansOut, WagersOut, XpsOut, LeagueParentsOut
from cachetools import TTLCache
from app.services.events import (
    get_lans, 
    get_leagues, 
    get_wagers, 
    get_xps, 
    get_league_parents, 
    league_nesting, 
    get_league_children, 
    get_event_by_path,
    get_lan_information,
    get_league_information,
    get_verified_events,
    invalidate_verified_events_cache,
    get_all_lans,
    get_verified_events_type,
    invalidate_verified_events_type_cache
)

router = APIRouter(prefix="/events", tags=["Events"])

EventModel = LeaguesOut | LansOut | WagersOut | XpsOut

@router.get("/leagues/{game}/{parent}/children", response_model=list[LeaguesOut])
async def league_children(game: str, parent: str, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to return the league children within a parent.

    ::param game the game the user is looking at
    ::param parent the parent league that owns the children
    ::param db the asynchronous db session

    ::return a list of the League response model
    """

    return await get_league_children(game, parent, db)

@router.get("/leagues/{game}/{path:path}/information", response_model = LeagueParentsOut | LeaguesOut)
async def league_information(path: str, game: str, db: AsyncSession = Depends(get_db)):
    """
    GET route to fetch leagues or their parents.

    ::param path the string containing the frontend path slug
    ::param game the string containing the game
    ::param db the Asynchronous Session depending on the local session to acquire the db object

    ::return either a league parent or a league validated by either the LeagueParentsOut or LeaguesOut schema
    """

    parent = await get_league_information(path, game, db)

    if parent:
        events = await get_league_children(game, parent.name, db)
        return league_nesting(events, [parent])[0]

    return await get_league_information(path, game, db, parent = False)    

@router.get("/leagues/{game}", response_model=list[LeaguesOut | LeagueParentsOut])
async def leagues(game: str, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to return all leagues related to the provided game.

    ::param game the game string for the desired game's leagues
    ::param db the asynchronous db session

    ::return the response model containing all league information related to the game
    """

    leagues = await get_leagues(game, db)
    league_parents = await get_league_parents(game, db)

    return league_nesting(leagues, league_parents)

@router.get("/lans/{game}", response_model=list[LansOut])
async def lans(game: str, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to return all lans related to the provided game.

    ::param game the game string for the desired game's lans
    ::param db the asynchronous db session

    ::return the reponse model containing all lan information related to the game
    """

    return await get_lans(game, db)

@router.get("/all_lans", response_model=list[LansOut])
async def lans(db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to return all non-archived lans.

    ::param db the asynchronous db session

    ::return the reponse model containing all non-archived lans
    """

    return await get_all_lans(db)

@router.get("/lans/{path}/information", response_model=EventModel)
async def lan_information(path: str, db: AsyncSession = Depends(get_db)):
    """
    GET route to get a lan event's information.

    ::param path the string containing the frontend path slug
    ::param db the Asynchronous Session depending on the local session to acquire the db object

    ::return a lan event validated by the EventModel schema union
    """

    return await get_lan_information(path, db)

@router.get("/wagers/{game}", response_model=list[WagersOut])
async def wagers(game: str, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to return all wagers related to the provided game.

    ::param game the game string for the desired game's wagers
    ::param db the asynchronous db session

    ::return the response model containing all wager information related to the game
    """

    return await get_wagers(game, db)

@router.get("/head-to-head/{game}", response_model=list[XpsOut])
async def xps(game: str, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to return all xps related to the provided game.

    ::param game the game string for the desired game's xps
    ::param db the asynchronous db session

    ::return the response model containing all xp information related to the game
    """

    return await get_xps(game, db)

@router.get("/{event_type}/{game}/{path}", response_model = EventModel)
async def event_information_by_path(event_type: str, game: str, path: str, db: AsyncSession = Depends(get_db)):
    """
    GET request to get an event's information by its path.

    ::param event_type the string indicating the evetn's type
    ::param game the string indicating what game the event is under
    ::param path the string indicating the frontend path slug
    ::param db the Asynchronous Session depending on the local session to acquire the db object

    ::return an event validated by the EventModel schema union
    """

    return await get_event_by_path(event_type, game, path, db)

@router.get("/{game}/verified", response_model=dict[str, list[LeaguesOut | LeagueParentsOut | WagersOut | XpsOut | LansOut]])
async def verified_events(game: str, db: AsyncSession = Depends(get_db), cache: TTLCache = Depends(get_verified_cache)):
    """
    GET route to fetch all verified events for a specific game.

    ::param game the string containing the game to lookup
    ::param db the Asynchronous Session depending on the local session to acquire the db object
    ::param cache the TTL cache depending on the local session

    ::return a dictionary containing a key showing the event type where its value is a list of events under that type validated by their respective schema
    """

    return await get_verified_events(game, db, cache)

@router.get("/{event_type}/verified/event/type", response_model=dict[str, list[LeaguesOut | LeagueParentsOut | WagersOut | XpsOut | LansOut]])
async def verified_events_type(event_type: str, db: AsyncSession = Depends(get_db), cache: TTLCache = Depends(get_verified_cache)):
    """
    GET route to fetch all verified events for a specific event type.

    ::param event_type the string containing the table to query
    ::param db the Asynchronous Session depending on the local session to acquire the db object
    ::param cache the TTL cache depending on the local session

    ::return a dictionary containing a key showing the event type where its value is a list of events under that type validated by their respective schema
    """

    return await get_verified_events_type(event_type, db, cache)

### NOTE: Need to modify verify sitemap token to be a general verify token function - will do when we setup the full invalidation for events.
@router.post("/{game}/verified/invalidate")
async def invalidate_verified_events(game: str, cache: TTLCache = Depends(get_verified_cache), _: None = Depends(verify_sitemap_token)):
    """
    POST route to invalidate the verified events when new verified events are posted.

    ::param game the string containing the game to invalidate
    ::param cache the TTL cache depending on the local session
    ::param _ the token verification depending on the local session
    """

    await invalidate_verified_events_cache(cache, game)

    return {"status": "invalidated"}

### NOTE: Need to modify verify sitemap token to be a general verify token function - will do when we setup the full invalidation for events.
@router.post("/{event_type}/verified/invalidate/event-type")
async def invalidate_verified_events_type(event_type: str, cache: TTLCache = Depends(get_verified_cache), _: None = Depends(verify_sitemap_token)):
    """
    POST route to invalidate the verified events by event type when new verified events are posted.

    ::param event_type the string containing the event type to invalidate
    ::param cache the TTL cache depending on the local session
    ::param _ the token verification depending on the local session
    """

    await invalidate_verified_events_type_cache(cache, event_type)

    return {"status": "invalidated"}

