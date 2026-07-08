from fastapi import APIRouter

router = APIRouter(tags=["Healthchecks"])

@router.get("/health")
async def healthcheck():
    """
    Healthcheck endpoint for CRON job to keep backend alive.
    """

    return {"status", "ok"}