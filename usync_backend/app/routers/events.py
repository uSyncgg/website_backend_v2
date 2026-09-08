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
    get_event, 
    get_league_children, 
    get_event_by_path,
    get_lan_information,
    get_league_information,
    get_verified_events,
    invalidate_verified_events_cache,
    get_all_lans
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

@router.get("/lans/{path}/information", response_model=EventModel)
async def lan_information(path: str, db: AsyncSession = Depends(get_db)):
    """
    
    """

    return await get_lan_information(path, db)

@router.get("/{event_type}/{game}/{path}", response_model = EventModel)
async def event_information_by_path(event_type: str, game: str, path: str, db: AsyncSession = Depends(get_db)):
    """
    
    """

    return await get_event_by_path(event_type, game, path, db)

@router.get("/{event_type}/{game}/{event}", response_model = EventModel)
async def event_information(event_type: str, game: str, event: str, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to return event information for a specific event specified by the user.

    ::param event_type the string denoting leagues, lans, wagers, head-to-head
    ::param game the game string
    ::param event the string denoting the event name
    ::param db the asynchronous db session

    ::return the response model containing event information
    """

    return await get_event(event_type, game, event, db)

@router.get("/{game}/verified", response_model=dict[str, list[LeaguesOut | LeagueParentsOut | WagersOut | XpsOut | LansOut]])
async def verified_events(game: str, db: AsyncSession = Depends(get_db), cache: TTLCache = Depends(get_verified_cache)):
    """
    
    """

    return await get_verified_events(game, db, cache)

@router.post("/{game}/verified/invalidate")
async def invalidate_verified_events(game: str, cache: TTLCache = Depends(get_verified_cache), _: None = Depends(verify_sitemap_token)):
    await invalidate_verified_events_cache(cache, game)

    return {"status": "invalidated"}


@router.post("/form/review", response_model=FormReviewOut)
def review_form_data(payload: FormReviewIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to review form data and return any issues with unique fields.
    """

    # Need to return function

@router.post("/form/submit", response_model=FormSubmissionOut)
def submit_form_data(payload: FormSubmissionIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to submit form data to the Supabase DB - If there is a duplicate record made at the exact moment of submission an error will be returned.
    """

    # Need to return function
