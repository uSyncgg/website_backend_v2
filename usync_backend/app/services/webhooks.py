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
from app.services.registration_holds import release_registration, RELEASED_STATUSES

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

    stripe_object = event["data"]["object"]
    # payment_intent.* events carry the PaymentIntent itself; charge.* events carry a Charge that
    # points at its PaymentIntent.
    payment_intent_id = stripe_object["payment_intent"] if event["type"].startswith("charge.") else stripe_object["id"]

    stmt = select(EventRegistrations).where(EventRegistrations.stripe_payment_intent_id == payment_intent_id)
    result = await db.execute(stmt)
    registration = result.scalars().first()        

    if registration is None:
        return {"status": "ignored"}

    event_pass, event_name = await get_pass_event_helper(registration, db)

    if event["type"] == "payment_intent.succeeded":
        if registration.payment_status in RELEASED_STATUSES:
            # Shouldn't happen: slots are only released after Stripe confirms the cancel.
            logger.error("Registration %s paid after its slot was released; check pass counts", registration.id)
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
        # Final: give the slot back. No-op if the sweeper already released it as "expired".
        await release_registration(registration.id, "canceled", db)
    elif event["type"] == "charge.refunded":
        if stripe_object["refunded"]:
            # Fully refunded: the team is out, give the slot back. Duplicate webhooks are no-ops.
            await release_registration(registration.id, "refunded", db, from_statuses=("paid",))
        else:
            logger.info("Partial refund on registration %s; slot kept", registration.id)

    return {"status": "ok"}
