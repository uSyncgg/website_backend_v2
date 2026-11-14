import uuid
import base64
import logging

from fastapi import HTTPException, UploadFile
from supabase import AsyncClient
from storage3.utils import StorageException
from openai import AsyncOpenAI
from app.services.images import read_image_upload

# Profile and team pictures: moderated, re-encoded and stored in the profile_pictures bucket
# under <user_id>/<uuid>.webp. Shared by app.services.users and app.services.teams (it used to
# live in users, which forced teams to import it inside functions to dodge an import cycle).

logger = logging.getLogger(__name__)

PFP_SIZE = 512
PFP_BUCKET = "profile_pictures"

async def deposit_pfp(pfp: bytes, supabase: AsyncClient, user_id: str) -> tuple[str, str]:
    """
    Uploads an already processed picture and returns its (public url, storage path).
    """

    path = f"{user_id}/{str(uuid.uuid4())}.webp"

    try:
        await supabase.storage.from_(PFP_BUCKET).upload(
            path, pfp, {"content-type": "image/webp", "upsert": "false"}
        )
    except StorageException:
        logger.exception("Profile picture upload failed.")
        raise HTTPException(502, "Could not save profile picture. Try again.")

    url = await supabase.storage.from_(PFP_BUCKET).get_public_url(path)

    return url, path

async def moderate_pfp(file: bytes, openai: AsyncOpenAI) -> None:
    """
    Raises ValueError when OpenAI's moderation flags the image.
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
    Reads, moderates and uploads a picture. Returns its (public url, storage path).
    """

    pfp = await read_image_upload(file, square = PFP_SIZE)

    try:
        await moderate_pfp(pfp, openai)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return await deposit_pfp(pfp, supabase, user_id)

async def copy_pfp(source_path: str, supabase: AsyncClient, user_id: str) -> tuple[str, str]:
    """
    Copies an already moderated picture to a new file, so another profile can use it without
    moderating it again. Each profile gets its own file, so replacing one profile's picture
    (which deletes its old file) never breaks another's.
    """

    path = f"{user_id}/{str(uuid.uuid4())}.webp"

    try:
        await supabase.storage.from_(PFP_BUCKET).copy(source_path, path)
    except StorageException:
        logger.exception("Profile picture copy failed.")
        raise HTTPException(502, "Could not save profile picture. Try again.")

    url = await supabase.storage.from_(PFP_BUCKET).get_public_url(path)

    return url, path

async def remove_pfps(paths: list[str], supabase: AsyncClient) -> None:
    """
    Best-effort cleanup of pictures uploaded for a request that then failed.
    """

    if not paths:
        return None

    try:
        await supabase.storage.from_(PFP_BUCKET).remove(paths)
    except Exception:
        logger.exception("Failed to clean up profile pictures %s", paths)

    return None

def storage_path(pfp_url: str) -> str | None:
    """
    Turns a profile_pictures public URL back into its storage path (<user_id>/<uuid>.webp).
    """

    marker = f"/{PFP_BUCKET}/"

    if marker not in pfp_url:
        return None

    return pfp_url.split(marker, 1)[1].split("?", 1)[0]
