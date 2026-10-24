from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt, get_supabase, get_openai
from app.core.limiter import limiter, STRICT_REGISTER, STRICT_USERNAME_CHECK, MODERATE
from app.schemas.users import RegistrationIn, RegistrationOut, Profile, Me, ProfileSettingsOut, UserUpdate, PlayerUpdate, HostUpdate
from app.services.users import handle_registration, process_pfp, check_valid_username, get_profile, get_me, get_profile_settings
from pydantic import ValidationError
from supabase import AsyncClient
from openai import AsyncOpenAI

router = APIRouter(prefix="/users", tags=["Users"])

@router.post("/register", response_model=RegistrationOut)
@limiter.limit(STRICT_REGISTER)
async def registerUser(
    request: Request,
    response: Response,
    data: str = Form(...), 
    profile_picture: UploadFile | None = File(None), 
    claims: dict = Depends(verify_supabase_jwt), 
    db: AsyncSession = Depends(get_db), 
    supabase: AsyncClient = Depends(get_supabase),
    openai: AsyncOpenAI = Depends(get_openai)
):
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

    await check_valid_username(
        payload.username, 
        db, 
        openai,
        ("body", "username"),
        True if "player" in payload.signup_path else False,
        True if "host" in payload.signup_path else False
    )

    if profile_picture:
        pfp_url, pfp_path = await process_pfp(profile_picture, supabase, user_id, openai)

    return await handle_registration(payload, email, pfp_url, pfp_path, user_id, db, supabase)

@router.get("/me", response_model=Me)
async def getMe(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Returns the signed in user's username and profile picture, or a 404 if their profile is incomplete.
    """

    return await get_me(claims["sub"], db)

@router.get("/check/{username}")
@limiter.limit(STRICT_USERNAME_CHECK)
async def checkUsername(
    request: Request, 
    response: Response, 
    username: str, 
    player: bool | None = None, 
    host: bool | None = None, 
    db: AsyncSession = Depends(get_db), 
    openai: AsyncOpenAI = Depends(get_openai)
):
    """
    
    """

    return await check_valid_username(username, db, openai, ("path", "username"), player, host)

@router.get("/fetch/{username}/profile", response_model = Profile)
async def getProfile(username: str, db: AsyncSession = Depends(get_db)):
    """
    
    """

    return await get_profile(username, db)

@router.get("/me/settings", response_model = ProfileSettingsOut)
async def getProfileSettings(request: Request, response: Response, db: AsyncSession = Depends(get_db), claims: dict = Depends(verify_supabase_jwt)):
    """
    
    """

    user_id = claims["sub"]

    return await get_profile_settings(user_id, db)

@router.patch("/me/user", response_model="")
@limiter.limit(MODERATE)
async def updateUser(
    request: Request, 
    response: Response, 
    user: UserUpdate, 
    db: AsyncSession = Depends(get_db), 
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    
    """

    user_id = claims["sub"]

    try:
        payload = user.model_dump(exclude_unset=True, by_alias=True)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

@router.patch("/me/player", response_model = "")
@limiter.limit(MODERATE)
async def updatePlayer(
    request: Request,
    response: Response,
    player: PlayerUpdate,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    
    """

    user_id = claims["sub"]

    try:
        payload = player.model_dump(exclude_unset=True, by_alias=True)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

@router.patch("/me/host", response_model = "")
@limiter.limit(MODERATE)
async def updateHost(
    request: Request,
    response: Response,
    host: HostUpdate,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    
    """

    user_id = claims["sub"]

    try:
        payload = host.model_dump(exclude_unset=True, by_alias=True)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))


