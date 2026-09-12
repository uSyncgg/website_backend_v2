import uuid

from fastapi import APIRouter, Depends
from app.core.dependencies import get_stripe_client, get_db
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from app.schemas.event_registration import EventPassesOut, EventSubmissionOut, EventSubmissionIn, PaymentIntentOut, ReceiptOut
from app.services.event_registration import get_event_passes, deposit_form_data, get_or_create_payment_intent, get_receipt

router = APIRouter(prefix = "/event-registration", tags = ["Event Registration"])

@router.get("/event/{path}/passes", response_model = list[EventPassesOut])
async def passes_by_path(path: str, db: AsyncSession = Depends(get_db)):
    """
    
    """

    return await get_event_passes(path, db)

@router.post("/event-form-submission", response_model = EventSubmissionOut)
async def submit_event_form(payload: EventSubmissionIn, db: AsyncSession = Depends(get_db)):
    """
    
    """

    return await deposit_form_data(payload, db)

@router.post("/{registration_id}/payment-intent", response_model = PaymentIntentOut)
async def registration_payment_intent(registration_id: uuid.UUID, db: AsyncSession = Depends(get_db), stripeClient: StripeClient = Depends(get_stripe_client)):
    """
    
    """

    return await get_or_create_payment_intent(registration_id, db, stripeClient)

@router.get("/{registration_id}/receipt", response_model = ReceiptOut)
async def registration_receipt(registration_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    
    """

    return await get_receipt(registration_id, db) 
