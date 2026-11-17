import uuid
import logging

from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import select, literal, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import AsyncClient
from storage3.utils import StorageException
from app.models.event_claims import EventClaims
from app.models.host_data import EventFormParent, LanEvents, LeagueEvents, LeagueParentEvents, WagerEvents, XpEvents
from app.models.users import Profiles
from app.schemas.event_claims import EventClaimIn, ClaimTargetOut
from app.services.images import read_image_upload
from app.services.email import send_event_claim_notification

logger = logging.getLogger(__name__)

# Private bucket: proofs can show personal details, so they're only readable with the secret key
# (the Supabase dashboard, or a signed URL made by the backend).
PROOF_BUCKET = "event_proof_images"
PROOF_MAX_SIDE = 2048
# How long the proof link in the team's notification email works.
PROOF_LINK_SECONDS = 7 * 24 * 60 * 60

# Every listing type a host can claim, and the type name the API reports for it.
_CLAIMABLE = (
    (LanEvents, LanEvents.event_id, "lan"),
    (LeagueParentEvents, LeagueParentEvents.id, "league"),
    (LeagueEvents, LeagueEvents.event_id, "league"),
    (WagerEvents, WagerEvents.event_id, "wager"),
    (XpEvents, XpEvents.event_id, "head-to-head"),
)

async def _find_event(event_id: uuid.UUID, db: AsyncSession) -> tuple[str, str, uuid.UUID | None]:
    """
    Looks the event up across every listing table in one query.

    ::return (name, event_type, owner account id or None). 404 if no listing has this id.
    """

    listings = union_all(*(
        select(table.name.label("name"), literal(label).label("event_type"))
        .where(id_column == event_id)
        for table, id_column, label in _CLAIMABLE
    )).subquery()

    stmt = (
        select(listings.c.name, listings.c.event_type, EventFormParent.user_id)
        .select_from(listings)
        .join(EventFormParent, EventFormParent.id == event_id)
        .limit(1)
    )

    row = (await db.execute(stmt)).first()

    if row is None:
        raise HTTPException(status_code = 404, detail = "This event could not be found.")

    return row.name, row.event_type, row.user_id

async def _has_pending_claim(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> bool:
    stmt = select(EventClaims.id).where(
        EventClaims.event_id == event_id,
        EventClaims.account_id == account_id,
        EventClaims.status == "pending",
    )

    return (await db.execute(stmt)).first() is not None

async def _require_host(account_id: str, db: AsyncSession) -> None:
    """
    403 unless the account has a host profile. Checked here, not trusted from the frontend.
    """

    stmt = select(Profiles.id).where(Profiles.account_id == account_id, Profiles.type == "host")

    if (await db.execute(stmt)).first() is None:
        raise HTTPException(status_code = 403, detail = "Only host accounts can claim events. Make a host profile first.")

async def get_claim_target(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> ClaimTargetOut:
    """
    What the claim page shows before the form: the event's real name and type, and whether
    it can still be claimed by this account.
    """

    name, event_type, owner_id = await _find_event(event_id, db)

    return ClaimTargetOut(
        event_id = event_id,
        name = name,
        event_type = event_type,
        owned = owner_id is not None,
        pending_claim = await _has_pending_claim(event_id, account_id, db),
    )

async def _remove_proof(path: str, supabase: AsyncClient) -> None:
    try:
        await supabase.storage.from_(PROOF_BUCKET).remove([path])
    except Exception:
        logger.exception("Failed to clean up claim proof %s", path)

async def submit_event_claim(
        claim: EventClaimIn,
        proof: UploadFile,
        account_id: str,
        account_email: str | None,
        db: AsyncSession,
        supabase: AsyncClient,
        background_tasks: BackgroundTasks
) -> EventClaims:
    """
    Files a pending claim. Order matters: every cheap check runs before the image is processed
    and uploaded, and the upload is removed again if the row can't be saved.

    403 not a host · 404 unknown event · 409 already owned / already pending · 413/422 bad image
    """

    await _require_host(account_id, db)

    event_name, event_type, owner_id = await _find_event(claim.event_id, db)

    if owner_id is not None:
        raise HTTPException(status_code = 409, detail = "This event already has an owner on uSync.")

    if await _has_pending_claim(claim.event_id, account_id, db):
        raise HTTPException(status_code = 409, detail = "You already have a claim for this event under review.")

    image = await read_image_upload(proof, max_side = PROOF_MAX_SIDE)

    claim_id = uuid.uuid4()
    path = f"{account_id}/{claim_id}.webp"

    try:
        await supabase.storage.from_(PROOF_BUCKET).upload(
            path, image, {"content-type": "image/webp", "upsert": "false"}
        )
    except StorageException:
        logger.exception("Claim proof upload failed.")
        raise HTTPException(502, "Could not save your proof picture. Try again.")

    row = EventClaims(
        id = claim_id,
        event_id = claim.event_id,
        account_id = account_id,
        role = claim.role,
        description = claim.description,
        contact_method = claim.contact_method,
        contact = claim.contact,
        proof_path = path,
    )

    try:
        db.add(row)
        await db.commit()
    except IntegrityError:
        # Two submits racing past the pending check: the partial unique index stops the second.
        await db.rollback()
        await _remove_proof(path, supabase)
        raise HTTPException(status_code = 409, detail = "You already have a claim for this event under review.")
    except Exception:
        await db.rollback()
        await _remove_proof(path, supabase)
        raise

    await db.refresh(row)

    # After the response is sent, so the host never waits on (or fails because of) email.
    # Plain values, not the ORM row: the request's DB session is closed by the time this runs.
    background_tasks.add_task(
        _notify_team,
        supabase,
        claim_id = row.id,
        event_name = event_name,
        event_type = event_type,
        event_id = row.event_id,
        claimant_email = account_email,
        role = row.role,
        contact_method = row.contact_method,
        contact = row.contact,
        description = row.description,
        proof_path = row.proof_path,
    )

    return row

async def _notify_team(supabase: AsyncClient, **claim) -> None:
    """
    Emails contact@usync.gg about a new claim. Best effort: the claim is already saved, so a
    failure here is only logged.
    """

    proof_url = None

    try:
        signed = await supabase.storage.from_(PROOF_BUCKET).create_signed_url(claim["proof_path"], PROOF_LINK_SECONDS)
        proof_url = signed.get("signedURL") or signed.get("signedUrl")
    except Exception:
        logger.exception("Could not sign proof link for claim %s", claim["claim_id"])

    try:
        await send_event_claim_notification(**claim, proof_url = proof_url)
    except Exception:
        logger.exception("Could not send claim notification for claim %s", claim["claim_id"])
