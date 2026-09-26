import uuid
import logging

from sqlalchemy import select, insert, update, Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from app.schemas.users import RegistrationIn, RegistrationOut, PlayerDetails, HostDetails
from app.models.users import Players, Hosts, UsersParent, PlayerCompSiteAccounts, Venues

logger = logging.getLogger(__name__)

def _build_player(player_data: PlayerDetails, user_id: uuid.UUID) -> Players:
    """
    
    """

    return Players(
        id = str(uuid.uuid4()),
        user_id = user_id,
        first_name = player_data.first_name,
        last_name = player_data.last_name,
        phone_number = player_data.phone_number,
        gender = player_data.gender,
        date_of_birth = player_data.date_of_birth,
        country = player_data.country,
        games = player_data.interests,
        other_games = player_data.other_games,
        battlenet = player_data.battlenet,
        activision = player_data.activision,
        steam = player_data.steam,
        riot = player_data.riot
    )

def _build_host(host_data: HostDetails, user_id: uuid.UUID) -> Hosts:
    """
    
    """

    return Hosts (
        id = str(uuid.uuid4()),
        user_id = user_id,
        games = host_data.hosted_games,
        other_games = host_data.other_hosted_games,
        organization = host_data.organization,
        host_country = host_data.host_country,
        event_types = host_data.event_types
    )

def _build_venue(payload: RegistrationIn, user_id: str) -> Sequence[Venues]:
    """
    
    """

    venues: Sequence[Venues] = []

    for v in payload.venues:
        if v.get("name") and v.get("location"):
            venue = Venues(
                id = user_id,
                venue_name = v.get("name"),
                location = v.get("location")
            )

            venues.append(venue)

    return venues

def _build_user(payload: RegistrationIn) -> UsersParent:
    """
    
    """

    return UsersParent (
        id = str(uuid.uuid4()),
        username = payload.username,
        email = payload.email,
        is_player = True if "player" in payload.signup_path else False,
        is_host = True if "host" in payload.signup_path else False,
        twitch = payload.twitch,
        twitter = payload.twitter,
        youtube = payload.youtube,
        kick = payload.kick,
        discord = payload.discord,
        instagram = payload.instagram,
        bio = payload.bio,
        bracket_hosting = True if payload.bracket_hosting is None or payload.bracket_hosting == "yes" else False,
        other_roles = payload.other_roles,
        other_role_detail = payload.other_role_detail
    )

async def deposit_models(models: list[UsersParent | Hosts | Players | Venues], db: AsyncSession) -> None:
    """
    
    """
    db.add(models[0])
    await db.flush()

    db.add_all(models[1:])

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "ix_users_username_player_unique" in str(e.orig):
            raise HTTPException(status_code=409, detail="Player username is already taken. Enter a new one.")
        if "ix_users_username_host_unique" in str(e.orig):
            raise HTTPException(status_code=409, detail="Host username is already taken. Enter a new one.")
        logger.exception("Registration failed with unexpected integrity error.")
        raise HTTPException(status_code = 409, detail = "Registration conflicts with existing data.")

    return None

async def handle_registration(payload: RegistrationIn, db: AsyncSession) -> RegistrationOut:
    """
    
    """

    user_model = _build_user(payload)
    models = [user_model]

    if "player" in payload.signup_path:
        models.append(_build_player(payload.player, user_model.id))

    if "host" in payload.signup_path:
        models.append(_build_host(payload.host, user_model.id))

    if payload.venues:
        models.extend(_build_venue(payload, user_model.id))

    await deposit_models(models, db)

    ### NOTE: below are things for comp sites - not adding just yet
    # cmg = player_data.cmg,
    # gankster = player_data.gankster,
    # faceit = player_data.faceit,
    # battlefly = player_data.battlefly


    return RegistrationOut(message = "Sign up form successfully submitted!")