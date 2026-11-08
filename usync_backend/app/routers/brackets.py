import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt, optional_supabase_jwt
from app.core.limiter import limiter, MODERATE
from app.schemas.brackets import EventCreate, EventUpdate, EventDelay, Registration, SeedsUpdate, MatchScore, NotificationsRead
from app.services import brackets as svc
from app.services.notifications import list_notifications, mark_read

# Bracket hosting. Every route returns the event as the bracket pages use it (camelCase), with
# viewerEntrantId / viewerCanAct / canManage filled in for the signed in account. Host-only
# routes 404 for anyone else.
router = APIRouter(prefix="/brackets", tags=["Brackets"])
notifications_router = APIRouter(prefix="/notifications", tags=["Notifications"])

def _uid(claims: dict | None) -> str | None:
    return claims["sub"] if claims else None

# ---- reads -----------------------------------------------------------------

@router.get("/hosting")
async def getHostingEvents(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.hosting_events(claims["sub"], db)

@router.get("/entered")
async def getEnteredEvents(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.entered_events(claims["sub"], db)

@router.get("/profile/{public_id}")
async def getProfileEvents(public_id: str, claims: dict | None = Depends(optional_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.profile_events(public_id, _uid(claims), db)

@router.get("/{event_id}")
async def getEvent(event_id: str, claims: dict | None = Depends(optional_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.get_event(event_id, _uid(claims), db)

# ---- host: the event -------------------------------------------------------

@router.post("")
@limiter.limit(MODERATE)
async def createEvent(request: Request, response: Response, body: EventCreate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.create_event(claims["sub"], body, db)

@router.patch("/{event_id}")
async def updateEvent(event_id: str, body: EventUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.update_event(claims["sub"], event_id, body, db)

@router.delete("/{event_id}", status_code = 204)
async def deleteEvent(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await svc.delete_event(claims["sub"], event_id, db)

@router.post("/{event_id}/delays")
@limiter.limit(MODERATE)
async def delayEvent(request: Request, response: Response, event_id: str, body: EventDelay, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.delay_start(claims["sub"], event_id, body, db)

@router.delete("/{event_id}/entrants/{entrant_id}")
async def removeEntrant(event_id: str, entrant_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.remove_entrant(claims["sub"], event_id, entrant_id, db)

# ---- sign-ups --------------------------------------------------------------

@router.post("/{event_id}/registration")
@limiter.limit(MODERATE)
async def register(request: Request, response: Response, event_id: str, body: Registration, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.register(claims["sub"], event_id, body, db)

@router.delete("/{event_id}/registration")
async def unregister(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.unregister(claims["sub"], event_id, db)

# ---- bracket ---------------------------------------------------------------

@router.put("/{event_id}/bracket")
async def saveDraft(event_id: str, body: SeedsUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.save_draft(claims["sub"], event_id, body.seeds, db)

@router.post("/{event_id}/bracket/publish")
async def publishBracket(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.publish(claims["sub"], event_id, db)

@router.delete("/{event_id}/bracket")
async def resetBracket(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.reset_bracket(claims["sub"], event_id, db)

@router.put("/{event_id}/bracket/matches/{match_id}")
async def hostReport(event_id: str, match_id: str, body: MatchScore, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.host_report(claims["sub"], event_id, match_id, body, db)

@router.delete("/{event_id}/bracket/matches/{match_id}")
async def clearMatch(event_id: str, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.clear_match(claims["sub"], event_id, match_id, db)

@router.post("/{event_id}/bracket/matches/{match_id}/reports")
@limiter.limit(MODERATE)
async def playerReport(request: Request, response: Response, event_id: str, match_id: str, body: MatchScore, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.player_report(claims["sub"], event_id, match_id, body, db)

@router.post("/{event_id}/bracket/matches/{match_id}/no-show")
@limiter.limit(MODERATE)
async def reportNoShow(request: Request, response: Response, event_id: str, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.report_no_show(claims["sub"], event_id, match_id, db)

@router.delete("/{event_id}/bracket/matches/{match_id}/no-show")
async def dismissNoShow(event_id: str, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.dismiss_no_show(claims["sub"], event_id, match_id, db)

@router.post("/{event_id}/bracket/log/{entry_id}/revert")
async def revertDecision(event_id: str, entry_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.revert_decision(claims["sub"], event_id, entry_id, db)

# ---- notifications ---------------------------------------------------------

@notifications_router.get("")
async def getNotifications(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await list_notifications(claims["sub"], db)

@notifications_router.post("/read")
async def readNotifications(body: NotificationsRead, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await mark_read(claims["sub"], body.ids, db)
