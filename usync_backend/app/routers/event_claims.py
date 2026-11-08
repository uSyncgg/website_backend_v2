import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import AsyncClient
from app.core.dependencies import get_db, get_supabase, verify_supabase_jwt
from app.core.limiter import limiter, STRICT_FORM_SUBMISSION
from app.schemas.event_claims import EventClaimIn, EventClaimOut, ClaimTargetOut
from app.services.event_claims import get_claim_target, submit_event_claim

router = APIRouter(prefix="/host/event-claims", tags=["Event claims"])

@router.get("/events/{event_id}", response_model = ClaimTargetOut)
async def claimTarget(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    The event a claim page is for: its name and type, and whether this account can still claim it.
    """

    return await get_claim_target(event_id, claims["sub"], db)

@router.post("", response_model = EventClaimOut, status_code = 201)
@limiter.limit(STRICT_FORM_SUBMISSION)
async def createEventClaim(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    event_id: str = Form(...),
    role: str = Form(...),
    description: str = Form(...),
    contact_method: str = Form(...),
    contact: str = Form(...),
    proof: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Files a claim that the signed in host runs an existing event, with a picture as proof.
    uSync reviews it by hand, and contact@usync.gg is emailed about it. The claimant comes from
    the token, never the form.
    """

    try:
        claim = EventClaimIn(
            event_id = event_id,
            role = role,
            description = description,
            contact_method = contact_method,
            contact = contact,
        )
    except ValidationError as e:
        raise HTTPException(status_code = 422, detail = e.errors(include_context = False))

    return await submit_event_claim(claim, proof, claims["sub"], claims.get("email"), db, supabase, background_tasks)
