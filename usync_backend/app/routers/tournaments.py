from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

from app.schemas.tournaments import CodTournamentOut

from app.services.tournaments import get_tournaments

router = APIRouter(prefix="/tournaments", tags=["Tournaments"])

@router.get("/cod", response_model=list[CodTournamentOut])
async def tournaments(db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to get all tournaments related to the provided game.

    ::param db the Async database session
    """

    return await get_tournaments("cod", db)
