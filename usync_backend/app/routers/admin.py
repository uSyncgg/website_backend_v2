from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_readonly_db, require_admin
from app.core.limiter import limiter, MODERATE
from app.schemas.admin import AdminAccountsOut, AdminAnalyticsOut, AdminEventsOut
from app.services.admin import get_admin_accounts, get_admin_analytics, get_admin_events

# Admin dashboard reads. GET only, admins only (the admins table), and the session is READ ONLY.
router = APIRouter(prefix="/admin", tags=["Admin"])

@router.get("/access", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(MODERATE)
async def adminAccess(request: Request, response: Response, claims: dict = Depends(require_admin)) -> None:
    """
    204 for admins, 403 for everyone else. The frontend calls this before showing the dashboard.
    """

@router.get("/analytics", response_model=AdminAnalyticsOut)
@limiter.limit(MODERATE)
async def adminAnalytics(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Account totals, daily sign-ups, and the games listed on profiles.
    """

    return await get_admin_analytics(db)

@router.get("/events", response_model=AdminEventsOut)
@limiter.limit(MODERATE)
async def adminEvents(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Every event listing, most recently updated first.
    """

    return await get_admin_events(db)

@router.get("/accounts", response_model=AdminAccountsOut)
@limiter.limit(MODERATE)
async def adminAccounts(request: Request, response: Response, claims: dict = Depends(require_admin), db: AsyncSession = Depends(get_readonly_db)):
    """
    Every account, including unfinished sign-ups, with roles, games and linked platforms.
    """

    return await get_admin_accounts(db)
