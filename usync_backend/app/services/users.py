import uuid
import logging
import io
import base64
import secrets
import string
import unicodedata
import re

from typing import NoReturn
from pydantic import ValidationError
from sqlalchemy import select, insert, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from app.schemas.links import check_link_types
from app.schemas.users import (
    ProfileEvent,
    RegistrationIn,
    RegistrationOut,
    ProfileIn,
    ProfileType,
    ProfileSummary,
    Me,
    Profile,
    PlayerPublic,
    HostPublic,
    VenuePublic,
    VenueOut,
    PlayerSettings,
    HostSettings,
    VenueSettings,
    ProfileSettingsOut,
    ProfileUpdate,
    PlayerUpdate,
    HostUpdate,
    VenueUpdate
)
from app.models.users import UsersParent, Profiles, ProfileLinks, Players, Hosts, Venues
from app.models.host_data import EventFormParent, LanEvents, LeagueParentEvents, LeagueEvents, WagerEvents, XpEvents
from PIL import Image, ImageOps, UnidentifiedImageError
from supabase import AsyncClient
from storage3.utils import StorageException
from supabase_auth.errors import AuthApiError
from openai import AsyncOpenAI
from better_profanity import profanity

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 5 * 1024 * 1024
ACCEPTED_IMAGE_TYPES = {"JPEG", "WEBP", "PNG"}
ALLOWED_SYMBOLS = {"_", ".", "-"}
ALLOWED_CHAR_FORMATS = {"‌", "‍"}
MIN_USER_LEN = 4
MAX_USER_LEN = 25
_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")

# public_id goes in profile URLs: random (not sequential, so profiles can't be enumerated) and short.
PUBLIC_ID_ALPHABET = string.ascii_lowercase + string.digits
PUBLIC_ID_LENGTH = 10

DETAIL_UPDATES = {"player": PlayerUpdate, "host": HostUpdate, "venue": VenueUpdate}
GAME_ACCOUNT_LABELS = {"battlenet": "Battle.net", "activision": "Activision", "steam": "Steam", "riot": "Riot"}
LINK_LABELS = {
    "twitch": "Twitch", "twitter": "Twitter", "youtube": "YouTube", "kick": "Kick", "discord": "Discord",
    "instagram": "Instagram", "cmg": "CMG", "gankster": "Gankster", "faceit": "FACEIT", "battlefy": "Battlefy",
    "website": "Website", "tiktok": "TikTok", "linkedin": "LinkedIn", "facebook": "Facebook", "linktree": "Linktree"
}
_USERNAME_CONFLICT_RE = re.compile(r"\(type, canonical_username\)=\((\w+),")
_LINK_CONFLICT_RE = re.compile(r"\(platform, canonical_handle, account_id\)=\((\w+),")

profanity.load_censor_words()

def _field_error(loc: tuple, msg: str) -> HTTPException:
    """
    A 422 shaped like FastAPI's own validation errors, so the frontend can point at the field.
    """

    return HTTPException(status_code=422, detail=[{"loc": list(loc), "msg": msg, "type": "value_error"}])

def _raise_conflict(e: IntegrityError, action: str, profile_type: str | None = None) -> NoReturn:
    """
    Turns a unique violation from a profile write into a 409 the user can act on. Anything
    unexpected is logged and gets a generic message.
    """

    message = str(e.orig)

    if "uq_profiles_type_canonical_username" in message:
        match = _USERNAME_CONFLICT_RE.search(message)
        taken_type = match.group(1) if match else profile_type
        raise HTTPException(status_code=409, detail=f"That {taken_type} username is taken.")

    if "uq_profiles_account_type" in message:
        raise HTTPException(status_code=409, detail=f"You already have a {profile_type or 'profile of that'} profile.")

    if "ex_profile_links_claimed" in message:
        match = _LINK_CONFLICT_RE.search(message)
        label = LINK_LABELS.get(match.group(1), "linked") if match else "linked"
        raise HTTPException(status_code=409, detail=f"That {label} account is already linked to another user.")

    if "users_pkey" in message:
        raise HTTPException(status_code=409, detail="Your profile is already set up.")

    for column, label in GAME_ACCOUNT_LABELS.items():
        if f"players_{column}_key" in message:
            raise HTTPException(status_code=409, detail=f"That {label} account is already linked to another player.")

    logger.exception("%s failed with unexpected integrity error.", action)
    raise HTTPException(status_code=409, detail=f"{action} conflicts with existing data.")

def _canonical_handle(handle: str) -> str:
    return unicodedata.normalize("NFKC", handle).casefold()

def _new_public_id() -> str:
    return "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(PUBLIC_ID_LENGTH))

def _build_profile(profile: ProfileIn, account_id: str, pfp_url: str | None) -> tuple[Profiles, list[ProfileLinks | Players | Hosts | Venues]]:
    """
    Builds a profiles row and the rows that hang off it (links and the type's details). The
    profile's id is set here so the child rows can point at it before anything is flushed.
    """

    row = Profiles(
        id = uuid.uuid4(),
        public_id = _new_public_id(),
        account_id = account_id,
        type = profile.type,
        username = profile.username,
        canonical_username = _canonical_username(profile.username),
        bio = profile.bio,
        profile_picture = pfp_url,
        bracket_hosting = profile.bracket_hosting,
        other_roles = sorted(profile.other_roles) if profile.other_roles is not None else None,
        other_role_detail = profile.other_role_detail
    )

    children: list[ProfileLinks | Players | Hosts | Venues] = [
        ProfileLinks(
            profile_id = row.id,
            account_id = account_id,
            platform = platform,
            handle = handle,
            canonical_handle = _canonical_handle(handle)
        )
        for platform, handle in (profile.links or {}).items()
    ]

    details = profile.details

    if profile.type == "player":
        children.append(Players(
            profile_id = row.id,
            first_name = details.first_name,
            last_name = details.last_name,
            phone_number = details.phone_number,
            gender = details.gender,
            date_of_birth = details.date_of_birth,
            country = details.country,
            games = details.interests,
            other_games = details.other_games,
            battlenet = details.battlenet,
            activision = details.activision,
            steam = details.steam,
            riot = details.riot
        ))
    elif profile.type == "host":
        children.append(Hosts(
            profile_id = row.id,
            games = details.hosted_games,
            other_games = details.other_hosted_games,
            organization = details.organization,
            host_country = details.host_country,
            event_types = details.event_types
        ))
    else:
        # Keyed by name since (profile_id, venue_name) is the primary key.
        unique_locations = {v.name: v.location for v in details.locations}
        children.extend(
            Venues(profile_id = row.id, venue_name = name, location = location)
            for name, location in unique_locations.items()
        )

    return row, children

async def _insert_profiles(rows: list[tuple[Profiles, list]], db: AsyncSession) -> None:
    """
    Profiles are flushed before their child rows so the child foreign keys have something to point at.
    """

    db.add_all([profile for profile, _ in rows])
    await db.flush()
    db.add_all([child for _, children in rows for child in children])
    await db.flush()

    return None

def _split_normalized(normalized_username: str) -> str:
    """
    
    """

    s = re.sub(r"[_./-]+", " ", normalized_username)
    return _SPLIT_RE.sub(" ", s)

def profanity_moderation(normalized: str, split: str) -> None:
    """
    
    """

    for candidate in (normalized, split):
        if profanity.contains_profanity(candidate):
            raise ValueError("Thise username is not allowed. Please choose another.")

    return None

async def openai_username_moderation(normalized_username: str, split_username: str, openai: AsyncOpenAI) -> None:
    """
    
    """

    response = await openai.moderations.create(
        model = "omni-moderation-latest",
        input = [
            normalized_username,
            split_username,
        ],
    )

    if any(r.flagged for r in response.results):
        raise ValueError("This username is not allowed. Please choose another.")

    return None

def _check_user_restrictions(canonical_username: str) -> None:
    """
    
    """

    if len(canonical_username) < MIN_USER_LEN:
        raise ValueError("Username is too short.")

    if len(canonical_username) > MAX_USER_LEN:
        raise ValueError("Username is too long.")

    combining_run = 0
    for ch in canonical_username:
        cat = unicodedata.category(ch)
        if cat.startswith("L") or cat == "Nd" or ch in ALLOWED_SYMBOLS:
            combining_run = 0
        elif cat.startswith("M"):          
            combining_run += 1
            if combining_run > 2:
                raise ValueError("Too many accent marks on one character.")
        else:
            raise ValueError("Usernames can only contain letters, numbers, and _ . -")

    return None

def _normalize_username(username: str) -> str:
    s = unicodedata.normalize("NFKC", username.strip())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")

def _canonical_username(username: str) -> str:
    return _normalize_username(username).casefold()

def _check_display_chars(username: str) -> None:
    for ch in unicodedata.normalize("NFKC", username.strip()):
        if unicodedata.category(ch) == "Cf" and ch not in ALLOWED_CHAR_FORMATS:
            raise ValueError("Username contains invisible or formatting characters.")

    return None


def _sanitize_pfp(raw: bytes) -> bytes:
    """
    
    """

    with Image.open(io.BytesIO(raw)) as img:
        if img.format not in ACCEPTED_IMAGE_TYPES:
            raise ValueError("unsupported format")

        if img.width * img.height > 25_000_000:
            raise ValueError("image is too large")

        img = ImageOps.exif_transpose(img).convert("RGBA")
        img = ImageOps.fit(img, (512, 512), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, "WEBP", quality = 85)

        return out.getvalue()

async def deposit_pfp(pfp: bytes, supabase: AsyncClient, user_id: str) -> tuple[str, str]:
    """
    
    """

    path = f"{user_id}/{str(uuid.uuid4())}.webp"

    try:
        await supabase.storage.from_("profile_pictures").upload(
            path, pfp, {"content-type": "image/webp", "upsert": "false"}
        )
    except StorageException:
        logger.exception("Profile picture upload failed.")
        raise HTTPException(502, "Could not save profile picture. Try again.")

    url = await supabase.storage.from_("profile_pictures").get_public_url(path)

    return url, path

async def moderate_pfp(file: bytes, openai: AsyncOpenAI) -> None:
    """
    
    """

    b64 = base64.b64encode(file).decode("ascii")

    response = await openai.moderations.create(
        model = "omni-moderation-latest",
        input = [
            {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{b64}"}},
        ],
    )

    if response.results[0].flagged:
        raise ValueError("This profile picture is not allowed. Please upload another.")

    return None

async def process_pfp(file: UploadFile, supabase: AsyncClient, user_id: str, openai: AsyncOpenAI) -> tuple[str, str]:
    """
    
    """

    raw = await file.read(MAX_IMAGE_SIZE + 1)

    if len(raw) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code = 413, detail = "Image size is too large")

    try:
        pfp = await run_in_threadpool(_sanitize_pfp, raw)
    except (ValueError, UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise HTTPException(422, "Invalid image")

    try:
        await moderate_pfp(pfp, openai)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    pfp_url, pfp_path = await deposit_pfp(pfp, supabase, user_id)

    return pfp_url, pfp_path


async def copy_pfp(source_path: str, supabase: AsyncClient, user_id: str) -> tuple[str, str]:
    """
    Copies an already moderated picture to a new file, so another profile can use it without
    moderating it again. Each profile gets its own file, so replacing one profile's picture
    (which deletes its old file) never breaks another's.
    """

    path = f"{user_id}/{str(uuid.uuid4())}.webp"

    try:
        await supabase.storage.from_("profile_pictures").copy(source_path, path)
    except StorageException:
        logger.exception("Profile picture copy failed.")
        raise HTTPException(502, "Could not save profile picture. Try again.")

    url = await supabase.storage.from_("profile_pictures").get_public_url(path)

    return url, path

async def _remove_pfps(paths: list[str], supabase: AsyncClient) -> None:
    """
    Best-effort cleanup of pictures uploaded for a request that then failed.
    """

    if not paths:
        return None

    try:
        await supabase.storage.from_("profile_pictures").remove(paths)
    except Exception:
        logger.exception("Failed to clean up profile pictures %s", paths)

    return None

def _check_picture_files(profiles: list[ProfileIn], files: dict[str, UploadFile | None], field: str) -> None:
    """
    Every profile set to "upload" has a file, and no file is sent that nothing will use.
    `field` formats the form field name for errors, e.g. "profile_picture_{type}".
    """

    types = {p.type for p in profiles}

    for profile in profiles:
        file = files.get(profile.type)

        if profile.picture == "upload" and file is None:
            raise _field_error(("body", field.format(type=profile.type)), "Upload a picture or choose another picture option.")

        if profile.picture != "upload" and file is not None:
            raise _field_error(("body", field.format(type=profile.type)), "A picture was sent but this profile isn't set to upload one.")

    for profile_type, file in files.items():
        if file is not None and profile_type not in types:
            raise _field_error(("body", field.format(type=profile_type)), "A picture was sent for a profile that isn't being created.")

    return None

async def validate_username(username: str, openai: AsyncOpenAI, loc: tuple) -> None:
    """
    The format and moderation rules for a new username. Doesn't touch the database, so it can
    run (including the OpenAI call) without holding a connection.
    """

    normalized = _normalize_username(username)
    split = _split_normalized(normalized)
    canonical_username = _canonical_username(username)

    try:
        _check_user_restrictions(canonical_username)
        _check_display_chars(username)
        profanity_moderation(normalized, split)
        await openai_username_moderation(normalized, split, openai)
    except ValueError as e:
        raise _field_error(loc, str(e))

    return None

async def check_username_available(username: str, profile_type: ProfileType, db: AsyncSession, loc: tuple) -> None:
    """
    409 if another profile of the same type has this name. A player and a host can share one.
    The unique constraint is the real guard; this gives a friendly error before any uploads.
    """

    result = await db.execute(
        select(Profiles.id)
        .where(Profiles.type == profile_type)
        .where(Profiles.canonical_username == _canonical_username(username))
    )

    if result.first() is not None:
        raise HTTPException(status_code = 409, detail=f"That {profile_type} username is taken.")

    return None

async def check_valid_username(username: str, profile_type: ProfileType, db: AsyncSession, openai: AsyncOpenAI, loc: tuple) -> None:
    """
    Full check used by the sign up form's live username field.
    """

    await validate_username(username, openai, loc)
    await check_username_available(username, profile_type, db, loc)

    return None

async def handle_registration(
        payload: RegistrationIn,
        files: dict[str, UploadFile | None],
        email: str,
        user_id: str,
        db: AsyncSession,
        supabase: AsyncClient,
        openai: AsyncOpenAI
    ) -> RegistrationOut:
    """
    Creates the account's users row and one profile per entry, then points the account at its
    chosen default. Database checks run first and the connection is released before the slow
    outside calls (username moderation, picture moderation and uploads); everything is then
    written in one transaction, and uploaded pictures are removed if that fails.
    """

    _check_picture_files(payload.profiles, files, "profile_picture_{type}")

    existing = await db.execute(select(UsersParent.id).where(UsersParent.id == user_id))

    if existing.first() is not None:
        raise HTTPException(status_code=409, detail="Your profile is already set up.")

    for i, profile in enumerate(payload.profiles):
        await check_username_available(profile.username, profile.type, db, ("body", "profiles", i, "username"))

    # Release the connection before the outside calls below.
    await db.rollback()

    for i, profile in enumerate(payload.profiles):
        await validate_username(profile.username, openai, ("body", "profiles", i, "username"))

    urls, paths = await _prepare_pictures(payload, files, supabase, user_id, openai)

    try:
        user = UsersParent(id = user_id, email = email)
        db.add(user)
        await db.flush()

        rows = [_build_profile(p, user_id, urls.get(p.type)) for p in payload.profiles]
        await _insert_profiles(rows, db)

        user.default_profile_id = next(row.id for row, _ in rows if row.type == payload.default_profile)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        await _remove_pfps(paths, supabase)
        _raise_conflict(e, "Registration")
    except Exception:
        await db.rollback()
        await _remove_pfps(paths, supabase)
        raise

    return RegistrationOut(message = "Sign up form successfully submitted!")

async def _prepare_pictures(
        payload: RegistrationIn,
        files: dict[str, UploadFile | None],
        supabase: AsyncClient,
        user_id: str,
        openai: AsyncOpenAI
    ) -> tuple[dict[str, str], list[str]]:
    """
    Processes sign up pictures: the default profile's first (its copies depend on it), then the
    other uploads, then copies of the default. Returns each profile type's URL and every stored
    path, so the caller can clean up. If one fails, the ones already stored are removed.
    """

    order = sorted(payload.profiles, key = lambda p: (p.type != payload.default_profile, p.picture == "copy_default"))
    urls: dict[str, str] = {}
    paths: dict[str, str] = {}

    try:
        for profile in order:
            if profile.picture == "upload":
                urls[profile.type], paths[profile.type] = await process_pfp(files[profile.type], supabase, user_id, openai)
            elif profile.picture == "copy_default":
                urls[profile.type], paths[profile.type] = await copy_pfp(paths[payload.default_profile], supabase, user_id)
    except Exception:
        await _remove_pfps(list(paths.values()), supabase)
        raise

    return urls, list(paths.values())

async def _get_user(user_id: str, db: AsyncSession) -> UsersParent:
    """
    Loads the signed in user's users row, or raises a 404 if their profile doesn't exist.
    """

    result = await db.execute(select(UsersParent).where(UsersParent.id == user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="Profile not found.")

    return user

async def _get_own_profile(user_id: str, profile_type: ProfileType, db: AsyncSession) -> Profiles:
    """
    Loads one of the signed in user's profiles by type, or raises a 404 if they don't have one.
    """

    result = await db.execute(
        select(Profiles).where(Profiles.account_id == user_id).where(Profiles.type == profile_type)
    )
    profile = result.scalars().first()

    if not profile:
        raise HTTPException(status_code=404, detail=f"You don't have a {profile_type} profile.")

    return profile

def _summary(profile: Profiles) -> ProfileSummary:
    return ProfileSummary(
        type = profile.type,
        username = profile.username,
        public_id = profile.public_id,
        profile_picture = profile.profile_picture
    )

async def _account_profiles(account_id, db: AsyncSession) -> list[Profiles]:
    result = await db.execute(
        select(Profiles).where(Profiles.account_id == account_id).order_by(Profiles.created_at, Profiles.type)
    )

    return list(result.scalars().all())

async def _links(profile_id, db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(ProfileLinks).where(ProfileLinks.profile_id == profile_id))

    return {link.platform: link.handle for link in result.scalars().all()}

async def _locations(profile_id, db: AsyncSession) -> list[VenueOut]:
    result = await db.execute(select(Venues).where(Venues.profile_id == profile_id).order_by(Venues.venue_name))

    return [VenueOut(name = v.venue_name, location = v.location) for v in result.scalars().all()]

def _without_other(games: list[str] | None) -> list[str]:
    return [g for g in (games or []) if g != "Other"]

async def get_me(user_id: str, db: AsyncSession) -> Me:
    """
    The navbar's data: the default profile plus every profile on the account for the view
    switcher. 404 if sign up hasn't been completed.
    """

    result = await db.execute(select(UsersParent).where(UsersParent.id == user_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="Profile not completed.")

    profiles = await _account_profiles(user_id, db)
    default = next((_summary(p) for p in profiles if p.id == user.default_profile_id), None)

    return Me(verified = user.verified, default_profile = default, profiles = [_summary(p) for p in profiles])

# Event tables a host or venue's events can be in, and the site section each one lives under.
_EVENT_TABLES = (
    ("lan", LanEvents, LanEvents.event_id),
    ("league", LeagueParentEvents, LeagueParentEvents.id),
    ("league", LeagueEvents, LeagueEvents.event_id),
    ("wager", WagerEvents, WagerEvents.event_id),
    ("h2h", XpEvents, XpEvents.event_id),
)

async def get_profile_events(public_id: str, db: AsyncSession) -> list[ProfileEvent]:
    """
    The public events owned by a host or venue profile's account (events.user_id), newest
    listing first within each kind. Pending events and archived LANs aren't public, so they're
    left out. Players don't own events, so theirs is always empty.
    """

    profile = (await db.execute(select(Profiles).where(Profiles.public_id == public_id))).scalars().first()
    if not profile:
        raise HTTPException(status_code=404, detail="User does not exist.")
    if profile.type == "player":
        return []

    events: list[ProfileEvent] = []
    for kind, table, event_id in _EVENT_TABLES:
        query = (
            select(table)
            .join(EventFormParent, EventFormParent.id == event_id)
            .where(EventFormParent.user_id == profile.account_id, table.status != "pending")
            .order_by(table.updated_at.desc())
        )
        if table is LanEvents:
            query = query.where(LanEvents.archived.is_(False))

        for row in (await db.execute(query)).scalars():
            events.append(ProfileEvent(
                kind = kind,
                name = row.name,
                game = row.game,
                path = row.path,
                date = getattr(row, "date", None),
                location = getattr(row, "location", None),
                verified = bool(row.verified)
            ))

    return events

async def get_profile(public_id: str, db: AsyncSession) -> Profile:
    """
    A public profile page by its public_id. Only this profile's details are loaded; the
    account's other profiles come from the profiles table alone, for the view switcher.
    """

    result = await db.execute(
        select(Profiles, UsersParent.verified)
        .join(UsersParent, UsersParent.id == Profiles.account_id)
        .where(Profiles.public_id == public_id)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="User does not exist.")

    profile, verified = row

    if profile.type == "player":
        player = (await db.execute(select(Players).where(Players.profile_id == profile.id))).scalars().first()
        details = PlayerPublic(
            games = _without_other(player.games if player else None),
            battlenet = player.battlenet if player else None,
            activision = player.activision if player else None,
            steam = player.steam if player else None,
            riot = player.riot if player else None
        )
    elif profile.type == "host":
        host = (await db.execute(select(Hosts).where(Hosts.profile_id == profile.id))).scalars().one()
        details = HostPublic(
            organization = host.organization,
            hosted_games = _without_other(host.games),
            event_types = host.event_types or []
        )
    else:
        details = VenuePublic(locations = await _locations(profile.id, db))

    others = [_summary(p) for p in await _account_profiles(profile.account_id, db) if p.id != profile.id]

    return Profile(
        public_id = profile.public_id,
        type = profile.type,
        username = profile.username,
        verified = verified,
        bio = profile.bio,
        profile_picture = profile.profile_picture,
        other_roles = profile.other_roles or set(),
        other_role_detail = profile.other_role_detail,
        links = await _links(profile.id, db),
        details = details,
        others = others
    )

async def get_profile_settings(user_id: str, profile_type: ProfileType, db: AsyncSession) -> ProfileSettingsOut:
    """
    Everything the owner can edit on one of their profiles.
    """

    result = await db.execute(
        select(Profiles, UsersParent.verified, UsersParent.default_profile_id)
        .join(UsersParent, UsersParent.id == Profiles.account_id)
        .where(Profiles.account_id == user_id)
        .where(Profiles.type == profile_type)
    )
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail=f"You don't have a {profile_type} profile.")

    profile, verified, default_profile_id = row

    if profile_type == "player":
        player = (await db.execute(select(Players).where(Players.profile_id == profile.id))).scalars().one()
        details = PlayerSettings(
            first_name = player.first_name,
            last_name = player.last_name,
            phone_number = player.phone_number,
            gender = player.gender,
            date_of_birth = player.date_of_birth,
            country = player.country,
            interests = player.games or [],
            other_games = player.other_games,
            battlenet = player.battlenet,
            activision = player.activision,
            steam = player.steam,
            riot = player.riot
        )
    elif profile_type == "host":
        host = (await db.execute(select(Hosts).where(Hosts.profile_id == profile.id))).scalars().one()
        details = HostSettings(
            organization = host.organization,
            hosted_games = host.games or [],
            other_hosted_games = host.other_games,
            host_country = host.host_country,
            event_types = host.event_types or []
        )
    else:
        details = VenueSettings(locations = await _locations(profile.id, db))

    return ProfileSettingsOut(
        public_id = profile.public_id,
        type = profile.type,
        username = profile.username,
        verified = verified,
        is_default = profile.id == default_profile_id,
        bio = profile.bio,
        profile_picture = profile.profile_picture,
        bracket_hosting = profile.bracket_hosting,
        other_roles = profile.other_roles or set(),
        other_role_detail = profile.other_role_detail,
        links = await _links(profile.id, db),
        details = details
    )

async def add_profile(
    user_id: str,
    profile: ProfileIn,
    file: UploadFile | None,
    db: AsyncSession,
    supabase: AsyncClient,
    openai: AsyncOpenAI
) -> ProfileSettingsOut:
    """
    Adds a new profile type to an existing account (e.g. a player who starts hosting). It gets
    its own username, so nothing about the account's other profiles changes. Same order as sign
    up: database checks, release the connection, outside calls, then one write.
    """

    _check_picture_files([profile], {profile.type: file}, "profile_picture")

    user = await _get_user(user_id, db)

    existing = await db.execute(
        select(Profiles.id).where(Profiles.account_id == user_id).where(Profiles.type == profile.type)
    )

    if existing.first() is not None:
        raise HTTPException(status_code=409, detail=f"You already have a {profile.type} profile.")

    await check_username_available(profile.username, profile.type, db, ("body", "username"))

    default_picture = None

    if profile.picture == "copy_default":
        result = await db.execute(select(Profiles.profile_picture).where(Profiles.id == user.default_profile_id))
        default_picture = result.scalar_one_or_none()

        if not default_picture or not _storage_path(default_picture):
            raise _field_error(("body", "picture"), "Your default profile has no picture to copy.")

    # Release the connection before the outside calls below.
    await db.rollback()

    await validate_username(profile.username, openai, ("body", "username"))

    pfp_url = None
    pfp_path = None

    if profile.picture == "upload":
        pfp_url, pfp_path = await process_pfp(file, supabase, user_id, openai)
    elif profile.picture == "copy_default":
        pfp_url, pfp_path = await copy_pfp(_storage_path(default_picture), supabase, user_id)

    paths = [pfp_path] if pfp_path else []

    try:
        await _insert_profiles([_build_profile(profile, user_id, pfp_url)], db)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        await _remove_pfps(paths, supabase)
        _raise_conflict(e, "Adding the profile", profile.type)
    except Exception:
        await db.rollback()
        await _remove_pfps(paths, supabase)
        raise

    return await get_profile_settings(user_id, profile.type, db)

async def _apply_profile_changes(
    user_id: str,
    profile_id,
    profile_type: ProfileType,
    payload: dict,
    links: dict | None,
    details_values: dict | None,
    db: AsyncSession
) -> None:
    """
    The writes behind update_profile, without committing.
    """

    if payload:
        await db.execute(update(Profiles).where(Profiles.id == profile_id).values(**payload))

    if links is not None:
        await db.execute(delete(ProfileLinks).where(ProfileLinks.profile_id == profile_id))

        if links:
            await db.execute(
                insert(ProfileLinks),
                [
                    {
                        "profile_id": profile_id,
                        "account_id": user_id,
                        "platform": platform,
                        "handle": handle,
                        "canonical_handle": _canonical_handle(handle)
                    }
                    for platform, handle in links.items()
                ]
            )

    if details_values:
        if profile_type == "venue":
            # Keyed by name since (profile_id, venue_name) is the primary key.
            unique_locations = {v["name"]: v["location"] for v in details_values["locations"]}

            await db.execute(delete(Venues).where(Venues.profile_id == profile_id))
            await db.execute(
                insert(Venues),
                [{"profile_id": profile_id, "venue_name": name, "location": location} for name, location in unique_locations.items()]
            )
        else:
            table = Players if profile_type == "player" else Hosts
            await db.execute(update(table).where(table.profile_id == profile_id).values(**details_values))

    return None

async def update_profile(user_id: str, profile_type: ProfileType, changes: ProfileUpdate, db: AsyncSession) -> ProfileSettingsOut:
    """
    Partially updates one of the user's profiles. Links and venue locations, when sent, are
    replaced as a whole set. Everything is written in one transaction.
    """

    payload = changes.model_dump(exclude_unset=True)
    links = payload.pop("links", None)
    details = payload.pop("details", None)
    details_values = None

    try:
        check_link_types(profile_type, links)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if details is not None:
        try:
            details_values = DETAIL_UPDATES[profile_type].model_validate(details).model_dump(exclude_unset=True, by_alias=True)
        except ValidationError as e:
            raise HTTPException(
                status_code=422,
                detail=[{**err, "loc": ["body", "details", *err["loc"]]} for err in e.errors(include_context=False, include_url=False)]
            )

    profile = await _get_own_profile(user_id, profile_type, db)
    profile_id = profile.id

    # Constraints are checked as each statement runs, so the writes sit inside the try too.
    try:
        await _apply_profile_changes(user_id, profile_id, profile_type, payload, links, details_values, db)
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        _raise_conflict(e, "Profile update", profile_type)

    return await get_profile_settings(user_id, profile_type, db)

async def update_profile_picture(
    user_id: str,
    profile_type: ProfileType,
    file: UploadFile,
    db: AsyncSession,
    supabase: AsyncClient,
    openai: AsyncOpenAI
) -> ProfileSettingsOut:
    """
    Replaces one profile's picture. The connection is released during moderation and upload.
    The new file is saved before the old one is removed, so a failure part way through never
    leaves the profile without a picture.
    """

    profile = await _get_own_profile(user_id, profile_type, db)
    profile_id = profile.id
    old_url = profile.profile_picture

    # Release the connection before the outside calls below.
    await db.rollback()

    pfp_url, pfp_path = await process_pfp(file, supabase, user_id, openai)

    try:
        await db.execute(update(Profiles).where(Profiles.id == profile_id).values(profile_picture = pfp_url))
        await db.commit()
    except Exception:
        await db.rollback()
        await _remove_pfps([pfp_path], supabase)
        raise

    old_path = _storage_path(old_url) if old_url else None

    if old_path and old_path != pfp_path:
        try:
            await supabase.storage.from_("profile_pictures").remove([old_path])
        except Exception:
            logger.exception("Failed to remove old profile picture %s", old_path)

    return await get_profile_settings(user_id, profile_type, db)

async def change_username(user_id: str, profile_type: ProfileType, username: str, db: AsyncSession, openai: AsyncOpenAI) -> ProfileSettingsOut:
    """
    Renames one of the user's profiles; the others keep their names. A change that only differs
    in casing skips the length and moderation rules the name already passed, and only rechecks
    for invisible characters. Otherwise the full sign up rules apply, with the connection
    released during the moderation call.
    """

    profile = await _get_own_profile(user_id, profile_type, db)
    profile_id = profile.id
    canonical = _canonical_username(username)
    loc = ("body", "username")

    if canonical == profile.canonical_username:
        try:
            _check_display_chars(username)
        except ValueError as e:
            raise _field_error(loc, str(e))
    else:
        await check_username_available(username, profile_type, db, loc)

        # Release the connection before the moderation call.
        await db.rollback()

        await validate_username(username, openai, loc)

    try:
        await db.execute(
            update(Profiles).where(Profiles.id == profile_id).values(username = username.strip(), canonical_username = canonical)
        )
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        _raise_conflict(e, "Username change", profile_type)

    return await get_profile_settings(user_id, profile_type, db)

async def set_default_profile(user_id: str, profile_type: ProfileType, db: AsyncSession) -> Me:
    """
    Makes one of the user's profiles their default (navbar picture and name, first view).
    """

    profile = await _get_own_profile(user_id, profile_type, db)

    await db.execute(update(UsersParent).where(UsersParent.id == user_id).values(default_profile_id = profile.id))
    await db.commit()

    return await get_me(user_id, db)

async def delete_profile(user_id: str, profile_type: ProfileType, db: AsyncSession, supabase: AsyncClient) -> Me:
    """
    Deletes one of the user's profiles. Removing the profiles row cascades to its players/hosts/
    venues details, links and comp site accounts. The account's only profile can't be deleted
    (409, delete the account instead). If it was the default, the oldest remaining profile
    becomes the default first, since the default_profile FK won't let it point at a deleted row.
    """

    # Lock the account row so two deletes at once can't both pass the "not the last profile"
    # check and leave the account with no profiles.
    result = await db.execute(select(UsersParent).where(UsersParent.id == user_id).with_for_update())
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="Profile not found.")

    profile = await _get_own_profile(user_id, profile_type, db)
    remaining = [p for p in await _account_profiles(user_id, db) if p.id != profile.id]

    if not remaining:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="You can't delete your only profile. Delete your account instead."
        )

    if user.default_profile_id == profile.id:
        # _account_profiles is ordered oldest first.
        await db.execute(
            update(UsersParent).where(UsersParent.id == user_id).values(default_profile_id = remaining[0].id)
        )

    pfp_path = _storage_path(profile.profile_picture) if profile.profile_picture else None

    await db.execute(delete(Profiles).where(Profiles.id == profile.id).where(Profiles.account_id == user_id))
    await db.commit()

    # After the commit, so a failed delete never leaves a profile without its picture.
    await _remove_pfps([pfp_path] if pfp_path else [], supabase)

    return await get_me(user_id, db)

def _storage_path(pfp_url: str) -> str | None:
    """
    Turns a profile_pictures public URL back into its storage path (<user_id>/<uuid>.webp).
    """

    marker = "/profile_pictures/"

    if marker not in pfp_url:
        return None

    return pfp_url.split(marker, 1)[1].split("?", 1)[0]


async def delete_account(user_id: str, db: AsyncSession, supabase: AsyncClient) -> None:
    """
    Deletes the user's app data, then their Supabase auth user, then their stored files.
    The FK rules on users.id cascade to the user's own rows and null out shared event data.
    App data goes first so a failure never leaves a public profile nobody can log into.
    Every step is safe to retry: a missing users row just deletes nothing.
    """

    await db.execute(delete(UsersParent).where(UsersParent.id == user_id))
    await db.commit()

    try:
        await supabase.auth.admin.delete_user(user_id)
    except AuthApiError as e:
        # 404 means an earlier attempt already removed the auth user.
        if e.status != 404:
            logger.exception("Failed to delete auth user %s", user_id)
            raise HTTPException(status_code=500, detail="Could not finish deleting your account. Please try again.")
    except Exception:
        logger.exception("Failed to delete auth user %s", user_id)
        raise HTTPException(status_code=500, detail="Could not finish deleting your account. Please try again.")

    try:
        files = await supabase.storage.from_("profile_pictures").list(user_id)
        paths = [f"{user_id}/{f['name']}" for f in files]

        if paths:
            await supabase.storage.from_("profile_pictures").remove(paths)
    except Exception:
        logger.exception("Failed to remove stored files for deleted user %s", user_id)

    return None
