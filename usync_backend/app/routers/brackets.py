import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt, optional_supabase_jwt
from app.core.limiter import limiter, STRICT_FORM_SUBMISSION, MODERATE
from app.schemas.brackets import (
    BracketEventIn,
    BracketEventOut,
    BracketEventSummary,
    BracketEventUpdate,
    CheckInIn,
    DelayIn,
    HostEntrantIn,
    HostResultIn,
    RegistrationIn,
    RegistrationOut,
    ReportOut,
    ScoreIn,
    SeedsIn
)
from app.services import brackets as service

# Bracket hosting: hosts and venues run their own events (sign-ups, seeding, a live bracket),
# and players sign up and report their own matches. Host routes only work on events the
# signed-in account owns.
router = APIRouter(prefix = "/brackets", tags = ["Brackets"])

# ---- host ------------------------------------------------------------------

@router.get("/hosted", response_model = list[BracketEventSummary])
async def hostedEvents(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    The Host Dashboard: every bracket event the signed-in account posted.
    """

    return await service.list_hosted_events(claims["sub"], db)

@router.post("", response_model = BracketEventOut, status_code = 201)
@limiter.limit(STRICT_FORM_SUBMISSION)
async def createEvent(request: Request, response: Response, payload: BracketEventIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Creates an event posted as one of the account's host or venue profiles.
    """

    event_id = await service.create_bracket_event(payload, claims["sub"], db)
    return await service.get_managed_event(event_id, claims["sub"], db)

@router.get("/{event_id}/manage", response_model = BracketEventOut)
async def manageEvent(event_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Everything the host manages the event with: entrant details and sign-up answers, the draft
    bracket and the decision log.
    """

    return await service.get_managed_event(event_id, claims["sub"], db)

@router.patch("/{event_id}", status_code = 204)
@limiter.limit(MODERATE)
async def updateEvent(request: Request, response: Response, event_id: uuid.UUID, payload: BracketEventUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.update_bracket_event(event_id, payload, claims["sub"], db)

@router.delete("/{event_id}", status_code = 204)
@limiter.limit(MODERATE)
async def deleteEvent(request: Request, response: Response, event_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.delete_bracket_event(event_id, claims["sub"], db)

@router.post("/{event_id}/delays", status_code = 204)
@limiter.limit(STRICT_FORM_SUBMISSION)
async def delayStart(request: Request, response: Response, event_id: uuid.UUID, payload: DelayIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Moves the start later and emails everyone signed up.
    """

    await service.delay_start(event_id, payload, claims["sub"], db)

@router.post("/{event_id}/entrants", status_code = 201)
@limiter.limit(MODERATE)
async def addEntrant(request: Request, response: Response, event_id: uuid.UUID, payload: HostEntrantIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)) -> dict:
    return {"entrant_id": await service.add_host_entrant(event_id, payload, claims["sub"], db)}

@router.delete("/{event_id}/entrants/{entrant_id}", status_code = 204)
@limiter.limit(MODERATE)
async def removeEntrant(request: Request, response: Response, event_id: uuid.UUID, entrant_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.remove_entrant(event_id, entrant_id, claims["sub"], db)

@router.patch("/{event_id}/entrants/{entrant_id}", status_code = 204)
@limiter.limit(MODERATE)
async def checkInEntrant(request: Request, response: Response, event_id: uuid.UUID, entrant_id: uuid.UUID, payload: CheckInIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.set_checked_in(event_id, entrant_id, payload.checked_in, claims["sub"], db)

@router.put("/{event_id}/bracket", status_code = 204)
@limiter.limit(MODERATE)
async def saveBracketDraft(request: Request, response: Response, event_id: uuid.UUID, payload: SeedsIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.save_bracket_draft(event_id, payload, claims["sub"], db)

@router.post("/{event_id}/bracket/publish", status_code = 204)
@limiter.limit(MODERATE)
async def publishBracket(request: Request, response: Response, event_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.publish_bracket(event_id, claims["sub"], db)

@router.delete("/{event_id}/bracket", status_code = 204)
@limiter.limit(MODERATE)
async def resetBracket(request: Request, response: Response, event_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.reset_bracket(event_id, claims["sub"], db)

@router.put("/{event_id}/bracket/matches/{match_id}", status_code = 204)
@limiter.limit(MODERATE)
async def hostReportMatch(request: Request, response: Response, event_id: uuid.UUID, match_id: str, payload: HostResultIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    The host sets or overrides a result (forfeit: true for a no-show forfeit).
    """

    await service.host_report(event_id, match_id, payload, claims["sub"], db)

@router.delete("/{event_id}/bracket/matches/{match_id}", status_code = 204)
@limiter.limit(MODERATE)
async def clearMatch(request: Request, response: Response, event_id: uuid.UUID, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.clear_match(event_id, match_id, claims["sub"], db)

@router.delete("/{event_id}/bracket/matches/{match_id}/no-show", status_code = 204)
@limiter.limit(MODERATE)
async def dismissNoShow(request: Request, response: Response, event_id: uuid.UUID, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.dismiss_no_show(event_id, match_id, claims["sub"], db)

@router.post("/{event_id}/bracket/log/{entry_id}/revert", status_code = 204)
@limiter.limit(MODERATE)
async def revertDecision(request: Request, response: Response, event_id: uuid.UUID, entry_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.revert_decision(event_id, entry_id, claims["sub"], db)

# ---- public and players -------------------------------------------------------

@router.get("/{event_id}", response_model = BracketEventOut)
async def getEvent(event_id: uuid.UUID, claims: dict | None = Depends(optional_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    The public event page. Signed-in viewers also get their own entrant id, if they're signed up.
    """

    return await service.get_bracket_event(event_id, claims["sub"] if claims else None, db)

@router.post("/{event_id}/registration", response_model = RegistrationOut, status_code = 201)
@limiter.limit(STRICT_FORM_SUBMISSION)
async def register(request: Request, response: Response, event_id: uuid.UUID, payload: RegistrationIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Signs the player up. For paid events, continue with
    POST /event-registration/{registration_id}/payment-intent.
    """

    return await service.register(event_id, payload, claims["sub"], db)

@router.delete("/{event_id}/registration", status_code = 204)
@limiter.limit(MODERATE)
async def unregister(request: Request, response: Response, event_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.unregister(event_id, claims["sub"], db)

@router.post("/{event_id}/registration/check-in", status_code = 204)
@limiter.limit(MODERATE)
async def checkIn(request: Request, response: Response, event_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.check_in(event_id, claims["sub"], db)

@router.post("/{event_id}/bracket/matches/{match_id}/reports", response_model = ReportOut)
@limiter.limit(MODERATE)
async def reportMatch(request: Request, response: Response, event_id: uuid.UUID, match_id: str, payload: ScoreIn, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    A player reports their match: 'confirmed' when both sides agree, 'disputed' when they don't,
    'waiting' until the other side reports.
    """

    return await service.player_report(event_id, match_id, payload, claims["sub"], db)

@router.post("/{event_id}/bracket/matches/{match_id}/no-show", status_code = 204)
@limiter.limit(MODERATE)
async def reportNoShow(request: Request, response: Response, event_id: uuid.UUID, match_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await service.report_no_show(event_id, match_id, claims["sub"], db)
