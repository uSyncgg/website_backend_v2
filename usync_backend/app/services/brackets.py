import math
import uuid
import logging

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from app.models.users import Profiles
from app.models.host_data import EventFormParent
from app.models.event_registration import EventPassTiers, EventRegistrations
from app.models.brackets import (
    BracketEvents,
    BracketEventDelays,
    BracketEntrants,
    Brackets,
    BracketMatchResults,
    BracketMatchReports,
    BracketNoShows,
    BracketDecisionLog
)
from app.schemas.brackets import (
    BracketEventIn,
    BracketEventUpdate,
    BracketEventOut,
    BracketEventSummary,
    BracketOut,
    DelayIn,
    DelayOut,
    EntrantHost,
    EntrantPublic,
    HostEntrantIn,
    HostResultIn,
    LogEntryOut,
    NoShowOut,
    PassTierOut,
    PaymentsOut,
    RegistrationIn,
    RegistrationOut,
    ReportOut,
    ReportRecord,
    ResultOut,
    ScoreIn,
    SeedsIn,
    SignupForm,
    check_match_id
)
from app.services import PLATFORM_FEE_PERCENT
from app.services.bracket_engine import build_bracket, bracket_progress, find_match, placements, same_matchup
from app.services.email import send_bracket_delay_email
from app.services.event_registration import check_and_claim
from app.services.users import _summary

logger = logging.getLogger(__name__)

EVENT_TYPE = "bracket_events"
PUBLIC_EVENT_URL = "https://usync.gg/brackets/{id}"
# Paid sign-ups whose payment failed or was canceled don't hold a spot.
DROPPED_PAYMENTS = ("payment_failed", "canceled")

# ---- loading ----------------------------------------------------------------

async def _event(event_id: uuid.UUID, db: AsyncSession) -> BracketEvents:
    event = await db.get(BracketEvents, event_id)
    if event is None:
        raise HTTPException(status_code = 404, detail = "Event not found.")
    return event

async def _owned_event(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> BracketEvents:
    """
    The event, if the signed-in account owns it. Anyone else gets a 404 rather than learning it exists.
    """

    event = await _event(event_id, db)
    if str(event.account_id) != account_id:
        raise HTTPException(status_code = 404, detail = "Event not found.")
    return event

async def _host_profile(account_id: str, public_id: str, db: AsyncSession) -> Profiles:
    """
    The account's own host or venue profile the event is posted as, with bracket hosting on.
    """

    profile = (await db.execute(
        select(Profiles).where(Profiles.public_id == public_id, Profiles.account_id == account_id)
    )).scalars().first()

    if profile is None or profile.type == "player":
        raise HTTPException(status_code = 403, detail = "Events are posted from one of your host or venue profiles.")
    if not profile.bracket_hosting:
        raise HTTPException(status_code = 403, detail = "Turn on bracket hosting in this profile's settings first.")
    return profile

async def _entrants(event_id: uuid.UUID, db: AsyncSession) -> list[tuple[BracketEntrants, str | None]]:
    """
    Every entrant with their registration's payment status (None for free and host-added ones),
    in sign-up order.
    """

    rows = await db.execute(
        select(BracketEntrants, EventRegistrations.payment_status)
        .outerjoin(EventRegistrations, EventRegistrations.id == BracketEntrants.registration_id)
        .where(BracketEntrants.event_id == event_id)
        .order_by(BracketEntrants.registered_at, BracketEntrants.id)
    )
    return [(entrant, status) for entrant, status in rows.all()]

def _holds_spot(entrant: BracketEntrants, payment_status: str | None) -> bool:
    return entrant.registration_id is None or payment_status not in DROPPED_PAYMENTS

def _confirmed(entrant: BracketEntrants, payment_status: str | None) -> bool:
    """
    In the event: free and host-added entrants straight away, paid ones once paid.
    """

    return entrant.registration_id is None or payment_status == "paid"

async def _bracket_state(event_id: uuid.UUID, db: AsyncSession):
    bracket = await db.get(Brackets, event_id)
    if bracket is None:
        return None, [], [], []
    results = (await db.execute(select(BracketMatchResults).where(BracketMatchResults.event_id == event_id))).scalars().all()
    reports = (await db.execute(select(BracketMatchReports).where(BracketMatchReports.event_id == event_id))).scalars().all()
    no_shows = (await db.execute(select(BracketNoShows).where(BracketNoShows.event_id == event_id))).scalars().all()
    return bracket, results, reports, no_shows

def _result_record(row: BracketMatchResults) -> dict:
    return {
        "a": str(row.entrant_a), "b": str(row.entrant_b),
        "score_a": row.score_a, "score_b": row.score_b, "forfeit": row.forfeit,
    }

def _built(bracket: Brackets, results: list[BracketMatchResults]):
    return build_bracket(bracket.format, [str(s) for s in bracket.seeds], {r.match_id: _result_record(r) for r in results})

def _stage(event: BracketEvents, bracket: Brackets | None, results: list[BracketMatchResults]) -> str:
    """
    Where the event is in its life cycle, derived from its data (same as the frontend's eventStage).
    """

    if bracket and bracket.published:
        done, total = bracket_progress(_built(bracket, results))
        return "complete" if total > 0 and done == total else "live"
    if bracket:
        return "seeding"
    return "registration" if event.registration_open else "closed"

async def _tiers(event_id: uuid.UUID, db: AsyncSession) -> list[EventPassTiers]:
    return (await db.execute(
        select(EventPassTiers)
        .where(EventPassTiers.event_id == event_id, EventPassTiers.is_active.is_(True))
        .order_by(EventPassTiers.price_cents, EventPassTiers.tier_name)
    )).scalars().all()

def _entry_fee(event: BracketEvents, tiers: list[EventPassTiers]) -> str:
    if not event.payments_enabled or not tiers:
        return "Free"
    return f"${min(t.price_cents for t in tiers) / 100:.2f}"

# ---- reads ------------------------------------------------------------------

async def get_bracket_event(event_id: uuid.UUID, viewer_id: str | None, db: AsyncSession, host_view: bool = False) -> BracketEventOut:
    """
    An event for its public page, or (host_view) everything the host manages it with. The
    public page only shows the bracket once it's published, and only confirmed entrants.
    """

    event = await _event(event_id, db)
    profile = await db.get(Profiles, event.host_profile_id)
    entrants = await _entrants(event_id, db)
    tiers = await _tiers(event_id, db)
    bracket, results, reports, no_shows = await _bracket_state(event_id, db)
    delays = (await db.execute(
        select(BracketEventDelays).where(BracketEventDelays.event_id == event_id).order_by(BracketEventDelays.created_at)
    )).scalars().all()

    bracket_out = None
    if bracket and (bracket.published or host_view):
        built = _built(bracket, results)
        grouped_reports: dict[str, dict[str, ReportRecord]] = {}
        for r in reports:
            grouped_reports.setdefault(r.match_id, {})[str(r.reporter_id)] = ReportRecord(
                a = r.entrant_a, b = r.entrant_b, score_a = r.score_a, score_b = r.score_b, at = r.reported_at
            )
        log = None
        if host_view:
            entries = (await db.execute(
                select(BracketDecisionLog).where(BracketDecisionLog.event_id == event_id).order_by(BracketDecisionLog.created_at)
            )).scalars().all()
            log = [
                LogEntryOut(
                    id = e.id, match_id = e.match_id, a = e.entrant_a, b = e.entrant_b, score_a = e.score_a, score_b = e.score_b,
                    source = e.source, at = e.created_at, reverted = e.reverted_at is not None, reverted_at = e.reverted_at
                )
                for e in entries
            ]
        bracket_out = BracketOut(
            format = bracket.format,
            seeds = bracket.seeds,
            published = bracket.published,
            generated_at = bracket.generated_at,
            results = {r.match_id: ResultOut(a = r.entrant_a, b = r.entrant_b, score_a = r.score_a, score_b = r.score_b, forfeit = r.forfeit) for r in results},
            reports = grouped_reports,
            no_shows = {
                n.match_id: NoShowOut(a = n.entrant_a, b = n.entrant_b, reported_by = n.reported_by, against = n.against, at = n.reported_at)
                for n in no_shows
            },
            placements = placements(bracket.format, built) if bracket.published else None,
            log = log,
        )

    if host_view:
        entrant_list = [
            EntrantHost(
                id = e.id, name = e.name, captain = e.captain, checked_in = e.checked_in, teammates = e.teammates,
                email = e.email, answers = e.answers, rating = e.rating, registered_at = e.registered_at,
                payment_status = status or ("free" if e.account_id else "manual"),
                account_linked = e.account_id is not None,
            )
            for e, status in entrants if _holds_spot(e, status)
        ]
    else:
        entrant_list = [
            EntrantPublic(id = e.id, name = e.name, captain = e.captain, checked_in = e.checked_in)
            for e, status in entrants if _confirmed(e, status)
        ]

    viewer_entrant = next((e.id for e, status in entrants if viewer_id and str(e.account_id) == viewer_id and _holds_spot(e, status)), None)

    return BracketEventOut(
        id = event.event_id,
        host_profile = _summary(profile),
        name = event.name,
        game = event.game,
        format = event.format,
        team_size = event.team_size,
        best_of = event.best_of,
        max_entrants = event.max_entrants,
        starts_at = event.starts_at,
        location_type = event.location_type,
        venue = event.venue,
        description = event.description,
        rules = event.rules,
        prize = event.prize,
        entry_fee = _entry_fee(event, tiers),
        check_in_required = event.check_in_required,
        no_show_minutes = event.no_show_minutes,
        registration_open = event.registration_open,
        stage = _stage(event, bracket, results),
        signup_form = SignupForm.model_validate({"player_fields": event.signup_player_fields, "questions": event.signup_questions}),
        payments = PaymentsOut(
            enabled = event.payments_enabled,
            tiers = [PassTierOut(id = t.id, name = t.tier_name, price_cents = t.price_cents, capacity = t.capacity, sold_count = t.sold_count) for t in tiers],
            payout_email = event.payout_email if host_view else None,
        ),
        delays = [DelayOut(from_starts_at = d.from_starts_at, to_starts_at = d.to_starts_at, reason = d.reason, at = d.created_at) for d in delays],
        entrant_count = sum(_holds_spot(e, s) for e, s in entrants),
        entrants = entrant_list,
        bracket = bracket_out,
        viewer_entrant_id = viewer_entrant,
        created_at = event.created_at,
    )

async def get_managed_event(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> BracketEventOut:
    """
    The host's view of an event they own.
    """

    await _owned_event(event_id, account_id, db)
    return await get_bracket_event(event_id, account_id, db, host_view = True)

async def list_hosted_events(account_id: str, db: AsyncSession) -> list[BracketEventSummary]:
    """
    The Host Dashboard: every bracket event the account posted, from any of its profiles,
    newest first.
    """

    events = (await db.execute(
        select(BracketEvents).where(BracketEvents.account_id == account_id).order_by(BracketEvents.created_at.desc())
    )).scalars().all()

    out = []
    for event in events:
        profile = await db.get(Profiles, event.host_profile_id)
        entrants = await _entrants(event.event_id, db)
        bracket, results, _, _ = await _bracket_state(event.event_id, db)
        out.append(BracketEventSummary(
            id = event.event_id,
            host_profile = _summary(profile),
            name = event.name,
            game = event.game,
            format = event.format,
            team_size = event.team_size,
            starts_at = event.starts_at,
            stage = _stage(event, bracket, results),
            entrant_count = sum(_holds_spot(e, s) for e, s in entrants),
            max_entrants = event.max_entrants,
            created_at = event.created_at,
        ))
    return out

# ---- host: the event ---------------------------------------------------------

async def create_bracket_event(payload: BracketEventIn, account_id: str, db: AsyncSession) -> uuid.UUID:
    """
    Creates the events row (event_type "bracket_events", owned by the account), the
    bracket_events row, and for paid events one event_pass_tiers row per pass. Registration
    opens straight away.
    """

    profile = await _host_profile(account_id, payload.host_profile, db)
    parent = EventFormParent(user_id = profile.account_id, event_type = EVENT_TYPE)
    db.add(parent)
    await db.flush()

    pay = payload.payments
    db.add(BracketEvents(
        event_id = parent.id,
        account_id = profile.account_id,
        host_profile_id = profile.id,
        name = payload.name,
        game = payload.game,
        format = payload.format,
        team_size = payload.team_size,
        best_of = payload.best_of,
        max_entrants = payload.max_entrants,
        starts_at = payload.starts_at,
        location_type = payload.location_type,
        venue = payload.venue if payload.location_type == "in_person" else None,
        description = payload.description,
        rules = payload.rules,
        prize = payload.prize,
        check_in_required = payload.check_in_required,
        no_show_minutes = payload.no_show_minutes,
        registration_open = True,
        signup_player_fields = payload.signup_form.player_fields,
        signup_questions = [q.model_dump() for q in payload.signup_form.questions],
        payments_enabled = pay.enabled,
        payout_email = pay.payout_email if pay.enabled else None,
    ))

    if pay.enabled:
        for tier in pay.tiers:
            db.add(EventPassTiers(
                event_id = parent.id,
                user_id = profile.account_id,
                host_email = pay.payout_email,
                tier_name = tier.name,
                price_cents = tier.price_cents,
                capacity = tier.capacity,
                requires_team = payload.team_size > 1,
                team_size = payload.team_size,
                form_fields = [],
                player_fields = [],
            ))

    await db.commit()
    return parent.id

async def update_bracket_event(event_id: uuid.UUID, payload: BracketEventUpdate, account_id: str, db: AsyncSession) -> None:
    event = await _owned_event(event_id, account_id, db)
    changes = payload.model_dump(exclude_unset = True)
    bracket = await db.get(Brackets, event_id)
    published = bool(bracket and bracket.published)

    if changes.get("registration_open") and published:
        raise HTTPException(status_code = 409, detail = "The bracket is live, so sign-ups stay closed.")
    if "best_of" in changes and published and changes["best_of"] != event.best_of:
        raise HTTPException(status_code = 409, detail = "Matches are already being played at this length.")
    if changes.get("max_entrants") is not None:
        held = sum(_holds_spot(e, s) for e, s in await _entrants(event_id, db))
        if changes["max_entrants"] < held:
            raise HTTPException(status_code = 409, detail = f"{held} have already signed up, so the cap can't go below that.")

    location = changes.get("location_type", event.location_type)
    venue = changes.get("venue", event.venue)
    if location == "in_person" and not venue:
        raise HTTPException(status_code = 422, detail = "Add where the event is held.")
    if location == "online":
        changes["venue"] = None

    for field, value in changes.items():
        if value is None and field not in ("max_entrants", "no_show_minutes", "prize", "venue"):
            continue
        setattr(event, field, value)

    await db.commit()

async def delete_bracket_event(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> None:
    """
    Deletes the event and everything under it. Events with paid sign-ups can't be deleted here:
    those players need refunding first.
    """

    await _owned_event(event_id, account_id, db)
    paid = (await db.execute(
        select(func.count()).select_from(EventRegistrations)
        .where(EventRegistrations.event_id == event_id, EventRegistrations.payment_status == "paid")
    )).scalar_one()
    if paid:
        raise HTTPException(status_code = 409, detail = "Players have paid for this event. Contact uSync to refund them before deleting it.")

    await db.execute(delete(BracketEntrants).where(BracketEntrants.event_id == event_id))
    await db.execute(delete(EventRegistrations).where(EventRegistrations.event_id == event_id))
    await db.execute(delete(EventPassTiers).where(EventPassTiers.event_id == event_id))
    # bracket_events (and its bracket, delays and logs) cascade from events.
    await db.execute(delete(EventFormParent).where(EventFormParent.id == event_id))
    await db.commit()

async def delay_start(event_id: uuid.UUID, payload: DelayIn, account_id: str, db: AsyncSession) -> None:
    """
    Moves the start later, records why, and emails everyone signed up.
    """

    event = await _owned_event(event_id, account_id, db)
    if payload.starts_at <= event.starts_at:
        raise HTTPException(status_code = 422, detail = "Pick a time after the current start.")

    db.add(BracketEventDelays(event_id = event_id, from_starts_at = event.starts_at, to_starts_at = payload.starts_at, reason = payload.reason))
    event.starts_at = payload.starts_at
    await db.commit()

    emails = [e.email for e, s in await _entrants(event_id, db) if e.email and _holds_spot(e, s)]
    local = payload.starts_at.astimezone(ZoneInfo("America/New_York"))
    when = f"{local:%a %b} {local.day}, {local.hour % 12 or 12}:{local:%M %p} ET"
    try:
        await send_bracket_delay_email(emails, event.name, when, payload.reason, PUBLIC_EVENT_URL.format(id = event_id))
    except Exception:
        logger.exception("Failed to email the new start time for bracket event %s", event_id)

# ---- entrants ----------------------------------------------------------------

def _normalize(name: str) -> str:
    return " ".join(name.split()).lower()

async def _check_room(event: BracketEvents, db: AsyncSession) -> None:
    if event.max_entrants is None:
        return
    held = sum(_holds_spot(e, s) for e, s in await _entrants(event.event_id, db))
    if held >= event.max_entrants:
        raise HTTPException(status_code = 409, detail = "This event is full.")

async def _commit_entrant(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "uq_bracket_entrants_event_name" in str(e.orig):
            raise HTTPException(status_code = 409, detail = "That name is already signed up for this event.")
        if "uq_bracket_entrants_event_account" in str(e.orig):
            raise HTTPException(status_code = 409, detail = "You're already signed up for this event.")
        if "uq_registrations_event_team_name" in str(e.orig):
            raise HTTPException(status_code = 409, detail = "That team name is already signed up for this event.")
        if "uq_registrations_individual_contact" in str(e.orig):
            raise HTTPException(status_code = 409, detail = "That email is already signed up for this pass.")
        raise

async def add_host_entrant(event_id: uuid.UUID, payload: HostEntrantIn, account_id: str, db: AsyncSession) -> uuid.UUID:
    """
    The host adds a player or team by name (walk-ins, people who signed up elsewhere).
    """

    event = await _owned_event(event_id, account_id, db)
    bracket = await db.get(Brackets, event_id)
    if bracket and bracket.published:
        raise HTTPException(status_code = 409, detail = "The bracket is live. Reset it to add more entrants.")
    await _check_room(event, db)

    entrant = BracketEntrants(
        event_id = event_id, name = payload.name, name_normalized = _normalize(payload.name),
        captain = payload.name, checked_in = not event.check_in_required,
    )
    db.add(entrant)
    await _commit_entrant(db)
    return entrant.id

async def _drop_from_draft(event_id: uuid.UUID, entrant_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Removing an entrant is blocked once the bracket is live; from a draft they're just unseeded.
    """

    bracket = await db.get(Brackets, event_id)
    if bracket is None:
        return
    if bracket.published:
        raise HTTPException(status_code = 409, detail = "The bracket is live. Reset it before removing entrants.")
    if entrant_id in bracket.seeds:
        bracket.seeds = [s for s in bracket.seeds if s != entrant_id]

async def _release(entrant: BracketEntrants, db: AsyncSession) -> None:
    """
    Gives an unpaid pass back and removes its registration.
    """

    if entrant.registration_id is None:
        return
    registration = await db.get(EventRegistrations, entrant.registration_id)
    if registration and registration.payment_status != "paid":
        tier = await db.get(EventPassTiers, registration.pass_tier_id)
        if tier and tier.sold_count > 0:
            tier.sold_count -= 1
        await db.delete(registration)

async def remove_entrant(event_id: uuid.UUID, entrant_id: uuid.UUID, account_id: str, db: AsyncSession) -> None:
    """
    The host removes an entrant. A paid entrant's registration is kept (as the payment record);
    refunds are handled outside this API.
    """

    await _owned_event(event_id, account_id, db)
    entrant = await db.get(BracketEntrants, entrant_id)
    if entrant is None or entrant.event_id != event_id:
        raise HTTPException(status_code = 404, detail = "Entrant not found.")
    await _drop_from_draft(event_id, entrant_id, db)
    await _release(entrant, db)
    await db.delete(entrant)
    await db.commit()

async def set_checked_in(event_id: uuid.UUID, entrant_id: uuid.UUID, checked_in: bool, account_id: str, db: AsyncSession) -> None:
    await _owned_event(event_id, account_id, db)
    entrant = await db.get(BracketEntrants, entrant_id)
    if entrant is None or entrant.event_id != event_id:
        raise HTTPException(status_code = 404, detail = "Entrant not found.")
    entrant.checked_in = checked_in
    await db.commit()

def _clean_answers(event: BracketEvents, answers: dict[str, str]) -> dict[str, str]:
    """
    Keeps answers to the event's own fields and questions, and checks the required ones and
    dropdown choices.
    """

    questions = {q["label"]: q for q in event.signup_questions}
    known = set(event.signup_player_fields) | set(questions)
    cleaned = {k: v.strip()[:200] for k, v in answers.items() if k in known and v and v.strip()}

    missing = [label for label, q in questions.items() if q.get("required") and label not in cleaned]
    if missing:
        raise HTTPException(status_code = 422, detail = f"Fill in: {', '.join(missing)}.")
    for label, q in questions.items():
        if q.get("type") == "select" and label in cleaned and cleaned[label] not in q.get("options", []):
            raise HTTPException(status_code = 422, detail = f"Pick one of the options for '{label}'.")
    return cleaned

async def register(event_id: uuid.UUID, payload: RegistrationIn, account_id: str, db: AsyncSession) -> RegistrationOut:
    """
    A signed-in player signs up. Free events confirm straight away. Paid events claim a pass and
    create a pending event_registrations row; the frontend then takes payment through the
    existing /event-registration/{registration_id}/payment-intent flow, and the Stripe webhook
    marks it paid (which is what confirms the entrant).
    """

    event = await _event(event_id, db)
    bracket = await db.get(Brackets, event_id)
    if not event.registration_open or (bracket and bracket.published):
        raise HTTPException(status_code = 409, detail = "Sign-ups are closed.")
    await _check_room(event, db)

    await _clear_dropped(event_id, account_id, _normalize(payload.name), db)

    team = event.team_size > 1
    if team and not payload.captain:
        raise HTTPException(status_code = 422, detail = "Add the captain's gamertag.")
    if len(payload.teammates) > event.team_size - 1:
        raise HTTPException(status_code = 422, detail = f"A team has {event.team_size} players at most.")

    entrant = BracketEntrants(
        event_id = event_id,
        name = payload.name,
        name_normalized = _normalize(payload.name),
        captain = payload.captain if team else payload.name,
        teammates = payload.teammates if team else [],
        email = payload.email,
        answers = _clean_answers(event, payload.answers),
        checked_in = not event.check_in_required,
        account_id = account_id,
    )

    out_total = None
    if event.payments_enabled:
        if payload.pass_tier_id is None:
            raise HTTPException(status_code = 422, detail = "Choose a pass.")
        tier = await db.get(EventPassTiers, payload.pass_tier_id)
        if tier is None or tier.event_id != event_id or not tier.is_active:
            raise HTTPException(status_code = 404, detail = "Pass not found.")

        fee = (tier.price_cents * PLATFORM_FEE_PERCENT + 50) // 100
        registration = EventRegistrations(
            event_id = event_id,
            pass_tier_id = tier.id,
            user_id = account_id,
            team_name = payload.name if team else None,
            team_name_normalized = _normalize(payload.name) if team else None,
            contact_username = entrant.captain,
            contact_email = payload.email,
            player_info = [{"gamertag": p} for p in [entrant.captain, *entrant.teammates]],
            price_snapshot_cents = tier.price_cents,
            fee_snapshot_cents = fee,
            total_snapshot_cents = tier.price_cents + fee,
            custom_fields = entrant.answers,
        )
        await check_and_claim(registration, db)
        db.add(registration)
        await db.flush()
        entrant.registration_id = registration.id
        out_total = registration.total_snapshot_cents
    elif payload.pass_tier_id is not None:
        raise HTTPException(status_code = 422, detail = "This event is free.")

    db.add(entrant)
    await _commit_entrant(db)
    return RegistrationOut(entrant_id = entrant.id, registration_id = entrant.registration_id, total_snapshot_cents = out_total)

async def _clear_dropped(event_id: uuid.UUID, account_id: str, name_normalized: str, db: AsyncSession) -> None:
    """
    A paid sign-up whose payment failed or was canceled doesn't hold its spot, its name or the
    player's place in line: it's removed (with its unpaid registration) so they can sign up again.
    """

    for entrant, status in await _entrants(event_id, db):
        if status in DROPPED_PAYMENTS and (str(entrant.account_id) == account_id or entrant.name_normalized == name_normalized):
            await _release(entrant, db)
            await db.delete(entrant)
    await db.flush()

async def _viewer_entrant(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> BracketEntrants:
    entrant = (await db.execute(
        select(BracketEntrants).where(BracketEntrants.event_id == event_id, BracketEntrants.account_id == account_id)
    )).scalars().first()
    if entrant is None:
        raise HTTPException(status_code = 404, detail = "You're not signed up for this event.")
    return entrant

async def unregister(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> None:
    """
    A player drops out before the bracket goes live. Paid entrants ask the host instead, so a
    refund can be sorted out.
    """

    entrant = await _viewer_entrant(event_id, account_id, db)
    if entrant.registration_id:
        registration = await db.get(EventRegistrations, entrant.registration_id)
        if registration and registration.payment_status == "paid":
            raise HTTPException(status_code = 409, detail = "You've paid for this event, so ask the host to remove you.")
    await _drop_from_draft(event_id, entrant.id, db)
    await _release(entrant, db)
    await db.delete(entrant)
    await db.commit()

async def check_in(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> None:
    event = await _event(event_id, db)
    bracket = await db.get(Brackets, event_id)
    if not event.check_in_required or (bracket and bracket.published):
        raise HTTPException(status_code = 409, detail = "Check-in isn't open for this event.")
    entrant = await _viewer_entrant(event_id, account_id, db)
    entrant.checked_in = True
    await db.commit()

# ---- bracket: seeding and publishing ----------------------------------------

async def _clear_bracket_rows(event_id: uuid.UUID, db: AsyncSession) -> None:
    for table in (BracketMatchResults, BracketMatchReports, BracketNoShows, BracketDecisionLog):
        await db.execute(delete(table).where(table.event_id == event_id))

async def save_bracket_draft(event_id: uuid.UUID, payload: SeedsIn, account_id: str, db: AsyncSession) -> None:
    """
    Saves the seeding as a draft bracket (replacing any earlier draft). Every seed must be a
    confirmed entrant of this event.
    """

    event = await _owned_event(event_id, account_id, db)
    bracket = await db.get(Brackets, event_id)
    if bracket and bracket.published:
        raise HTTPException(status_code = 409, detail = "The bracket is live. Reset it to reseed.")

    confirmed = {e.id for e, s in await _entrants(event_id, db) if _confirmed(e, s)}
    if any(seed not in confirmed for seed in payload.seeds):
        raise HTTPException(status_code = 422, detail = "Only confirmed entrants of this event can be seeded.")

    await _clear_bracket_rows(event_id, db)
    if bracket is None:
        db.add(Brackets(event_id = event_id, format = event.format, seeds = payload.seeds))
    else:
        bracket.format = event.format
        bracket.seeds = payload.seeds
        bracket.generated_at = datetime.now(timezone.utc)
    await db.commit()

async def publish_bracket(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> None:
    """
    Players can see the bracket and report results. Sign-ups close.
    """

    event = await _owned_event(event_id, account_id, db)
    bracket = await db.get(Brackets, event_id)
    if bracket is None:
        raise HTTPException(status_code = 409, detail = "Seed the bracket first.")
    if len(bracket.seeds) < 2:
        raise HTTPException(status_code = 409, detail = "A bracket needs at least 2 entrants.")
    if not bracket.published:
        bracket.published = True
        bracket.published_at = datetime.now(timezone.utc)
        event.registration_open = False
        await db.commit()

async def reset_bracket(event_id: uuid.UUID, account_id: str, db: AsyncSession) -> None:
    await _owned_event(event_id, account_id, db)
    await _clear_bracket_rows(event_id, db)
    await db.execute(delete(Brackets).where(Brackets.event_id == event_id))
    await db.commit()

# ---- bracket: results --------------------------------------------------------

def _known_match_id(match_id: str) -> None:
    try:
        check_match_id(match_id)
    except ValueError:
        raise HTTPException(status_code = 404, detail = "Match not found.")

async def _live_match(event: BracketEvents, match_id: str, db: AsyncSession):
    """
    The published bracket, its rows, and the match as it stands now.
    """

    _known_match_id(match_id)
    bracket, results, reports, no_shows = await _bracket_state(event.event_id, db)
    if bracket is None or not bracket.published:
        raise HTTPException(status_code = 409, detail = "The bracket isn't live.")
    match = find_match(_built(bracket, results), match_id)
    if match is None:
        raise HTTPException(status_code = 404, detail = "Match not found.")
    return bracket, results, reports, no_shows, match

def _check_scores(event: BracketEvents, payload: ScoreIn) -> None:
    most = math.ceil(event.best_of / 2)
    if payload.score_a > most or payload.score_b > most:
        raise HTTPException(status_code = 422, detail = f"Best of {event.best_of} is first to {most}.")

def _snapshot(match_id: str, results, reports, no_shows) -> tuple[dict | None, dict | None, dict | None]:
    """
    What a decision wipes besides the result, kept on the log entry for Revert.
    """

    result = next((r for r in results if r.match_id == match_id), None)
    match_reports = {
        str(r.reporter_id): {"a": str(r.entrant_a), "b": str(r.entrant_b), "score_a": r.score_a, "score_b": r.score_b, "at": r.reported_at.isoformat()}
        for r in reports if r.match_id == match_id
    }
    no_show = next((n for n in no_shows if n.match_id == match_id), None)
    return (
        _result_record(result) if result else None,
        match_reports or None,
        {
            "a": str(no_show.entrant_a), "b": str(no_show.entrant_b), "reported_by": str(no_show.reported_by),
            "against": str(no_show.against), "at": no_show.reported_at.isoformat(),
        } if no_show else None,
    )

async def _clear_match_meta(event_id: uuid.UUID, match_id: str, db: AsyncSession) -> None:
    await db.execute(delete(BracketMatchReports).where(BracketMatchReports.event_id == event_id, BracketMatchReports.match_id == match_id))
    await db.execute(delete(BracketNoShows).where(BracketNoShows.event_id == event_id, BracketNoShows.match_id == match_id))

async def _set_result(event_id: uuid.UUID, match, score_a: int, score_b: int, forfeit: bool, db: AsyncSession) -> None:
    await db.execute(delete(BracketMatchResults).where(BracketMatchResults.event_id == event_id, BracketMatchResults.match_id == match.id))
    db.add(BracketMatchResults(
        event_id = event_id, match_id = match.id, entrant_a = uuid.UUID(match.a), entrant_b = uuid.UUID(match.b),
        score_a = score_a, score_b = score_b, forfeit = forfeit,
    ))

def _log(event_id, match, score_a, score_b, source, snapshot, actor) -> BracketDecisionLog:
    prev_result, prev_reports, prev_no_show = snapshot
    return BracketDecisionLog(
        event_id = event_id, match_id = match.id, entrant_a = uuid.UUID(match.a), entrant_b = uuid.UUID(match.b),
        score_a = score_a, score_b = score_b, source = source,
        prev_result = prev_result, prev_reports = prev_reports, prev_no_show = prev_no_show, actor_account_id = actor,
    )

async def host_report(event_id: uuid.UUID, match_id: str, payload: HostResultIn, account_id: str, db: AsyncSession) -> None:
    """
    The host's call is final: sets (or overrides) a match result, clearing its reports and any
    no-show. A forfeit is recorded as a win by the most games.
    """

    event = await _owned_event(event_id, account_id, db)
    _, results, reports, no_shows, match = await _live_match(event, match_id, db)
    if match.status not in ("ready", "complete"):
        raise HTTPException(status_code = 409, detail = "This match isn't ready to be played.")
    _check_scores(event, payload)

    snapshot = _snapshot(match_id, results, reports, no_shows)
    await _set_result(event_id, match, payload.score_a, payload.score_b, payload.forfeit, db)
    await _clear_match_meta(event_id, match_id, db)
    db.add(_log(event_id, match, payload.score_a, payload.score_b, "forfeit" if payload.forfeit else "host", snapshot, account_id))
    await db.commit()

async def clear_match(event_id: uuid.UUID, match_id: str, account_id: str, db: AsyncSession) -> None:
    """
    Un-reports a match. Later matches it fed into reset on their own (the bracket is derived).
    """

    event = await _owned_event(event_id, account_id, db)
    _, results, reports, no_shows, match = await _live_match(event, match_id, db)
    if match.status != "complete":
        raise HTTPException(status_code = 409, detail = "This match has no result to clear.")

    snapshot = _snapshot(match_id, results, reports, no_shows)
    await db.execute(delete(BracketMatchResults).where(BracketMatchResults.event_id == event_id, BracketMatchResults.match_id == match_id))
    await _clear_match_meta(event_id, match_id, db)
    db.add(_log(event_id, match, None, None, "cleared", snapshot, account_id))
    await db.commit()

async def dismiss_no_show(event_id: uuid.UUID, match_id: str, account_id: str, db: AsyncSession) -> None:
    await _owned_event(event_id, account_id, db)
    _known_match_id(match_id)
    await db.execute(delete(BracketNoShows).where(BracketNoShows.event_id == event_id, BracketNoShows.match_id == match_id))
    await db.commit()

async def revert_decision(event_id: uuid.UUID, entry_id: uuid.UUID, account_id: str, db: AsyncSession) -> None:
    """
    Puts a match back how it was before that decision: its result, reports and no-show.
    """

    await _owned_event(event_id, account_id, db)
    entry = await db.get(BracketDecisionLog, entry_id)
    if entry is None or entry.event_id != event_id:
        raise HTTPException(status_code = 404, detail = "Decision not found.")
    if entry.reverted_at is not None:
        return

    match_id = entry.match_id
    await db.execute(delete(BracketMatchResults).where(BracketMatchResults.event_id == event_id, BracketMatchResults.match_id == match_id))
    await _clear_match_meta(event_id, match_id, db)

    if entry.prev_result:
        r = entry.prev_result
        db.add(BracketMatchResults(
            event_id = event_id, match_id = match_id, entrant_a = uuid.UUID(r["a"]), entrant_b = uuid.UUID(r["b"]),
            score_a = r["score_a"], score_b = r["score_b"], forfeit = bool(r.get("forfeit")),
        ))
    for reporter, r in (entry.prev_reports or {}).items():
        db.add(BracketMatchReports(
            event_id = event_id, match_id = match_id, reporter_id = uuid.UUID(reporter), entrant_a = uuid.UUID(r["a"]),
            entrant_b = uuid.UUID(r["b"]), score_a = r["score_a"], score_b = r["score_b"], reported_at = datetime.fromisoformat(r["at"]),
        ))
    if entry.prev_no_show:
        n = entry.prev_no_show
        db.add(BracketNoShows(
            event_id = event_id, match_id = match_id, entrant_a = uuid.UUID(n["a"]), entrant_b = uuid.UUID(n["b"]),
            reported_by = uuid.UUID(n["reported_by"]), against = uuid.UUID(n["against"]), reported_at = datetime.fromisoformat(n["at"]),
        ))

    entry.reverted_at = datetime.now(timezone.utc)
    await db.commit()

# ---- bracket: players ---------------------------------------------------------

async def _player_match(event_id: uuid.UUID, match_id: str, account_id: str, db: AsyncSession):
    event = await _event(event_id, db)
    me = await _viewer_entrant(event_id, account_id, db)
    bracket, results, reports, no_shows, match = await _live_match(event, match_id, db)
    if match.status != "ready" or str(me.id) not in (match.a, match.b):
        raise HTTPException(status_code = 409, detail = "You don't have this match to play right now.")
    return event, me, results, reports, no_shows, match

async def player_report(event_id: uuid.UUID, match_id: str, payload: ScoreIn, account_id: str, db: AsyncSession) -> ReportOut:
    """
    One side reports the score. When both sides agree on the winner the result is confirmed and
    the winner advances; when they disagree it's disputed and the host decides.
    """

    event, me, results, reports, no_shows, match = await _player_match(event_id, match_id, account_id, db)
    _check_scores(event, payload)

    opponent = match.b if str(me.id) == match.a else match.a
    theirs = next(
        (r for r in reports if r.match_id == match_id and str(r.reporter_id) == opponent
         and same_matchup({"a": str(r.entrant_a), "b": str(r.entrant_b)}, match)),
        None,
    )

    if theirs and (theirs.score_a > theirs.score_b) == (payload.score_a > payload.score_b):
        snapshot = _snapshot(match_id, results, reports, no_shows)
        await _set_result(event_id, match, payload.score_a, payload.score_b, False, db)
        await _clear_match_meta(event_id, match_id, db)
        db.add(_log(event_id, match, payload.score_a, payload.score_b, "players", (snapshot[0], None, None), account_id))
        await db.commit()
        return ReportOut(status = "confirmed")

    await db.execute(delete(BracketMatchReports).where(
        BracketMatchReports.event_id == event_id, BracketMatchReports.match_id == match_id, BracketMatchReports.reporter_id == me.id
    ))
    db.add(BracketMatchReports(
        event_id = event_id, match_id = match_id, reporter_id = me.id, entrant_a = uuid.UUID(match.a), entrant_b = uuid.UUID(match.b),
        score_a = payload.score_a, score_b = payload.score_b,
    ))
    await db.commit()
    return ReportOut(status = "disputed" if theirs else "waiting")

async def report_no_show(event_id: uuid.UUID, match_id: str, account_id: str, db: AsyncSession) -> None:
    """
    A player reports their opponent didn't show. Unlocks no_show_minutes after the start; the
    host reviews it (forfeit the match, or dismiss).
    """

    event, me, _, _, _, match = await _player_match(event_id, match_id, account_id, db)
    if event.no_show_minutes is None:
        raise HTTPException(status_code = 409, detail = "This event doesn't use no-show reports.")
    if datetime.now(timezone.utc) < event.starts_at + timedelta(minutes = event.no_show_minutes):
        raise HTTPException(status_code = 409, detail = f"No-shows can be reported {event.no_show_minutes} minutes after the start.")

    await db.execute(delete(BracketNoShows).where(BracketNoShows.event_id == event_id, BracketNoShows.match_id == match_id))
    db.add(BracketNoShows(
        event_id = event_id, match_id = match_id, entrant_a = uuid.UUID(match.a), entrant_b = uuid.UUID(match.b),
        reported_by = me.id, against = uuid.UUID(match.b if str(me.id) == match.a else match.a),
    ))
    await db.commit()
