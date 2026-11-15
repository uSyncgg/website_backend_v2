import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import AsyncClient
from openai import AsyncOpenAI
from app.core.dependencies import get_db, verify_supabase_jwt, optional_supabase_jwt, get_supabase, get_openai
from app.core.limiter import limiter, MODERATE, STRICT_ACCOUNT_CHANGE, STRICT_USERNAME_CHECK
from app.schemas.users import (
    ProfileSummary,
    TeamOut,
    TeamCreate,
    TeamUpdate,
    TeamInviteCreate,
    TeamRoleUpdate,
    MyTeamInviteOut
)
from app.services.teams import (
    my_teams,
    get_team,
    search_players,
    create_team,
    update_team,
    update_team_picture,
    delete_team,
    invite_player,
    cancel_invite,
    set_member_role,
    remove_member,
    leave_team,
    my_invites,
    respond_to_invite
)

# Teams are owned by one of an account's profiles (an org's teams, or a player's own team).
# Owner-only routes 404 for anyone else. Most writes return the updated team.
router = APIRouter(prefix="/teams", tags=["Teams"])

@router.get("/me", response_model = list[TeamOut])
async def getMyTeams(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Every team the signed in account owns or plays on.
    """

    return await my_teams(claims["sub"], db)

@router.get("/invites/me", response_model = list[MyTeamInviteOut])
async def getMyInvites(claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Pending invites to the signed in user's player profile, with the team each is for.
    """

    return await my_invites(claims["sub"], db)

@router.post("/invites/{invite_id}/accept", response_model = list[MyTeamInviteOut])
@limiter.limit(MODERATE)
async def acceptInvite(request: Request, response: Response, invite_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await respond_to_invite(claims["sub"], invite_id, True, db)

@router.post("/invites/{invite_id}/decline", response_model = list[MyTeamInviteOut])
@limiter.limit(MODERATE)
async def declineInvite(request: Request, response: Response, invite_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await respond_to_invite(claims["sub"], invite_id, False, db)

@router.get("/players/search", response_model = list[ProfileSummary])
@limiter.limit(STRICT_USERNAME_CHECK)
async def searchPlayers(request: Request, response: Response, q: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    Player profiles matching `q`, for the invite picker.
    """

    return await search_players(q, claims["sub"], db)

@router.post("", response_model = TeamOut)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def createTeam(
    request: Request,
    response: Response,
    data: str = Form(...),
    picture: UploadFile | None = File(None),
    claims: dict = Depends(verify_supabase_jwt),
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    openai: AsyncOpenAI = Depends(get_openai)
):
    """
    Creates a team. `data` is TeamCreate as JSON; `picture` is the optional team image.
    """

    try:
        body = TeamCreate.model_validate_json(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_context=False))

    return await create_team(claims["sub"], body, picture, db, supabase, openai)

@router.get("/{team_id}", response_model = TeamOut)
async def getTeam(team_id: str, claims: dict | None = Depends(optional_supabase_jwt), db: AsyncSession = Depends(get_db)):
    """
    A team's public page. Signed in, it also says your place on it (and, for its owner, the pending invites).
    """

    return await get_team(team_id, claims["sub"] if claims else None, db)

@router.patch("/{team_id}", response_model = TeamOut)
@limiter.limit(MODERATE)
async def updateTeam(request: Request, response: Response, team_id: str, body: TeamUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await update_team(claims["sub"], team_id, body, db)

@router.put("/{team_id}/picture", response_model = TeamOut)
@limiter.limit(STRICT_ACCOUNT_CHANGE)
async def updateTeamPicture(
    request: Request,
    response: Response,
    team_id: str,
    picture: UploadFile = File(...),
    claims: dict = Depends(verify_supabase_jwt),
    db: AsyncSession = Depends(get_db),
    supabase: AsyncClient = Depends(get_supabase),
    openai: AsyncOpenAI = Depends(get_openai)
):
    return await update_team_picture(claims["sub"], team_id, picture, db, supabase, openai)

@router.delete("/{team_id}", status_code = 204)
@limiter.limit(MODERATE)
async def deleteTeam(request: Request, response: Response, team_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db), supabase: AsyncClient = Depends(get_supabase)):
    await delete_team(claims["sub"], team_id, db, supabase)

    return Response(status_code = 204)

@router.post("/{team_id}/leave", status_code = 204)
@limiter.limit(MODERATE)
async def leaveTeam(request: Request, response: Response, team_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    await leave_team(claims["sub"], team_id, db)

    return Response(status_code = 204)

@router.post("/{team_id}/invites", response_model = TeamOut)
@limiter.limit(MODERATE)
async def invitePlayer(request: Request, response: Response, team_id: str, body: TeamInviteCreate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await invite_player(claims["sub"], team_id, body.username, db)

@router.delete("/{team_id}/invites/{invite_id}", response_model = TeamOut)
@limiter.limit(MODERATE)
async def cancelInvite(request: Request, response: Response, team_id: str, invite_id: uuid.UUID, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await cancel_invite(claims["sub"], team_id, invite_id, db)

@router.patch("/{team_id}/members/{player_id}", response_model = TeamOut)
@limiter.limit(MODERATE)
async def setMemberRole(request: Request, response: Response, team_id: str, player_id: str, body: TeamRoleUpdate, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await set_member_role(claims["sub"], team_id, player_id, body.role, db)

@router.delete("/{team_id}/members/{player_id}", response_model = TeamOut)
@limiter.limit(MODERATE)
async def removeMember(request: Request, response: Response, team_id: str, player_id: str, claims: dict = Depends(verify_supabase_jwt), db: AsyncSession = Depends(get_db)):
    return await remove_member(claims["sub"], team_id, player_id, db)
