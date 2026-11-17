import io

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError

# Shared by every image upload (profile pictures, event claim proofs). The frontend mirrors
# these in utils/imageUpload.js.
MAX_IMAGE_SIZE = 5 * 1024 * 1024
ACCEPTED_IMAGE_TYPES = {"JPEG", "WEBP", "PNG"}
MAX_PIXELS = 25_000_000

def sanitize_image(raw: bytes, *, square: int | None = None, max_side: int | None = None) -> bytes:
    """
    Checks the bytes really are an accepted image and re-encodes them as WebP, which drops any
    metadata (EXIF location etc.). Raises ValueError / Pillow errors on anything invalid.

    ::param square crop to a centred square of this size (profile pictures)
    ::param max_side otherwise, shrink so the longest side is at most this, keeping the shape
    """

    with Image.open(io.BytesIO(raw)) as img:
        if img.format not in ACCEPTED_IMAGE_TYPES:
            raise ValueError("unsupported format")

        if img.width * img.height > MAX_PIXELS:
            raise ValueError("image is too large")

        img = ImageOps.exif_transpose(img).convert("RGBA")

        if square:
            img = ImageOps.fit(img, (square, square), Image.Resampling.LANCZOS)
        elif max_side:
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        img.save(out, "WEBP", quality = 85)

        return out.getvalue()

async def read_image_upload(file: UploadFile, **sanitize_options) -> bytes:
    """
    Reads an upload (refusing anything over MAX_IMAGE_SIZE without reading the rest) and
    sanitizes it off the event loop. 413 if too big, 422 if it isn't a valid image.
    """

    raw = await file.read(MAX_IMAGE_SIZE + 1)

    if len(raw) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code = 413, detail = "Image size is too large")

    try:
        return await run_in_threadpool(sanitize_image, raw, **sanitize_options)
    except (ValueError, UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise HTTPException(422, "Invalid image")
