from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt, get_supabase, get_openai
from app.core.limiter import limiter, STRICT_REGISTER, STRICT_USERNAME_CHECK, STRICT_ACCOUNT_CHANGE, STRICT_ACCOUNT_DELETE, MODERATE
from app.schemas.users import RegistrationIn, RegistrationOut, Profile, Me, ProfileSettingsOut, UserUpdate, PlayerUpdate, HostUpdate, UsernameUpdate, BecomeHostIn
from app.services.users import (
    handle_registration, 
    process_pfp, 
    check_valid_username, 
    get_profile, 
    get_me, 
    get_profile_settings,
    update_profile_section,
    update_profile_picture,
    change_username,
    add_host_role,
    delete_account
)
from app.models.users import UsersParent, Players, Hosts
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

@router.patch("/me/user", response_model = ProfileSettingsOut)
@limiter.limit(MODERATE)
async def updateUser(
    request: Request, 
    response: Response, 
    user: UserUpdate, 
    db: AsyncSession = Depends(get_db), 
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Partially updates the signed in user's users row (and replaces their venues if sent).
    """

    payload = user.model_dump(exclude_unset=True, by_alias=True)

    return await update_profile_section(claims["sub"], UsersParent, payload, db)

@router.patch("/me/player", response_model = ProfileSettingsOut)
@limiter.limit(MODERATE)
async def updatePlayer(
    request: Request,
    response: Response,
    player: PlayerUpdate,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Partially updates the signed in user's players row. 404 if they aren't a player.
    """

    payload = player.model_dump(exclude_unset=True, by_alias=True)

    return await update_profile_section(claims["sub"], Players, payload, db)

@router.patch("/me/host", response_model = ProfileSettingsOut)
@limiter.limit(MODERATE)
async def updateHost(
    request: Request,
    response: Response,
    host: HostUpdate,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Partially updates the signed in user's hosts row. 404 if they aren't a host.
    """

    payload = host.model_dump(exclude_unset=True, by_alias=True)

    return await update_profile_section(claims["sub"], Hosts, payload, db)

@router.post("/me/host", response_model = ProfileSettingsOut)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def becomeHost(
    request: Request,
    response: Response,
    payload: BecomeHostIn,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt),
    openai: AsyncOpenAI = Depends(get_openai)
):
    """
    Adds the host role to the signed in user's account: creates their hosts row from the
    host details and sets is_host. 409 if they're already a host or the username is taken.
    """

    return await add_host_role(claims["sub"], payload, db, openai)

@router.put("/me/profile-picture", response_model = Me)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def updateProfilePicture(
    request: Request,
    response: Response,
    profile_picture: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    openai: AsyncOpenAI = Depends(get_openai),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Replaces the signed in user's profile picture.
    """

    return await update_profile_picture(claims["sub"], profile_picture, db, supabase, openai)

@router.patch("/me/username", response_model = Me)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def updateUsername(
    request: Request,
    response: Response,
    body: UsernameUpdate,
    db: AsyncSession = Depends(get_db),
    openai: AsyncOpenAI = Depends(get_openai),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Changes the signed in user's username, using the same rules as sign up.
    """

    return await change_username(claims["sub"], body.username, db, openai)

@router.delete("/me", status_code = 204)
@limiter.limit(STRICT_ACCOUNT_DELETE)
async def deleteAccount(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Permanently deletes the signed in user's account: app data, Supabase auth user and stored files.
    """

    await delete_account(claims["sub"], db, supabase)

    return Response(status_code = 204)
