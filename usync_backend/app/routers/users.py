from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_db, verify_supabase_jwt, get_supabase, get_openai
from app.core.limiter import limiter, STRICT_REGISTER, STRICT_USERNAME_CHECK, STRICT_ACCOUNT_CHANGE, STRICT_ACCOUNT_DELETE, MODERATE
from app.schemas.users import (
    RegistrationIn,
    RegistrationOut,
    ProfileIn,
    ProfileType,
    Profile,
    Me,
    ProfileSettingsOut,
    ProfileUpdate,
    UsernameUpdate,
    DefaultProfileUpdate
)
from app.services.users import (
    handle_registration,
    check_valid_username,
    get_profile,
    get_me,
    get_profile_settings,
    add_profile,
    update_profile,
    update_profile_picture,
    change_username,
    set_default_profile,
    delete_account
)
from pydantic import TypeAdapter, ValidationError
from supabase import AsyncClient
from openai import AsyncOpenAI

router = APIRouter(prefix="/users", tags=["Users"])

_profile_in = TypeAdapter(ProfileIn)

@router.post("/register", response_model=RegistrationOut)
@limiter.limit(STRICT_REGISTER)
async def registerUser(
    request: Request,
    response: Response,
    data: str = Form(...),
    profile_picture_player: UploadFile | None = File(None),
    profile_picture_host: UploadFile | None = File(None),
    profile_picture_venue: UploadFile | None = File(None),
    claims: dict = Depends(verify_supabase_jwt),
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    openai: AsyncOpenAI = Depends(get_openai)
):
    """
    Creates the signed in user's account and one profile per entry in `data`. Each profile set
    to upload a picture sends it as profile_picture_<type>.
    """

    try:
        payload = RegistrationIn.model_validate_json(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

    files = {
        "player": profile_picture_player,
        "host": profile_picture_host,
        "venue": profile_picture_venue
    }

    return await handle_registration(payload, files, claims["email"], claims["sub"], db, supabase, openai)

@router.get("/me", response_model=Me)
async def getMe(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Returns the signed in user's default profile and all their profiles, or a 404 if sign up isn't complete.
    """

    return await get_me(claims["sub"], db)

@router.get("/check/{username}")
@limiter.limit(STRICT_USERNAME_CHECK)
async def checkUsername(
    request: Request,
    response: Response,
    username: str,
    type: ProfileType,
    db: AsyncSession = Depends(get_db),
    openai: AsyncOpenAI = Depends(get_openai)
):
    """
    Checks a username against the rules and against other profiles of the same type.
    """

    return await check_valid_username(username, type, db, openai, ("path", "username"))

@router.get("/profiles/{public_id}", response_model = Profile)
async def getProfile(public_id: str, db: AsyncSession = Depends(get_db)):
    """
    Returns a public profile and a list of the account's other profiles to switch to.
    """

    return await get_profile(public_id, db)

@router.get("/me/profiles/{profile_type}", response_model = ProfileSettingsOut)
async def getProfileSettings(
    profile_type: ProfileType,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Returns everything the signed in user can edit on one of their profiles.
    """

    return await get_profile_settings(claims["sub"], profile_type, db)

@router.post("/me/profiles", response_model = ProfileSettingsOut)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def addProfile(
    request: Request,
    response: Response,
    data: str = Form(...),
    profile_picture: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    openai: AsyncOpenAI = Depends(get_openai),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Adds a profile of a new type (player, host or venue) to the signed in user's account.
    409 if they already have one of that type or the username is taken.
    """

    try:
        profile = _profile_in.validate_json(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

    return await add_profile(claims["sub"], profile, profile_picture, db, supabase, openai)

@router.patch("/me/profiles/{profile_type}", response_model = ProfileSettingsOut)
@limiter.limit(MODERATE)
async def updateProfile(
    request: Request,
    response: Response,
    profile_type: ProfileType,
    changes: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Partially updates one of the signed in user's profiles. 404 if they don't have that type.
    """

    return await update_profile(claims["sub"], profile_type, changes, db)

@router.patch("/me/profiles/{profile_type}/username", response_model = ProfileSettingsOut)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def updateUsername(
    request: Request,
    response: Response,
    profile_type: ProfileType,
    body: UsernameUpdate,
    db: AsyncSession = Depends(get_db),
    openai: AsyncOpenAI = Depends(get_openai),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Renames one of the signed in user's profiles, using the same rules as sign up.
    """

    return await change_username(claims["sub"], profile_type, body.username, db, openai)

@router.put("/me/profiles/{profile_type}/profile-picture", response_model = ProfileSettingsOut)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def updateProfilePicture(
    request: Request,
    response: Response,
    profile_type: ProfileType,
    profile_picture: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    openai: AsyncOpenAI = Depends(get_openai),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Replaces one of the signed in user's profile pictures.
    """

    return await update_profile_picture(claims["sub"], profile_type, profile_picture, db, supabase, openai)

@router.patch("/me/default-profile", response_model = Me)
@limiter.limit(MODERATE)
async def updateDefaultProfile(
    request: Request,
    response: Response,
    body: DefaultProfileUpdate,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(verify_supabase_jwt)
):
    """
    Sets which of the signed in user's profiles is their default.
    """

    return await set_default_profile(claims["sub"], body.type, db)

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
