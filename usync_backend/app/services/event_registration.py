import uuid
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from stripe import StripeClient
from app.models.event_registration import EventRegistrations, EventPassTiers
from app.models.host_data import LanEvents
from app.schemas.event_registration import EventPassesOut, EventSubmissionIn, EventSubmissionOut, PaymentIntentOut, ReceiptOut
from collections.abc import Sequence
from collections import defaultdict
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from cachetools import TTLCache
from app.services import PLATFORM_FEE_PERCENT, STRIPE_CURRENCY, STRIPE_PAYMENT_METHOD_TYPES
from app.services.email import send_receipt_email
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

async def get_event_passes(path: str, db: AsyncSession) -> Sequence[EventPassesOut]:
    """
    
    """

    path = f"/{path}"

    lan_stmt = (
        select(LanEvents)
        .where(LanEvents.status != "pending", LanEvents.path == path)
    )
    lan_result = await db.execute(lan_stmt)
    lan = lan_result.scalars().first()

    if lan is None:
        raise HTTPException(status_code=404, detail=f"LAN event not found: {path}")

    pass_stmt = (
        select(EventPassTiers)
        .where(EventPassTiers.event_id == lan.event_id, EventPassTiers.is_active.is_(True))
    )
    pass_results = await db.execute(pass_stmt)

    return pass_results.scalars().all()

def craft_registration(payload: EventSubmissionIn, lan_pass: EventPassTiers) -> EventRegistrations:
    """
    
    """

    fee = (lan_pass.price_cents * PLATFORM_FEE_PERCENT + 50) // 100
    total = fee + lan_pass.price_cents

    registration = EventRegistrations(
        event_id = lan_pass.event_id,
        pass_tier_id = payload.pass_tier_id,
        team_name = payload.team_name,
        team_name_normalized = payload.team_name.strip().lower() if payload.team_name else None,
        contact_username = payload.contact_username,
        contact_email = payload.contact_email,
        player_info = payload.player_info,
        price_snapshot_cents = lan_pass.price_cents,
        fee_snapshot_cents = fee,
        total_snapshot_cents = total,
        custom_fields = payload.custom_fields,
        org_twitter = payload.org_twitter
    )

    return registration

async def deposit_registration(registration: EventRegistrations, db: AsyncSession) -> EventRegistrations:
    """
    
    """

    db.add(registration)

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "uq_registrations_event_team_name" in str(e.orig):
            raise HTTPException(status_code=409, detail="Team name already taken for this event")
        if "uq_registrations_individual_contact" in str(e.orig):
            raise HTTPException(status_code=409, detail="Already registered for this pass")
        raise

    await db.refresh(registration)

    return registration

async def check_and_claim(registration: EventRegistrations, db: AsyncSession) -> None:
    """
    
    """

    claim_stmt = (
        update(EventPassTiers)
        .where(
            EventPassTiers.id == registration.pass_tier_id,
            (EventPassTiers.capacity.is_(None)) | (EventPassTiers.sold_count < EventPassTiers.capacity),
        )
        .values(sold_count=EventPassTiers.sold_count + 1)
        .returning(EventPassTiers.id)
    )   
    claimed = (await db.execute(claim_stmt)).scalar_one_or_none()

    if claimed is None:
        raise HTTPException(status_code=409, detail="This pass is sold out")

    return None

def validate_form_fields(form_fields: list[dict], custom_fields: dict) -> None:
    """
    
    """

    for field in form_fields:
        key = field["key"]
        if field.get("required") and key not in custom_fields.get(key):
            raise HTTPException(status_code=400, detail=f"Missing required field: {key}")
        if key in custom_fields and field.get("field_type") == "email" and "@" not in custom_fields[key]:
            raise HTTPException(status_code=400, detail=f"Invalid email for field: {key}")

    return None

async def deposit_form_data(payload: EventSubmissionIn, db: AsyncSession) -> EventSubmissionOut:
    """
    
    """
    pass_stmt = (
        select(EventPassTiers)
        .where(EventPassTiers.id == payload.pass_tier_id, EventPassTiers.is_active.is_(True))
    )

    pass_result = await db.execute(pass_stmt)
    lan_pass = pass_result.scalars().first()

    if lan_pass is None:
        raise HTTPException(status_code=404, detail=f"Event ID Not Found: {payload.pass_tier_id}")

    validate_form_fields(lan_pass.form_fields, payload.custom_fields)
    
    if lan_pass.requires_team and not payload.team_name:
        raise HTTPException(status_code=400, detail="This pass requires a team name")
    
    if not lan_pass.requires_team and payload.team_name:
        raise HTTPException(status_code=400, detail="This pass does not support a team name")

    registration = craft_registration(payload, lan_pass)

    await check_and_claim(registration, db)

    registration = await deposit_registration(registration, db)

    return EventSubmissionOut(registration_id=registration.id, total_snapshot_cents=registration.total_snapshot_cents)

async def get_or_create_payment_intent(registration_id: uuid.UUID, db: AsyncSession, stripeClient: StripeClient) -> PaymentIntentOut:
    """
    
    """

    stmt = select(EventRegistrations).where(EventRegistrations.id == registration_id)
    result = await db.execute(stmt)
    registration = result.scalars().first()

    if registration is None:
        raise HTTPException(status_code=404, detail="Registration not found")

    if registration.payment_status == "paid":
        raise HTTPException(status_code=409, detail="Registration already paid")
        
    if registration.stripe_payment_intent_id is not None:
        existing = await stripeClient.v1.payment_intents.retrieve_async(registration.stripe_payment_intent_id)
        if existing.status not in ("canceled", "succeeded"):
            return PaymentIntentOut(client_secret=existing.client_secret, total_snapshot_cents=registration.total_snapshot_cents)

    intent = await stripeClient.v1.payment_intents.create_async({
          "amount": registration.total_snapshot_cents,
          "currency": STRIPE_CURRENCY,
          "payment_method_types": STRIPE_PAYMENT_METHOD_TYPES,
          "description": f"Payment for {registration.contact_email}",
          "metadata": {
              "registration_id": str(registration.id),
              "event_id": str(registration.event_id),
          },  
      })
      
    registration.stripe_payment_intent_id = intent.id
    await db.commit()
    
    return PaymentIntentOut(client_secret=intent.client_secret, total_snapshot_cents=registration.total_snapshot_cents)

async def check_and_send(registration: EventRegistrations, db: AsyncSession) -> None:
    """
    
    """

    # if registration.payment_status != "paid" or registration.receipt_email_sent_at is not None:
    #     return None
    if registration.receipt_email_sent_at is not None:
        return None

    stmt = select(LanEvents.name).where(LanEvents.event_id == registration.event_id)
    result = await db.execute(stmt)
    name = result.scalars().first()

    await send_receipt_email(
        to = registration.contact_email,
        display_name = registration.contact_username or "there",
        total_snapshot_cents = registration.total_snapshot_cents,
        event_name = name
    )

    registration.receipt_email_sent_at = datetime.now(timezone.utc)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Error inputting email time")

    return None

async def get_receipt(registration_id: uuid.UUID, db: AsyncSession) -> ReceiptOut:
    """
    
    """

    stmt = select(EventRegistrations).where(EventRegistrations.id == registration_id)
    result = await db.execute(stmt)
    registration = result.scalars().first()

    if registration is None:
        raise HTTPException(status_code=404, detail="Registration not found")

    try:
        await check_and_send(registration, db)
    except Exception:
        logger.exception("Failed to send receipt email for registration %s", registration.id)
        pass
    
    return ReceiptOut(
        payment_status = registration.payment_status,
        price_snapshot_cents = registration.price_snapshot_cents,
        fee_snapshot_cents = registration.fee_snapshot_cents,
        total_snapshot_cents = registration.total_snapshot_cents,
        team_name = registration.team_name,
        contact_username = registration.contact_username,
        contact_email = registration.contact_email,
        org_twitter = registration.org_twitter or "N/A",
        player_info = registration.player_info,
        custom_fields = registration.custom_fields
    )
