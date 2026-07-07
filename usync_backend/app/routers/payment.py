from fastapi import APIRouter, Depends
from app.core.dependencies import get_stripe_client
from app.schemas.payment import PaymentFormIn, PaymentFormOut
from app.services.payment import makeFormPayment
from stripe import StripeClient

router = APIRouter(prefix="/payment", tags=["Payment"])

@router.post("/event", response_model=PaymentFormOut)
async def eventPayment(payload: PaymentFormIn, stripeClient: StripeClient = Depends(get_stripe_client)):
    """
    Calls the functionality to create a payment intent and send it to stripe with the necessary information.

    ::param payload the PaymentFormIn Pydantic schema
    ::param stripeClient the established stripe client on backend initialization
    """

    return await makeFormPayment(stripeClient, payload)
