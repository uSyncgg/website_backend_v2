from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI
from app.models.users import Profiles
from app.models.chat import MatchChatRooms, MatchChatMessages, MatchChatAdmins
from app.models.brackets import BracketEvents
from app.services import bracket_engine as engine
from app.services import chat_rooms
from app.services.chat_filters import screen_message
from app.services.notifications import notify
from app.services.brackets import (
    _event_by_public_id, _live_bracket, _built, _entries, _entry_accounts, _manager_accounts,
    _my_profile_ids, _admin_event_ids, _iso,
)

# Match chats: who can read and write, and the three things that happen in a room besides talking.
#
#   Players - everyone on either side of the match (a solo player, or the team's owner, captains and
#     players) - can read and write whenever both sides are known.
#   Admins - the host and accepted event admins, treated the same - can't read a chat until they
#     join it. Joining shows them the whole history and posts "<name> joined" for the players.
#   A player can request an admin: it says so in the chat and every admin gets a notification
#     that leads to the match page. A score dispute or a reported no-show does the same on its own.
#
# Every message goes through app.services.chat_filters before it is stored.

HISTORY_LIMIT = 500

def _message_out(m: MatchChatMessages, user_id: str) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "sender": {"name": m.sender_name, "role": m.sender_role} if m.kind == "user" else None,
        "body": m.body,
        "at": _iso(m.created_at),
        "mine": bool(m.sender_account_id and str(m.sender_account_id) == str(user_id)),
    }

async def _display_name(user_id: str, db: AsyncSession, prefer: set | None = None) -> str:
    """
    The name a person chats under: their player profile's username, or for an admin the profile that
    gives them access (the host profile, or the profile they were invited as).
    """

    profiles = (await db.execute(select(Profiles).where(Profiles.account_id == user_id))).scalars().all()
    if not profiles:
        raise HTTPException(status_code=403, detail="Finish setting up your profile to use chat.")
    for profile in profiles:
        if prefer and profile.id in prefer:
            return profile.username
    for profile in profiles:
        if profile.type == "player":
            return profile.username
    return profiles[0].username

async def _context(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    """
    Loads the event, match and room and works out what the signed in account is to this chat.
    """

    event = await _event_by_public_id(public_id, db)
    _live_bracket(event)
    match = engine.find_match(_built(event), match_id)
    if not match or match["status"] in ("bye", "empty"):
        raise HTTPException(status_code=404, detail="Match not found.")

    room = await chat_rooms.get_room(db, event, match_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Match not found.")

    mine = await _my_profile_ids(user_id, db)
    is_manager = event.host_profile_id in mine or event.id in await _admin_event_ids(user_id, db)

    is_player = False
    if engine.is_entrant(match["a"]) and engine.is_entrant(match["b"]):
        entries = {str(e.id): e for e in await _entries(event.id, db)}
        sides = [entries[s] for s in (match["a"], match["b"]) if s in entries]
        accounts = await _entry_accounts(sides, db)
        is_player = any(str(user_id) in {str(a) for a in people} for people in accounts.values())

    if not (is_player or is_manager):
        raise HTTPException(status_code=404, detail="Match not found.")

    joined = (await db.execute(
        select(MatchChatAdmins).where(MatchChatAdmins.room_id == room.id).where(MatchChatAdmins.account_id == user_id)
    )).scalars().first()

    return {
        "event": event, "match": match, "room": room, "mine": mine,
        "role": "player" if is_player else "admin",
        "is_manager": is_manager, "joined": joined is not None,
        "open": engine.is_entrant(match["a"]) and engine.is_entrant(match["b"]),
    }

def _can_read(ctx: dict) -> bool:
    return ctx["role"] == "player" or ctx["joined"]

def _can_write(ctx: dict) -> bool:
    return ctx["open"] and _can_read(ctx)

async def _state(ctx: dict, user_id: str, after: int | None, db: AsyncSession) -> dict:
    room = ctx["room"]
    messages: list[MatchChatMessages] = []
    if _can_read(ctx):
        query = select(MatchChatMessages).where(MatchChatMessages.room_id == room.id)
        if after:
            messages = list((await db.execute(query.where(MatchChatMessages.id > after).order_by(MatchChatMessages.id).limit(HISTORY_LIMIT))).scalars().all())
        else:
            # The latest HISTORY_LIMIT, oldest first.
            newest = (await db.execute(query.order_by(MatchChatMessages.id.desc()).limit(HISTORY_LIMIT))).scalars().all()
            messages = list(reversed(newest))

    admins = (await db.execute(
        select(MatchChatAdmins).where(MatchChatAdmins.room_id == room.id).order_by(MatchChatAdmins.joined_at)
    )).scalars().all()

    return {
        "role": ctx["role"],
        "open": ctx["open"],
        "closedReason": None if ctx["open"] else "This chat opens once both sides of the match are decided.",
        "joined": ctx["joined"],
        "canRead": _can_read(ctx),
        "canWrite": _can_write(ctx),
        "canRequestAdmin": ctx["role"] == "player" and ctx["open"] and not room.admin_requested_at and not admins,
        "canJoin": ctx["is_manager"] and not ctx["joined"],
        "adminRequested": room.admin_requested_at is not None,
        "adminRequestedAt": _iso(room.admin_requested_at),
        "adminRequestReason": room.admin_request_reason,
        "admins": [{"name": a.name, "joinedAt": _iso(a.joined_at)} for a in admins],
        "expiresAt": _iso(room.expires_at),
        "messages": [_message_out(m, user_id) for m in messages],
    }

async def _with_retry(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    """
    _context, run again once if two first looks raced to create the same room.
    """

    try:
        ctx = await _context(user_id, public_id, match_id, db)
        await db.commit()
        return ctx
    except IntegrityError:
        await db.rollback()
        ctx = await _context(user_id, public_id, match_id, db)
        await db.commit()
        return ctx

# ---- routes ----------------------------------------------------------------

async def get_chat(user_id: str, public_id: str, match_id: str, after: int | None, db: AsyncSession) -> dict:
    ctx = await _with_retry(user_id, public_id, match_id, db)
    return await _state(ctx, user_id, after, db)

async def send_message(user_id: str, public_id: str, match_id: str, body: str, openai: AsyncOpenAI, db: AsyncSession) -> dict:
    ctx = await _with_retry(user_id, public_id, match_id, db)
    if not ctx["open"]:
        raise HTTPException(status_code=409, detail="This chat opens once both sides of the match are decided.")
    if not _can_write(ctx):
        raise HTTPException(status_code=403, detail="Join the chat first to take part.")

    reason = await screen_message(body, openai)
    if reason:
        raise HTTPException(status_code=422, detail=reason)

    prefer = ctx["mine"] if ctx["role"] == "admin" else None
    message = MatchChatMessages(
        room_id = ctx["room"].id, kind = "user", sender_account_id = user_id,
        sender_name = await _display_name(user_id, db, prefer), sender_role = ctx["role"], body = body,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return _message_out(message, user_id)

async def request_admin(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    ctx = await _with_retry(user_id, public_id, match_id, db)
    room: MatchChatRooms = ctx["room"]
    if ctx["role"] != "player":
        raise HTTPException(status_code=403, detail="Only the players in this match can request an admin.")
    if not ctx["open"]:
        raise HTTPException(status_code=409, detail="This chat opens once both sides of the match are decided.")

    admins = (await db.execute(select(MatchChatAdmins.id).where(MatchChatAdmins.room_id == room.id))).scalars().all()
    if admins:
        raise HTTPException(status_code=409, detail="An admin is already in this chat.")
    if room.admin_requested_at:
        raise HTTPException(status_code=409, detail="An admin has already been requested for this chat.")

    event: BracketEvents = ctx["event"]
    name = await _display_name(user_id, db)
    chat_rooms.invite_admin(db, room, "requested", f"{name} requested an admin to join this chat.")

    entries = {str(e.id): e for e in await _entries(event.id, db)}
    a, b = entries.get(ctx["match"]["a"]), entries.get(ctx["match"]["b"])
    notify(db, await _manager_accounts(event, db), "chat_admin_requested", {
        "event_id": event.public_id, "event_name": event.name, "match_id": room.match_id,
        "opponent": f"{a.name if a else '?'} vs {b.name if b else '?'}", "reason": "requested", "host": True,
    })
    await db.commit()
    return await _state(ctx, user_id, None, db)

async def join_chat(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    ctx = await _with_retry(user_id, public_id, match_id, db)
    if not ctx["is_manager"]:
        raise HTTPException(status_code=403, detail="Only the host or an event admin can join.")

    if not ctx["joined"]:
        name = await _display_name(user_id, db, ctx["mine"])
        db.add(MatchChatAdmins(room_id = ctx["room"].id, account_id = user_id, name = name))
        chat_rooms.add_system_message(db, ctx["room"], f"Admin {name} joined the chat.")
        try:
            await db.commit()
        except IntegrityError:
            # Joined from two tabs at once: the other one won.
            await db.rollback()
        ctx = await _context(user_id, public_id, match_id, db)

    return await _state(ctx, user_id, None, db)
