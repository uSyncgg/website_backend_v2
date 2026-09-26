from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db
from app.schemas.users import UpdateProfileIn, UpdateProfileOut, RegistrationIn, RegistrationOut
from app.services.users import handle_registration
from pydantic import ValidationError

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/register", response_model=RegistrationOut)
async def registerUser(data: str = Form(...), profile_picture: UploadFile | None = File(None), db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to register a user.
    """

    try:
        payload = RegistrationIn.model_validate_json(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

    return await handle_registration(payload, db)

@router.post("/profile/update", response_model=UpdateProfileOut)
async def updateProfile(payload: UpdateProfileIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to update a users profile information.
    """

    # Need return function
