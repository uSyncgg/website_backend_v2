from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db
from app.schemas.event_forms import FormReviewIn, FormReviewOut, FormSubmissionIn, FormSubmissionOut
from app.schemas.events import LeaguesOut, LansOut, WagersOut, XpsOut, LeagueParentsOut
from app.services.events import get_lans, get_leagues, get_wagers, get_xps, get_league_parents, league_nesting, get_event

router = APIRouter(prefix="/events", tags=["Events"])

EventModel = LeaguesOut | LansOut | WagersOut | XpsOut

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
