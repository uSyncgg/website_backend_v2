import uuid
import secrets
import string
import unicodedata

from fastapi import HTTPException, UploadFile
from sqlalchemy import select, update, delete, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import AsyncClient
from openai import AsyncOpenAI
from app.models.users import Profiles
from app.models.teams import Teams, TeamMembers, TeamInvites
from app.schemas.users import (
    ProfileSummary,
    TeamOut,
    TeamMemberOut,
    TeamInviteOut,
    TeamCreate,
    TeamUpdate,
    MyTeamInviteOut
)

# Teams are owned by one profile: an org's teams, or a team a player made themselves. The owner
# manages it (invites, captains, entering events and paying for entries); players join by
# accepting an invite to their player profile. Only player profiles can be on a team.
#
# Roster changes are checked against bracket events (app.services.brackets, imported inside the
# functions to avoid a cycle): rosters are locked while a team is in a live bracket, and a change
# moves the team's sign-ups between pending and signed up.
#
# This module mustn't import app.services.users at the top: that module builds an org's teams at
# sign up from here. The picture helpers are imported inside the functions that use them.

PUBLIC_ID_ALPHABET = string.ascii_lowercase + string.digits
PUBLIC_ID_LENGTH = 10
MAX_TEAMS_PER_OWNER = 50
SEARCH_LIMIT = 8
TAKEN_NAME = "You already have a team with that name."

def canonical_team_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold().strip()

def _canonical_username(username: str) -> str:
    # Same as app.services.users._canonical_username (NFKC, no format chars, casefolded).
    s = unicodedata.normalize("NFKC", username.strip())
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf").casefold()

def new_team(owner_profile_id, name: str, game: str | None, picture: str | None = None) -> Teams:
    return Teams(
        id = uuid.uuid4(),
        public_id = "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(PUBLIC_ID_LENGTH)),
        owner_profile_id = owner_profile_id,
        name = name.strip(),
        canonical_name = canonical_team_name(name),
        game = game or None,
        picture = picture
    )

def _summary(profile: Profiles) -> ProfileSummary:
    return ProfileSummary(
        type = profile.type,
        username = profile.username,
        public_id = profile.public_id,
        profile_picture = profile.profile_picture
    )

async def _teams_out(teams: list[Teams], db: AsyncSession, viewer_id: str | None = None, include_invites: bool = False) -> list[TeamOut]:
    """
    Teams with their owner and members (captains first). `include_invites` adds the pending
    invites, for teams the viewer owns only. `viewer_id` (an account id) fills in my_role.
    """

    if not teams:
        return []

    team_ids = [t.id for t in teams]
    owners = {
        p.id: p for p in (await db.execute(
            select(Profiles).where(Profiles.id.in_({t.owner_profile_id for t in teams}))
        )).scalars().all()
    }
    members: dict = {t.id: [] for t in teams}
    invites: dict = {t.id: [] for t in teams}
    my_roles: dict = {}

    rows = await db.execute(
        select(TeamMembers, Profiles)
        .join(Profiles, Profiles.id == TeamMembers.profile_id)
        .where(TeamMembers.team_id.in_(team_ids))
        .order_by(TeamMembers.role, TeamMembers.joined_at)
    )

    for member, profile in rows.all():
        members[member.team_id].append(TeamMemberOut(
            public_id = profile.public_id,
            username = profile.username,
            profile_picture = profile.profile_picture,
            role = member.role
        ))

        if viewer_id and str(profile.account_id) == str(viewer_id):
            my_roles[member.team_id] = member.role

    owned = {t.id for t in teams if viewer_id and str(owners[t.owner_profile_id].account_id) == str(viewer_id)}

    if include_invites and owned:
        rows = await db.execute(
            select(TeamInvites, Profiles)
            .join(Profiles, Profiles.id == TeamInvites.profile_id)
            .where(TeamInvites.team_id.in_(owned))
            .where(TeamInvites.status == "pending")
            .order_by(TeamInvites.created_at)
        )

        for invite, profile in rows.all():
            invites[invite.team_id].append(TeamInviteOut(
                id = str(invite.id),
                public_id = profile.public_id,
                username = profile.username,
                profile_picture = profile.profile_picture,
                created_at = invite.created_at
            ))

    return [
        TeamOut(
            public_id = t.public_id,
            name = t.name,
            game = t.game,
            picture = t.picture,
            created_at = t.created_at,
            owner = _summary(owners[t.owner_profile_id]),
            my_role = "owner" if t.id in owned else my_roles.get(t.id),
            members = members[t.id],
            invites = invites[t.id]
        )
        for t in teams
    ]

async def org_teams(owner_profile_id, db: AsyncSession, include_invites: bool = False) -> list[TeamOut]:
    """
    The teams a profile owns, oldest first, as shown on its public profile page.
    """

    teams = (await db.execute(
        select(Teams).where(Teams.owner_profile_id == owner_profile_id).order_by(Teams.created_at, Teams.name)
    )).scalars().all()

    return await _teams_out(list(teams), db)

async def _account_team_ids(user_id: str, db: AsyncSession):
    """
    A subquery of the ids of every team the account owns or is on.
    """

    my_profiles = select(Profiles.id).where(Profiles.account_id == user_id)

    return select(Teams.id).where(or_(
        Teams.owner_profile_id.in_(my_profiles),
        Teams.id.in_(select(TeamMembers.team_id).where(TeamMembers.profile_id.in_(my_profiles)))
    ))

async def my_teams(user_id: str, db: AsyncSession) -> list[TeamOut]:
    """
    Every team the signed in account owns (with pending invites) or plays on, newest first.
    """

    teams = (await db.execute(
        select(Teams).where(Teams.id.in_(await _account_team_ids(user_id, db))).order_by(Teams.created_at.desc())
    )).scalars().all()

    return await _teams_out(list(teams), db, user_id, include_invites = True)

async def get_team(public_id: str, user_id: str | None, db: AsyncSession) -> TeamOut:
    """
    A team's public page. Its owner also gets the pending invites; a player with a pending
    invite to it can see it too (it's public anyway).
    """

    team = (await db.execute(select(Teams).where(Teams.public_id == public_id))).scalars().first()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    return (await _teams_out([team], db, user_id, include_invites = True))[0]

async def _own_profile(user_id: str, profile_type: str, db: AsyncSession) -> Profiles:
    profile = (await db.execute(
        select(Profiles).where(Profiles.account_id == user_id).where(Profiles.type == profile_type)
    )).scalars().first()

    if not profile:
        raise HTTPException(status_code=404, detail=f"You don't have {'an org' if profile_type == 'org' else 'a ' + profile_type} profile.")

    return profile

async def _owned_team(user_id: str, team_public_id: str, db: AsyncSession) -> Teams:
    """
    One of the teams the signed in account owns (through any of its profiles), or a 404.
    """

    team = (await db.execute(
        select(Teams)
        .where(Teams.public_id == team_public_id)
        .where(Teams.owner_profile_id.in_(select(Profiles.id).where(Profiles.account_id == user_id)))
    )).scalars().first()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    return team

async def _player_by_public_id(public_id: str, db: AsyncSession) -> Profiles:
    profile = (await db.execute(
        select(Profiles).where(Profiles.public_id == public_id).where(Profiles.type == "player")
    )).scalars().first()

    if not profile:
        raise HTTPException(status_code=404, detail="Player not found.")

    return profile

async def _player_by_username(username: str, db: AsyncSession) -> Profiles:
    player = (await db.execute(
        select(Profiles).where(Profiles.type == "player").where(Profiles.canonical_username == _canonical_username(username))
    )).scalars().first()

    if not player:
        raise HTTPException(status_code=404, detail=f"No player named {username.strip()}.")

    return player

async def _invitable_player(user_id: str, username: str, db: AsyncSession) -> Profiles:
    """
    The player profile to invite, by username. You can't invite your own account's player
    profile (a player who makes a team is already on it as captain).
    """

    player = await _player_by_username(username, db)

    if str(player.account_id) == str(user_id):
        raise HTTPException(status_code=409, detail="You can't invite yourself to a team.")

    return player

async def _team_view(user_id: str, team: Teams, db: AsyncSession) -> TeamOut:
    return (await _teams_out([team], db, user_id, include_invites = True))[0]

async def search_players(query: str, user_id: str, db: AsyncSession) -> list[ProfileSummary]:
    """
    Player profiles whose username starts with (then contains) the query, for inviting.
    The signed in account's own player profile is left out (you can't invite yourself).
    """

    q = _canonical_username(query)

    if len(q) < 2:
        return []

    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = (await db.execute(
        select(Profiles)
        .where(Profiles.type == "player")
        .where(Profiles.account_id != user_id)
        .where(Profiles.canonical_username.like(f"%{escaped}%", escape = "\\"))
        .order_by(~Profiles.canonical_username.like(f"{escaped}%", escape = "\\"), Profiles.canonical_username)
        .limit(SEARCH_LIMIT)
    )).scalars().all()

    return [_summary(p) for p in rows]

async def create_team(
    user_id: str,
    body: TeamCreate,
    picture: UploadFile | None,
    db: AsyncSession,
    supabase: AsyncClient,
    openai: AsyncOpenAI
) -> TeamOut:
    """
    Creates a team owned by one of the account's profiles, with an optional picture, and
    invites the listed players. Like sign up: checks first, then the slow picture moderation
    and upload with no connection held, then one write (the picture is removed if it fails).
    """

    from app.services.users import process_pfp, _remove_pfps

    owner = await _own_profile(user_id, body.owner_type, db)
    count = len((await db.execute(select(Teams.id).where(Teams.owner_profile_id == owner.id))).all())

    if count >= MAX_TEAMS_PER_OWNER:
        raise HTTPException(status_code=409, detail=f"A profile can own up to {MAX_TEAMS_PER_OWNER} teams.")

    taken = (await db.execute(
        select(Teams.id).where(Teams.owner_profile_id == owner.id).where(Teams.canonical_name == canonical_team_name(body.name))
    )).first()

    if taken:
        raise HTTPException(status_code=409, detail=TAKEN_NAME)

    invitees: dict = {}

    for username in body.invites:
        player = await _invitable_player(user_id, username, db)
        invitees[player.id] = player

    owner_id = owner.id
    await db.rollback()

    url, path = (None, None)

    if picture is not None:
        url, path = await process_pfp(picture, supabase, user_id, openai)

    try:
        team = new_team(owner_id, body.name, body.game, url)
        db.add(team)
        await db.flush()
        # A player who makes their own team is on it as its captain.
        if body.owner_type == "player":
            db.add(TeamMembers(team_id = team.id, profile_id = owner_id, role = "captain"))
        db.add_all([TeamInvites(team_id = team.id, profile_id = pid) for pid in invitees])
        await db.commit()
    except IntegrityError:
        await db.rollback()
        await _remove_pfps([path] if path else [], supabase)
        raise HTTPException(status_code=409, detail=TAKEN_NAME)
    except Exception:
        await db.rollback()
        await _remove_pfps([path] if path else [], supabase)
        raise

    return await _team_view(user_id, team, db)

async def update_team(user_id: str, team_public_id: str, body: TeamUpdate, db: AsyncSession) -> TeamOut:
    team = await _owned_team(user_id, team_public_id, db)
    values = body.model_dump(exclude_unset=True)

    if "name" in values:
        values["name"] = values["name"].strip()
        values["canonical_name"] = canonical_team_name(values["name"])

    if values:
        try:
            await db.execute(update(Teams).where(Teams.id == team.id).values(**values))
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail=TAKEN_NAME)

        await db.refresh(team)

        if "name" in values:
            # Its sign-ups for upcoming events show the new name.
            from app.services.brackets import sync_team
            await sync_team(team.id, db)
            await db.commit()

    return await _team_view(user_id, team, db)

async def update_team_picture(
    user_id: str,
    team_public_id: str,
    picture: UploadFile,
    db: AsyncSession,
    supabase: AsyncClient,
    openai: AsyncOpenAI
) -> TeamOut:
    from app.services.users import process_pfp, _remove_pfps, _storage_path

    team = await _owned_team(user_id, team_public_id, db)
    team_id = team.id
    old = _storage_path(team.picture) if team.picture else None
    await db.rollback()

    url, path = await process_pfp(picture, supabase, user_id, openai)

    try:
        await db.execute(update(Teams).where(Teams.id == team_id).values(picture = url))
        await db.commit()
    except Exception:
        await db.rollback()
        await _remove_pfps([path], supabase)
        raise

    await _remove_pfps([old] if old else [], supabase)
    team = (await db.execute(select(Teams).where(Teams.id == team_id))).scalars().one()

    return await _team_view(user_id, team, db)

async def delete_team(user_id: str, team_public_id: str, db: AsyncSession, supabase: AsyncClient) -> None:
    from app.services.users import _remove_pfps, _storage_path

    from app.services.brackets import before_team_deleted, refresh_events

    team = await _owned_team(user_id, team_public_id, db)
    old = _storage_path(team.picture) if team.picture else None

    # Not while it's in a live bracket; its sign-ups for upcoming events go with it.
    upcoming = await before_team_deleted(team.id, db)

    # Members and invites cascade with the team.
    await db.execute(delete(Teams).where(Teams.id == team.id))
    await refresh_events(upcoming, db)
    await db.commit()

    await _remove_pfps([old] if old else [], supabase)

async def invite_player(user_id: str, team_public_id: str, username: str, db: AsyncSession) -> TeamOut:
    """
    Invites a player profile (by its username) onto one of the account's teams.
    """

    team = await _owned_team(user_id, team_public_id, db)
    player = await _invitable_player(user_id, username, db)

    on_team = (await db.execute(
        select(TeamMembers.profile_id).where(TeamMembers.team_id == team.id).where(TeamMembers.profile_id == player.id)
    )).first()

    if on_team:
        raise HTTPException(status_code=409, detail=f"{player.username} is already on {team.name}.")

    # Read before the write: a rollback expires the loaded rows, and reading them afterwards
    # would need IO the async session can't do there.
    duplicate = f"{player.username} already has an invite to {team.name}."

    try:
        db.add(TeamInvites(team_id = team.id, profile_id = player.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=duplicate)

    return await _team_view(user_id, team, db)

async def cancel_invite(user_id: str, team_public_id: str, invite_id: uuid.UUID, db: AsyncSession) -> TeamOut:
    team = await _owned_team(user_id, team_public_id, db)

    await db.execute(
        update(TeamInvites)
        .where(TeamInvites.id == invite_id)
        .where(TeamInvites.team_id == team.id)
        .where(TeamInvites.status == "pending")
        .values(status = "cancelled")
    )
    await db.commit()

    return await _team_view(user_id, team, db)

async def set_member_role(user_id: str, team_public_id: str, player_public_id: str, role: str, db: AsyncSession) -> TeamOut:
    team = await _owned_team(user_id, team_public_id, db)
    player = await _player_by_public_id(player_public_id, db)

    result = await db.execute(
        update(TeamMembers)
        .where(TeamMembers.team_id == team.id)
        .where(TeamMembers.profile_id == player.id)
        .values(role = role)
    )

    if result.rowcount == 0:
        await db.rollback()
        raise HTTPException(status_code=404, detail="That player isn't on this team.")

    await db.commit()

    return await _team_view(user_id, team, db)

async def remove_member(user_id: str, team_public_id: str, player_public_id: str, db: AsyncSession) -> TeamOut:
    from app.services.brackets import assert_roster_unlocked, sync_team

    team = await _owned_team(user_id, team_public_id, db)
    player = await _player_by_public_id(player_public_id, db)
    await assert_roster_unlocked(team.id, db)

    await db.execute(
        delete(TeamMembers).where(TeamMembers.team_id == team.id).where(TeamMembers.profile_id == player.id)
    )
    await sync_team(team.id, db)
    await db.commit()

    return await _team_view(user_id, team, db)

async def leave_team(user_id: str, team_public_id: str, db: AsyncSession) -> None:
    """
    The signed in player leaves a team they're on (the owner deletes it instead).
    """

    player = await _own_profile(user_id, "player", db)
    team = (await db.execute(select(Teams).where(Teams.public_id == team_public_id))).scalars().first()

    if not team:
        raise HTTPException(status_code=404, detail="Team not found.")

    if team.owner_profile_id == player.id:
        raise HTTPException(status_code=409, detail="You own this team. Delete it instead.")

    from app.services.brackets import assert_roster_unlocked, sync_team
    await assert_roster_unlocked(team.id, db)

    await db.execute(delete(TeamMembers).where(TeamMembers.team_id == team.id).where(TeamMembers.profile_id == player.id))
    await sync_team(team.id, db)
    await db.commit()

# ---- the invited player's side ---------------------------------------------

async def my_invites(user_id: str, db: AsyncSession) -> list[MyTeamInviteOut]:
    """
    Pending invites to the signed in user's player profile, newest first, each with the team
    it's for (and its roster) and who sent it.
    """

    rows = (await db.execute(
        select(TeamInvites, Teams)
        .join(Teams, Teams.id == TeamInvites.team_id)
        .join(Profiles, Profiles.id == TeamInvites.profile_id)
        .where(Profiles.account_id == user_id)
        .where(Profiles.type == "player")
        .where(TeamInvites.status == "pending")
        .order_by(TeamInvites.created_at.desc())
    )).all()

    if not rows:
        return []

    teams = {t.public_id: t for t in await _teams_out([team for _, team in rows], db, user_id)}

    return [
        MyTeamInviteOut(
            id = str(invite.id),
            team = teams[team.public_id],
            invited_by = teams[team.public_id].owner,
            created_at = invite.created_at
        )
        for invite, team in rows
    ]

async def respond_to_invite(user_id: str, invite_id: uuid.UUID, accept: bool, db: AsyncSession) -> list[MyTeamInviteOut]:
    """
    Accepts (joins the team as a player) or declines one of the signed in player's invites.
    """

    player = await _own_profile(user_id, "player", db)

    invite = (await db.execute(
        select(TeamInvites)
        .where(TeamInvites.id == invite_id)
        .where(TeamInvites.profile_id == player.id)
        .where(TeamInvites.status == "pending")
        .with_for_update()
    )).scalars().first()

    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found.")

    if accept:
        from app.services.brackets import assert_roster_unlocked, sync_team
        from app.services.notifications import notify

        await assert_roster_unlocked(invite.team_id, db)

    invite.status = "accepted" if accept else "declined"

    if accept:
        already = (await db.execute(
            select(TeamMembers.profile_id).where(TeamMembers.team_id == invite.team_id).where(TeamMembers.profile_id == player.id)
        )).first()

        if not already:
            db.add(TeamMembers(team_id = invite.team_id, profile_id = player.id))
            await db.flush()

            # Tell the owner and the captains, then update the team's event sign-ups.
            team = await db.get(Teams, invite.team_id)
            owner_account = (await db.execute(select(Profiles.account_id).where(Profiles.id == team.owner_profile_id))).scalar()
            captain_accounts = (await db.execute(
                select(Profiles.account_id)
                .join(TeamMembers, TeamMembers.profile_id == Profiles.id)
                .where(TeamMembers.team_id == team.id)
                .where(TeamMembers.role == "captain")
            )).scalars().all()
            notify(db, {owner_account, *captain_accounts} - {player.account_id}, "team_joined", {
                "team_id": team.public_id, "team_name": team.name, "who": player.username,
            })
            await sync_team(team.id, db)

    await db.commit()

    return await my_invites(user_id, db)
