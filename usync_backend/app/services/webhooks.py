import os
import stripe
import logging

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from app.models.event_registration import EventRegistrations
from app.services.event_registration import check_and_send

logger = logging.getLogger(__name__)

async def handle_stripe_webhook(payload: bytes, signature: str, db: AsyncSession) -> dict:
    try:
        event = stripe.Webhook.construct_event(payload, signature, os.getenv("STRIPE_TEST_WEBHOOK_KEY"))
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    intent = event["data"]["object"]

    stmt = select(EventRegistrations).where(EventRegistrations.stripe_payment_intent_id == intent["id"])
    result = await db.execute(stmt)
    registration = result.scalars().first()

    if registration is None:
        return {"status": "ignored"}

    if event["type"] == "payment_intent.succeeded":
        registration.payment_status = "paid"
        registration.paid_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            await check_and_send(registration, db)
        except Exception:
            logger.exception("Failed to send receipt email for registration %s", registration.id)
            pass

    elif event["type"] == "payment_intent.payment_failed":
        registration.payment_status = "payment_failed"
        await db.commit()
    elif event["type"] == "payment_intent.canceled":
        registration.payment_status = "canceled"
        await db.commit()

    return {"status": "ok"}
