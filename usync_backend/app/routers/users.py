from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt, get_supabase
from app.schemas.users import UpdateProfileIn, UpdateProfileOut, RegistrationIn, RegistrationOut
from app.services.users import handle_registration, process_pfp
from pydantic import ValidationError
from supabase import AsyncClient

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/register", response_model=RegistrationOut)
async def registerUser(data: str = Form(...), profile_picture: UploadFile | None = File(None), claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db), supabase: AsyncClient = Depends(get_supabase)):
    """
    Calls functionality to register a user.
    """

    user_id = claims["sub"]
    email = claims["email"]
    pfp_url = ""
    pfp_path = ""

    try:
        payload = RegistrationIn.model_validate_json(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

    
    if profile_picture:
        pfp_url, pfp_path = await process_pfp(profile_picture, supabase, user_id)

    return await handle_registration(payload, email, pfp_url, pfp_path, user_id, db, supabase)

@router.post("/profile/update", response_model=UpdateProfileOut)
async def updateProfile(payload: UpdateProfileIn, db: AsyncSession = Depends(get_db)):
    """
    Calls functionality to update a users profile information.
    """

    # Need return function
