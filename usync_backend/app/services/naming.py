import re
import secrets
import string
import unicodedata

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI
from better_profanity import profanity
from app.models.users import Profiles
from app.schemas.users import ProfileSummary

# Names and ids shared by users, teams and bracket events: public ids for URLs, username
# normalisation, profanity / moderation checks for anything a user names, and the small profile
# lookups several services need. One copy of each, imported wherever it's used.

# Public ids go in URLs: random (not sequential, so nothing can be enumerated) and short.
PUBLIC_ID_ALPHABET = string.ascii_lowercase + string.digits
PUBLIC_ID_LENGTH = 10

_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")

profanity.load_censor_words()

def new_public_id(prefix: str = "", length: int = PUBLIC_ID_LENGTH) -> str:
    return prefix + "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(length))

# ---- usernames ---------------------------------------------------------------

def normalize_username(username: str) -> str:
    """
    NFKC, trimmed, with invisible formatting characters removed.
    """

    s = unicodedata.normalize("NFKC", username.strip())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")

def canonical_username(username: str) -> str:
    return normalize_username(username).casefold()

def split_words(normalized: str) -> str:
    """
    "xXKillerXx_99" -> "x Xkiller Xx 99": catches words hidden by casing, digits and symbols.
    """

    s = re.sub(r"[_./-]+", " ", normalized)
    return _SPLIT_RE.sub(" ", s)

# ---- moderation --------------------------------------------------------------

def profanity_moderation(normalized: str, split: str, what: str = "username") -> None:
    """
    Raises ValueError when the word list matches. Local and instant.
    """

    for candidate in (normalized, split):
        if profanity.contains_profanity(candidate):
            raise ValueError(f"This {what} is not allowed. Please choose another.")

    return None

async def openai_text_moderation(texts: list[str], openai: AsyncOpenAI, what: str = "username") -> None:
    """
    Raises ValueError when OpenAI's moderation flags any of the texts. A network call: run it
    without holding a database connection where possible.
    """

    response = await openai.moderations.create(model = "omni-moderation-latest", input = texts)

    if any(r.flagged for r in response.results):
        raise ValueError(f"This {what} is not allowed. Please choose another.")

    return None

async def moderate_name(name: str, openai: AsyncOpenAI | None, what: str, field: str = "name") -> None:
    """
    The check for anything a user names that others will see (team names, event names): the
    profanity list, then OpenAI's moderation. Raises a 422 pointing at `field`.
    """

    normalized = normalize_username(name)
    split = split_words(normalized)

    try:
        profanity_moderation(normalized, split, what)
        if openai is not None:
            await openai_text_moderation([normalized, split], openai, what)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=[{"loc": ["body", field], "msg": str(e), "type": "value_error"}])

    return None

# ---- profiles ----------------------------------------------------------------

def profile_summary(profile: Profiles) -> ProfileSummary:
    return ProfileSummary(
        type = profile.type,
        username = profile.username,
        public_id = profile.public_id,
        profile_picture = profile.profile_picture
    )

async def get_own_profile(user_id: str, profile_type: str, db: AsyncSession) -> Profiles:
    """
    One of the signed in account's profiles by type, or a 404 if it doesn't have one.
    """

    profile = (await db.execute(
        select(Profiles).where(Profiles.account_id == user_id).where(Profiles.type == profile_type)
    )).scalars().first()

    if not profile:
        raise HTTPException(status_code=404, detail=f"You don't have {'an org' if profile_type == 'org' else 'a ' + profile_type} profile.")

    return profile

async def account_profile_ids(user_id: str | None, db: AsyncSession) -> set:
    """
    Every profile id belonging to the signed in account (the identity comes from the verified
    JWT's sub, never from the request).
    """

    if not user_id:
        return set()

    return set((await db.execute(select(Profiles.id).where(Profiles.account_id == user_id))).scalars().all())
