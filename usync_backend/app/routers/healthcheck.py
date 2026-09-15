from fastapi import APIRouter

router = APIRouter(tags=["Healthchecks"])

### NOTE: Need to fix how the healthchecks are done.
@router.get("/health")
async def healthcheck():
    """
    Healthcheck endpoint for CRON job to keep backend alive.
    """

    return {"status", "ok"}