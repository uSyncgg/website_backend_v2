from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db
from app.schemas.users import UpdateProfileIn, UpdateProfileOut, RegistrationIn, RegistrationOut

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/register", response_model=RegistrationOut)
async def registerUser(payload: RegistrationIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to register a user.
    """

    # Need return function

@router.post("/profile/update", response_model=UpdateProfileOut)
async def updateProfile(payload: UpdateProfileIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to update a users profile information.
    """

    # Need return function
