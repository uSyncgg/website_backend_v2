from fastapi import Request
from fastapi import APIRouter

from services.webhooks import handle_stripe_webhook

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

@router.post("/stripe")
async def stripeRoute(request: Request):
    """
    Sends the stripe payment via a StripeSubmission function which will be imported via webhooks.py
    """

    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    event = await handle_stripe_webhook(payload, signature)

    # base return message off event?