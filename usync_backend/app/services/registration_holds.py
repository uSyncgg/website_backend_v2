import uuid
import asyncio
import logging
import stripe

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from app.core.db import AsyncSessionLocal
from app.models.event_registration import EventRegistrations, EventPassTiers, PassPools
from app.services import REGISTRATION_HOLD_MINUTES, HOLD_SWEEP_INTERVAL_SECONDS

logger = logging.getLogger(__name__)

# Statuses that still hold a slot but have not paid. "payment_failed" is included because Stripe
# lets the buyer retry on the same PaymentIntent, so a failed attempt is not final on its own.
UNPAID_HOLD_STATUSES = ("pending", "payment_failed")
# Statuses whose slot has been given back. Must match the unique indexes on event_registrations.
RELEASED_STATUSES = ("expired", "canceled", "refunded")
SWEEP_BATCH_SIZE = 50

def hold_expired(registration: EventRegistrations) -> bool:
    """
    Whether a registration's hold on its pass slot is gone (released, or past the hold window).

    ::param registration the registration to check

    ::return True if the registration is no longer allowed to pay
    """

    if registration.payment_status in RELEASED_STATUSES:
        return True

    # created_at is a naive UTC timestamp (server default now() on a UTC database).
    age = datetime.now(timezone.utc).replace(tzinfo=None) - registration.created_at
    return registration.payment_status in UNPAID_HOLD_STATUSES and age > timedelta(minutes=REGISTRATION_HOLD_MINUTES)

async def release_registration(
    registration_id: uuid.UUID,
    new_status: str,
    db: AsyncSession,
    expected_payment_intent_id: str | None = None,
    check_payment_intent: bool = False,
    from_statuses: tuple[str, ...] = UNPAID_HOLD_STATUSES,
) -> bool:
    """
    Asynchronous function to give a registration's slot back to its tier and pool.

    Safe to call more than once: the status change only matches a registration still in one of
    from_statuses, so a second call (or a duplicate webhook) matches nothing and decrements nothing.

    ::param registration_id the registration to release
    ::param new_status the final status to record ("expired", "canceled" or "refunded")
    ::param db the Asynchronous database Session
    ::param expected_payment_intent_id the PaymentIntent the caller saw (and canceled) before releasing
    ::param check_payment_intent when True, skip the release if the registration's PaymentIntent changed
        since the caller looked, because the new one hasn't been canceled
    ::param from_statuses the statuses allowed to be released (unpaid by default; ("paid",) for refunds)

    ::return True if this call released the slot
    """

    conditions = [
        EventRegistrations.id == registration_id,
        EventRegistrations.payment_status.in_(from_statuses),
    ]
    if check_payment_intent:
        conditions.append(EventRegistrations.stripe_payment_intent_id.is_not_distinct_from(expected_payment_intent_id))

    status_stmt = (
        update(EventRegistrations)
        .where(*conditions)
        .values(payment_status=new_status)
        .returning(EventRegistrations.pass_tier_id)
    )
    pass_tier_id = (await db.execute(status_stmt)).scalar_one_or_none()

    if pass_tier_id is None:
        await db.rollback()
        return False

    # Tier first, then pool: the same lock order as check_and_claim, so the two can't deadlock.
    tier_stmt = (
        update(EventPassTiers)
        .where(EventPassTiers.id == pass_tier_id, EventPassTiers.sold_count > 0)
        .values(sold_count=EventPassTiers.sold_count - 1)
        .returning(EventPassTiers.pool_id)
    )
    tier_row = (await db.execute(tier_stmt)).one_or_none()

    if tier_row is None:
        logger.error("Releasing registration %s: tier %s sold_count already 0", registration_id, pass_tier_id)
        pool_id = (await db.execute(select(EventPassTiers.pool_id).where(EventPassTiers.id == pass_tier_id))).scalar_one_or_none()
    else:
        pool_id = tier_row.pool_id

    if pool_id is not None:
        pool_stmt = (
            update(PassPools)
            .where(PassPools.id == pool_id, PassPools.sold_count > 0)
            .values(sold_count=PassPools.sold_count - 1)
            .returning(PassPools.id)
        )
        if (await db.execute(pool_stmt)).scalar_one_or_none() is None:
            logger.error("Releasing registration %s: pool %s sold_count already 0", registration_id, pool_id)

    await db.commit()
    logger.info("Released registration %s (%s)", registration_id, new_status)
    return True

async def cancel_payment_intent(payment_intent_id: str, stripeClient: StripeClient) -> bool:
    """
    Asynchronous function to cancel a PaymentIntent so the buyer can no longer pay on it.

    ::param payment_intent_id the Stripe PaymentIntent id
    ::param stripeClient the StripeClient to interface with Stripe

    ::return True if the PaymentIntent is now canceled, False if it succeeded or is still processing
    """

    try:
        await stripeClient.v1.payment_intents.cancel_async(payment_intent_id)
        return True
    except stripe.InvalidRequestError:
        # Stripe refuses to cancel a PaymentIntent that succeeded or is processing. It may also
        # already be canceled, which is fine for us.
        intent = await stripeClient.v1.payment_intents.retrieve_async(payment_intent_id)
        return intent.status == "canceled"

async def expire_stale_holds(stripeClient: StripeClient) -> int:
    """
    Asynchronous function to release every unpaid registration whose hold has run out.

    ::param stripeClient the StripeClient to interface with Stripe

    ::return the number of registrations released
    """

    async with AsyncSessionLocal() as db:
        cutoff = func.timezone("utc", func.now()) - timedelta(minutes=REGISTRATION_HOLD_MINUTES)
        stale_stmt = (
            select(EventRegistrations.id, EventRegistrations.stripe_payment_intent_id)
            .where(
                EventRegistrations.payment_status.in_(UNPAID_HOLD_STATUSES),
                EventRegistrations.created_at < cutoff,
            )
            .order_by(EventRegistrations.created_at)
            .limit(SWEEP_BATCH_SIZE)
        )
        stale = (await db.execute(stale_stmt)).all()
        await db.rollback()

        released = 0
        for registration_id, payment_intent_id in stale:
            try:
                # Cancel at Stripe first. If the buyer is mid-payment, Stripe refuses and we leave
                # the slot alone; the succeeded webhook will mark it paid, or the next sweep retries.
                if payment_intent_id is not None and not await cancel_payment_intent(payment_intent_id, stripeClient):
                    continue

                if await release_registration(
                    registration_id, "expired", db,
                    expected_payment_intent_id=payment_intent_id, check_payment_intent=True,
                ):
                    released += 1
            except Exception:
                await db.rollback()
                logger.exception("Failed to expire registration %s", registration_id)

        return released

async def run_hold_sweeper(stripeClient: StripeClient) -> None:
    """
    Background loop started in main.py's lifespan. Sweeps once at startup, then on an interval.

    ::param stripeClient the StripeClient to interface with Stripe
    """

    while True:
        try:
            released = await expire_stale_holds(stripeClient)
            if released:
                logger.info("Hold sweeper released %d registration(s)", released)
        except Exception:
            logger.exception("Hold sweeper run failed")

        await asyncio.sleep(HOLD_SWEEP_INTERVAL_SECONDS)
