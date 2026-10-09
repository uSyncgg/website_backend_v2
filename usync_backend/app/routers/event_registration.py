import uuid

from fastapi import APIRouter, Depends, Request, Response
from app.core.dependencies import get_stripe_client, get_db
from app.core.limiter import limiter, STRICT_FORM_SUBMISSION, STRICT_PAYMENT, MODERATE
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from app.schemas.event_registration import EventPassesOut, EventSubmissionOut, EventSubmissionIn, PaymentIntentOut, ReceiptOut
from app.services.event_registration import get_event_passes, deposit_form_data, get_or_create_payment_intent, get_receipt

router = APIRouter(prefix = "/event-registration", tags = ["Event Registration"])

@router.get("/event/{path}/passes", response_model = list[EventPassesOut])
@limiter.limit(MODERATE)
async def passes_by_path(request: Request, response: Response, path: str, db: AsyncSession = Depends(get_db)):
    """
    GET route to fetch all event passes via the frontend path slug.

    ::param request the Request object used by the rate limiter
    ::param response the Response object the rate limiter writes its headers to
    ::param path the string containing the frontend path slug
    ::param db the Asynchronous Session depending on the local session to acquire the db object

    ::return a list of event passes validated by the EventPassesOut schema
    """

    return await get_event_passes(path, db)

@router.post("/event-form-submission", response_model = EventSubmissionOut)
@limiter.limit(STRICT_FORM_SUBMISSION)
async def submit_event_form(request: Request, response: Response, payload: EventSubmissionIn, db: AsyncSession = Depends(get_db)):
    """
    POST route to submit the event form prior to paying.

    ::param request the Request object used by the rate limiter
    ::param response the Response object the rate limiter writes its headers to
    ::param payload the frontend payload validated by the EventSubmissionIn schema
    ::param db the Asynchronous Session depending on the local session to acquire the db object

    ::return an event submission validated by the EventSubmissionOut schema
    """

    return await deposit_form_data(payload, db)

@router.post("/{registration_id}/payment-intent", response_model = PaymentIntentOut)
@limiter.limit(STRICT_PAYMENT)
async def registration_payment_intent(request: Request, response: Response, registration_id: uuid.UUID, db: AsyncSession = Depends(get_db), stripeClient: StripeClient = Depends(get_stripe_client)):
    """
    POST route to submit a registration id and craft a payment intent to interact with Stripe via webhooks.

    ::param request the Request object used by the rate limiter
    ::param response the Response object the rate limiter writes its headers to
    ::param registration_id the uuid4 registration id
    ::param db the Asynchronous Session depending on the local session to acquire the db object
    ::param stripeClient the Stripe Client depending on the local async session 

    ::return a payment intent validated by the PaymentIntentOut schema
    """

    return await get_or_create_payment_intent(registration_id, db, stripeClient)

@router.get("/{registration_id}/receipt", response_model = ReceiptOut)
@limiter.limit(MODERATE)
async def registration_receipt(request: Request, response: Response, registration_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    GET route to fetch the receipt information by providing the registration id.

    ::param request the Request object used by the rate limiter
    ::param response the Response object the rate limiter writes its headers to
    ::param registration_id the uuid4 registration id
    ::param db the Asynchronous Session depending on the local session to acquire the db object

    ::return a receipt validated by the ReceiptOut schema
    """

    return await get_receipt(registration_id, db) 
