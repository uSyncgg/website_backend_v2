from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.tournaments import CodTournament
from collections.abc import Sequence

from datetime import datetime
from zoneinfo import ZoneInfo

GAME_MODELS = {
    "cod": CodTournament,
}

async def get_tournaments(game: str, db: AsyncSession) -> Sequence[CodTournament]:
    """
    Asynchronous function to get all tournaments related to a specific game.

    ::param game the string containing the game
    ::param db the Asynchronous database Session

    ::return a sequence containing all tournaments fetched and validated by the appropriate schema
    """

    model = GAME_MODELS.get(game)

    if model is None:
        return []
    
    now = datetime.now(tz = ZoneInfo("America/New_York"))

    stmt = (
        select(model)
        .where(model.starts_at >= now)
        .order_by(model.starts_at.asc())
    )
    
    result = await db.execute(stmt)
    return result.scalars().all()
