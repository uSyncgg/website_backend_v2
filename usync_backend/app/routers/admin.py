from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_readonly_db, require_admin
from app.core.limiter import limiter, MODERATE
from app.schemas.admin import AdminAnalyticsOut, AdminEventsOut
from app.services.admin import get_admin_analytics, get_admin_events

# Admin dashboard reads. GET only, admins only (ADMIN_EMAILS), and the session is READ ONLY.
router = APIRouter(prefix="/admin", tags=["Admin"])

@router.get("/analytics", response_model=AdminAnalyticsOut)
@limiter.limit(MODERATE)
async def adminAnalytics(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Account totals, daily sign-ups, and games picked at sign-up.
    """

    return await get_admin_analytics(db)

@router.get("/events", response_model=AdminEventsOut)
@limiter.limit(MODERATE)
async def adminEvents(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Every event listing, most recently updated first.
    """

    return await get_admin_events(db)
