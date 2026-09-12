from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db
from app.services.webhooks import handle_stripe_webhook

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

@router.post("/stripe")
async def stripeRoute(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Sends the stripe payment via a StripeSubmission function which will be imported via webhooks.py
    """

    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    return await handle_stripe_webhook(payload, signature, db)
