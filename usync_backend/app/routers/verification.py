from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db
from app.schemas.verification import VerificationIn, VerificationOut

router = APIRouter(prefix="/verification", tags=["Verification"])

@router.post("/verification", response_model=VerificationOut)
async def verification(payload: VerificationIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to update a users verification status.
    """

    # Need return function
