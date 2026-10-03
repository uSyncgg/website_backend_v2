import uuid
import logging
import io
import base64
import unicodedata
import re

from sqlalchemy import select, insert, update, Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from app.schemas.users import RegistrationIn, RegistrationOut, PlayerDetails, HostDetails
from app.models.users import Players, Hosts, UsersParent, PlayerCompSiteAccounts, Venues
from PIL import Image, ImageOps, UnidentifiedImageError
from supabase import AsyncClient
from storage3.utils import StorageException
from openai import AsyncOpenAI
from better_profanity import profanity

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 5 * 1024 * 1024
ACCEPTED_IMAGE_TYPES = {"JPEG", "WEBP", "PNG"}
ALLOWED_SYMBOLS = {"_", ".", "-"}
ALLOWED_CHAR_FORMATS = {"\u200c", "\u200d"}
MIN_USER_LEN = 4
MAX_USER_LEN = 25
_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")

profanity.load_censor_words()

def _build_player(player_data: PlayerDetails, user_id: str) -> Players:
    """
    
    """

    return Players(
        id = user_id,
        user_id = user_id,
        first_name = player_data.first_name,
        last_name = player_data.last_name,
        phone_number = player_data.phone_number,
        gender = player_data.gender,
        date_of_birth = player_data.date_of_birth,
        country = player_data.country,
        games = player_data.interests,
        other_games = player_data.other_games,
        battlenet = player_data.battlenet,
        activision = player_data.activision,
        steam = player_data.steam,
        riot = player_data.riot
    )

def _build_host(host_data: HostDetails, user_id: str) -> Hosts:
    """
    
    """

    return Hosts (
        id = user_id,
        user_id = user_id,
        games = host_data.hosted_games,
        other_games = host_data.other_hosted_games,
        organization = host_data.organization,
        host_country = host_data.host_country,
        event_types = host_data.event_types
    )

def _build_venue(payload: RegistrationIn, user_id: str) -> Sequence[Venues]:
    """
    
    """

    venues: Sequence[Venues] = []

    for v in payload.venues:
        if v.get("name") and v.get("location"):
            venue = Venues(
                id = user_id,
                venue_name = v.get("name"),
                location = v.get("location")
            )

            venues.append(venue)

    return venues

def _build_user(payload: RegistrationIn, email: str, pfp_url: str, user_id: str) -> UsersParent:
    """
    
    """

    return UsersParent (
        id = user_id,
        username = payload.username.strip(),
        canonical_username = _canonical_username(payload.username),
        email = email,
        is_player = True if "player" in payload.signup_path else False,
        is_host = True if "host" in payload.signup_path else False,
        twitch = payload.twitch,
        twitter = payload.twitter,
        youtube = payload.youtube,
        kick = payload.kick,
        discord = payload.discord,
        instagram = payload.instagram,
        profile_picture = pfp_url if pfp_url != "" else None,
        bio = payload.bio,
        bracket_hosting = True if payload.bracket_hosting is None or payload.bracket_hosting == "yes" else False,
        other_roles = payload.other_roles,
        other_role_detail = payload.other_role_detail
    )

async def deposit_models(models: list[UsersParent | Hosts | Players | Venues], db: AsyncSession) -> None:
    """
    
    """
    db.add(models[0])

    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        if "ix_users_username_player_unique" in str(e.orig):
            raise HTTPException(status_code=409, detail="Player username is already taken. Enter a new one.")
        if "ix_users_username_host_unique" in str(e.orig):
            raise HTTPException(status_code=409, detail="Host username is already taken. Enter a new one.")
        logger.exception("Registration failed with unexpected integrity error.")
        raise HTTPException(status_code = 409, detail = "Registration conflicts with existing data.")

    db.add_all(models[1:])

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.exception("Registration failed with unexpected integrity error.")
        raise HTTPException(status_code = 409, detail = "Registration conflicts with existing data.")

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

async def handle_registration(
        payload: RegistrationIn, 
        email: str, 
        pfp_url: str, 
        pfp_path: str, 
        user_id: str, 
        db: AsyncSession, 
        supabase: AsyncClient
    ) -> RegistrationOut:
    """
    
    """

    user_model = _build_user(payload, email, pfp_url, user_id)
    models = [user_model]

    if "player" in payload.signup_path:
        models.append(_build_player(payload.player, user_id))

    if "host" in payload.signup_path:
        models.append(_build_host(payload.host, user_id))

    if payload.venues:
        models.extend(_build_venue(payload, user_id))

    try:
        await deposit_models(models, db)
    except Exception:
        if pfp_path != "":
            try:
                await supabase.storage.from_("profile_pictures").remove([pfp_path])
            except Exception:
                logger.exception("Failed to clean up profile picture %s", pfp_path)

        raise

    ### NOTE: below are things for comp sites - not adding just yet
    # cmg = player_data.cmg,
    # gankster = player_data.gankster,
    # faceit = player_data.faceit,
    # battlefly = player_data.battlefly


    return RegistrationOut(message = "Sign up form successfully submitted!")

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

    await moderate_pfp(pfp, openai)

    pfp_url, pfp_path = await deposit_pfp(pfp, supabase, user_id)

    return pfp_url, pfp_path

async def check_valid_username(
        username: str, 
        db: AsyncSession, 
        openai: AsyncOpenAI, 
        loc: tuple[str, str], 
        player: bool | None = None, 
        host: bool | None = None
    ) -> None:
    """
    
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
        raise HTTPException(status_code=422, detail=[{"loc": list(loc), "msg": str(e), "type": "value_error"}])        

    stmt = (
        select(UsersParent)
        .where(UsersParent.canonical_username == canonical_username)
    )

    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user:
        return None

    if player:
        if player is True and user.is_player is True:
            raise HTTPException(status_code = 409, detail="Username is taken.")

    if host:
        if host is True and user.is_host is True:
            raise HTTPException(status_code = 409, detail="Username is taken.")

    return None
