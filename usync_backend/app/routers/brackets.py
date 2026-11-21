import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt, optional_supabase_jwt, get_openai
from app.core.limiter import limiter, MODERATE
from app.schemas.brackets import (
    EventCreate, EventUpdate, EventDelay, Registration, PlayersUpdate, SeedsUpdate, MatchScore, Ruling,
    NotificationsRead, AdminInvite, AdminResponse,
    EventOut, EventPage, QueueOut, ClaimOut, AdminInviteOut, NotificationOut, MetaOut,
)
from app.services import brackets as svc
from app.services import event_issues
from app.services.lookups import bracket_meta
from app.services.notifications import list_notifications, mark_read

# Bracket hosting. Event routes return the event as the bracket pages use it (EventOut), with
# canManage / viewerEntrantId / viewerCanAct and the viewer's view of each match filled in for
# the signed in account. Host / admin routes 404 for anyone else. Lists are pages of summaries.
router = APIRouter(prefix="/brackets", tags=["Brackets"])
notifications_router = APIRouter(prefix="/notifications", tags=["Notifications"])

def _uid(claims: dict | None) -> str | None:
    return claims["sub"] if claims else None

# ---- reads -----------------------------------------------------------------

@router.get("/meta", response_model = MetaOut)
async def getMeta(db: AsyncSession = Depends(get_db)):
    return await bracket_meta(db)

@router.get("/hosting", response_model = EventPage)
async def getHostingEvents(cursor: str | None = None, limit: int = Query(50, ge = 1, le = 50), claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.hosting_events(claims["sub"], db, cursor, limit)

@router.get("/entered", response_model = EventPage)
async def getEnteredEvents(cursor: str | None = None, limit: int = Query(50, ge = 1, le = 50), claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.entered_events(claims["sub"], db, cursor, limit)

@router.get("/admin-invites", response_model = list[AdminInviteOut])
async def getAdminInvites(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.my_admin_invites(claims["sub"], db)

@router.post("/admin-invites/{invite_id}", response_model = list[AdminInviteOut])
async def respondAdminInvite(invite_id: uuid.UUID, body: AdminResponse, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.respond_admin_invite(claims["sub"], invite_id, body.accept, db)

@router.get("/profile/{public_id}", response_model = EventPage)
async def getProfileEvents(public_id: str, cursor: str | None = None, limit: int = Query(50, ge = 1, le = 50), claims: dict | None = Depends(optional_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.profile_events(public_id, _uid(claims), db, cursor, limit)

@router.get("/{event_id}", response_model = EventOut)
async def getEvent(event_id: str, claims: dict | None = Depends(optional_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.get_event(event_id, _uid(claims), db)

# ---- host: the event -------------------------------------------------------

@router.post("", response_model = EventOut)
@limiter.limit(MODERATE)
async def createEvent(request: Request, response: Response, body: EventCreate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db), openai: AsyncOpenAI = Depends(get_openai)):
    return await svc.create_event(claims["sub"], body, db, openai)

@router.patch("/{event_id}", response_model = EventOut)
async def updateEvent(event_id: str, body: EventUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db), openai: AsyncOpenAI = Depends(get_openai)):
    return await svc.update_event(claims["sub"], event_id, body, db, openai)

@router.delete("/{event_id}", status_code = 204)
async def deleteEvent(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await svc.delete_event(claims["sub"], event_id, db)

@router.post("/{event_id}/delays", response_model = EventOut)
@limiter.limit(MODERATE)
async def delayEvent(request: Request, response: Response, event_id: str, body: EventDelay, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.delay_start(claims["sub"], event_id, body, db)

@router.post("/{event_id}/admins", response_model = EventOut)
@limiter.limit(MODERATE)
async def inviteAdmin(request: Request, response: Response, event_id: str, body: AdminInvite, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.invite_admin(claims["sub"], event_id, body, db)

@router.delete("/{event_id}/admins/{admin_id}", response_model = EventOut)
async def removeAdmin(event_id: str, admin_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.remove_admin(claims["sub"], event_id, admin_id, db)

@router.delete("/{event_id}/entrants/{entrant_id}", response_model = EventOut)
async def removeEntrant(event_id: str, entrant_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.remove_entrant(claims["sub"], event_id, entrant_id, db)

# ---- sign-ups --------------------------------------------------------------

@router.post("/{event_id}/registration", response_model = EventOut)
@limiter.limit(MODERATE)
async def register(request: Request, response: Response, event_id: str, body: Registration, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.register(claims["sub"], event_id, body, db)

@router.put("/{event_id}/registration/players", response_model = EventOut)
@limiter.limit(MODERATE)
async def setPlayers(request: Request, response: Response, event_id: str, body: PlayersUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.set_players(claims["sub"], event_id, body.players, db, body.entrant_id)

@router.delete("/{event_id}/registration", response_model = EventOut)
async def unregister(event_id: str, entrant_id: str | None = Query(default = None, alias = "entrantId"),
                     claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.unregister(claims["sub"], event_id, db, entrant_id)

# ---- bracket ---------------------------------------------------------------

@router.put("/{event_id}/bracket", response_model = EventOut)
async def saveDraft(event_id: str, body: SeedsUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.save_draft(claims["sub"], event_id, body.seeds, db)

@router.post("/{event_id}/bracket/publish", response_model = EventOut)
async def publishBracket(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.publish(claims["sub"], event_id, db)

@router.delete("/{event_id}/bracket", response_model = EventOut)
async def resetBracket(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.reset_bracket(claims["sub"], event_id, db)

@router.post("/{event_id}/matches/{match_id}/reports", response_model = EventOut)
@limiter.limit(MODERATE)
async def reportScore(request: Request, response: Response, event_id: str, match_id: str, body: MatchScore, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.report(claims["sub"], event_id, match_id, body, db)

@router.post("/{event_id}/matches/{match_id}/no-show", response_model = EventOut)
@limiter.limit(MODERATE)
async def reportNoShow(request: Request, response: Response, event_id: str, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.report_no_show(claims["sub"], event_id, match_id, db)

@router.post("/{event_id}/matches/{match_id}/dispute", response_model = EventOut)
@limiter.limit(MODERATE)
async def disputeResult(request: Request, response: Response, event_id: str, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.dispute(claims["sub"], event_id, match_id, db)

@router.post("/{event_id}/matches/{match_id}/ruling", response_model = EventOut)
async def ruleOnMatch(event_id: str, match_id: str, body: Ruling, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.rule(claims["sub"], event_id, match_id, body, db)

@router.get("/{event_id}/decisions")
async def getDecisions(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await svc.decisions(claims["sub"], event_id, db)

@router.post("/{event_id}/decisions/{decision_id}/revert", response_model = EventOut)
async def revertDecision(event_id: str, decision_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await svc.revert(claims["sub"], event_id, decision_id, db)

# ---- the admin queue ---------------------------------------------------------

@router.get("/{event_id}/issues", response_model = QueueOut)
async def getQueue(event_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await event_issues.queue(claims["sub"], event_id, db)

@router.post("/{event_id}/issues/{issue_id}/claim", response_model = ClaimOut)
async def claimIssue(event_id: str, issue_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await event_issues.claim(claims["sub"], event_id, issue_id, db)

@router.post("/{event_id}/issues/{issue_id}/dismiss", response_model = QueueOut)
async def dismissIssue(event_id: str, issue_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await event_issues.dismiss(claims["sub"], event_id, issue_id, db)

# ---- notifications ---------------------------------------------------------

@notifications_router.get("", response_model = list[NotificationOut])
async def getNotifications(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await list_notifications(claims["sub"], db)

@notifications_router.post("/read", response_model = list[NotificationOut])
async def readNotifications(body: NotificationsRead, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await mark_read(claims["sub"], body.ids, db)
