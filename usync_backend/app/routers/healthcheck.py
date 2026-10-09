from fastapi import APIRouter
from app.core.limiter import limiter

router = APIRouter(tags=["Healthchecks"])

### NOTE: Need to fix how the healthchecks are done.
@router.get("/health")
@limiter.exempt
async def healthcheck():
    """
    Healthcheck endpoint for CRON job to keep backend alive.
    """

    return {"status", "ok"}