import os
import stripe
import logging

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from app.models.host_data import LanEvents
from app.models.event_registration import EventRegistrations, EventPassTiers
from app.services.event_registration import check_and_send, host_confirmation, get_pass_event_helper
from app.services import STRIPE_WEBHOOK_SECRET

logger = logging.getLogger(__name__)

async def handle_stripe_webhook(payload: bytes, signature: str, db: AsyncSession) -> dict:
    """
    Asynchronous function to interact with and handle the response of Stripe via webhooks.

    ::param payload the bytes to send to Stripe
    ::param signature the signature to sent to Stripe
    ::param db the Asynchronous database Session

    ::return a dict containing the response from Stripe
    """

    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    intent = event["data"]["object"]

    stmt = select(EventRegistrations).where(EventRegistrations.stripe_payment_intent_id == intent["id"])
    result = await db.execute(stmt)
    registration = result.scalars().first()        

    if registration is None:
        return {"status": "ignored"}

    event_pass, event_name = await get_pass_event_helper(registration, db)

    if event["type"] == "payment_intent.succeeded":
        registration.payment_status = "paid"
        registration.paid_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            await check_and_send(registration, db, event_name)
        except Exception:
            logger.exception("Failed to send receipt email for registration %s", registration.id)
            pass

        try:
            await host_confirmation(event_pass, event_name, registration, db)
        except Exception:
            logger.exception(f"REGISTRATION: {registration}")
            logger.exception(f"Failed to send host confirmation email {event_pass.id}")
            pass

    elif event["type"] == "payment_intent.payment_failed":
        registration.payment_status = "payment_failed"
        await db.commit()
    elif event["type"] == "payment_intent.canceled":
        registration.payment_status = "canceled"
        await db.commit()

    return {"status": "ok"}
