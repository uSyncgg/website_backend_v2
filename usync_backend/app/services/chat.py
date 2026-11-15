from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI
from app.models.users import Profiles
from app.models.chat import MatchChatRooms, MatchChatMessages, MatchChatAdmins, ChatReports
from app.models.brackets import BracketEvents
from app.services import bracket_engine as engine
from app.services import chat_rooms
from app.services.chat_filters import screen_message
from app.services.notifications import notify
from app.services.brackets import (
    _event_by_public_id, _live_bracket, _built, _entries, _entry_accounts, _manager_accounts,
    _my_profile_ids, _admin_event_ids, _iso,
)

# Match chats: who can read and write, and what can happen in a room besides talking.
#
#   Players - everyone on either side of the match (a solo player, or the team's owner, captains and
#     players) - can read and write whenever both sides are known and the event isn't over.
#   Admins - the host and accepted event admins, treated the same - can't read a chat until they
#     join it. Joining shows them the whole history and posts "<name> joined" for the players;
#     they can leave again ("<name> left"). They can't delete messages, but they can hide a
#     player's message from the players, and that reports it to uSync staff automatically.
#   A player can request an admin: it says so in the chat and every admin gets a notification
#     that leads to the match page. A score dispute or a reported no-show does the same on its own.
#   Anyone in the chat can report a message. Reports go to uSync staff (the admin dashboard's
#     Moderation tab), never to the event's admins.
#   Once every match is played the chat is read-only until it is deleted.
#
# Every message goes through app.services.chat_filters before it is stored, and there are limits on
# how fast and how much one person can send.

HISTORY_LIMIT = 500
ROOM_MESSAGE_CAP = 1000
BURST = (5, 10)        # at most 5 messages in any 10 seconds
PER_MINUTE = (20, 60)  # and 20 in any 60
DUPLICATE_SECONDS = 30
CONTEXT_BEFORE = 12
CONTEXT_AFTER = 3
REPORT_CATEGORIES = ("harassment", "hate", "sexual", "threats", "spam", "other")

ENDED_REASON = "This event has ended, so the chat is read-only."
NOT_OPEN_REASON = "This chat opens once both sides of the match are decided."

def _message_out(m: MatchChatMessages, user_id: str, privileged: bool, can_hide: bool, reported: set) -> dict:
    hidden = m.hidden_at is not None
    mine = bool(m.sender_account_id and str(m.sender_account_id) == str(user_id))
    return {
        "id": m.id,
        "kind": m.kind,
        "sender": {"name": m.sender_name, "role": m.sender_role} if m.kind == "user" else None,
        # A hidden message's text only goes to admins; players get a placeholder.
        "body": m.body if (privileged or not hidden) else "",
        "at": _iso(m.created_at),
        "mine": mine,
        "hidden": hidden,
        "hiddenBy": m.hidden_by_name if (privileged and hidden) else None,
        "canReport": m.kind == "user" and not mine and (privileged or not hidden),
        "canHide": can_hide and m.kind == "user" and m.sender_role == "player" and not hidden,
        "reported": m.id in reported,
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
        "ended": chat_rooms.event_finished(event),
    }

def _can_read(ctx: dict) -> bool:
    return ctx["role"] == "player" or ctx["joined"]

def _can_write(ctx: dict) -> bool:
    return ctx["open"] and not ctx["ended"] and _can_read(ctx)

def _is_privileged(ctx: dict) -> bool:
    """
    An admin who has joined: sees hidden messages and can hide more.
    """

    return ctx["role"] == "admin" and ctx["joined"]

async def _state(ctx: dict, user_id: str, after: int | None, db: AsyncSession) -> dict:
    room = ctx["room"]
    messages: list[MatchChatMessages] = []
    hidden_ids: list[int] = []
    reported: set = set()

    if _can_read(ctx):
        query = select(MatchChatMessages).where(MatchChatMessages.room_id == room.id)
        if after:
            messages = list((await db.execute(query.where(MatchChatMessages.id > after).order_by(MatchChatMessages.id).limit(HISTORY_LIMIT))).scalars().all())
        else:
            # The latest HISTORY_LIMIT, oldest first.
            newest = (await db.execute(query.order_by(MatchChatMessages.id.desc()).limit(HISTORY_LIMIT))).scalars().all()
            messages = list(reversed(newest))

        # Hidden after the reader's last poll still has to reach them, so the ids always come along.
        hidden_ids = list((await db.execute(
            select(MatchChatMessages.id).where(MatchChatMessages.room_id == room.id).where(MatchChatMessages.hidden_at.is_not(None))
        )).scalars().all())
        if messages:
            reported = set((await db.execute(
                select(ChatReports.message_id).where(ChatReports.reporter_account_id == user_id)
                .where(ChatReports.message_id.in_([m.id for m in messages]))
            )).scalars().all())

    admins = (await db.execute(
        select(MatchChatAdmins).where(MatchChatAdmins.room_id == room.id).order_by(MatchChatAdmins.joined_at)
    )).scalars().all()

    privileged = _is_privileged(ctx)
    closed_reason = None if ctx["open"] and not ctx["ended"] else (NOT_OPEN_REASON if not ctx["open"] else ENDED_REASON)

    return {
        "role": ctx["role"],
        "open": ctx["open"],
        "ended": ctx["ended"],
        "closedReason": closed_reason,
        "joined": ctx["joined"],
        "canRead": _can_read(ctx),
        "canWrite": _can_write(ctx),
        "canRequestAdmin": ctx["role"] == "player" and ctx["open"] and not ctx["ended"] and not room.admin_requested_at and not admins,
        "canJoin": ctx["is_manager"] and not ctx["joined"],
        "canLeave": ctx["joined"],
        "adminRequested": room.admin_requested_at is not None,
        "adminRequestedAt": _iso(room.admin_requested_at),
        "adminRequestReason": room.admin_request_reason,
        "admins": [{"name": a.name, "joinedAt": _iso(a.joined_at)} for a in admins],
        "expiresAt": _iso(room.expires_at) if ctx["ended"] else None,
        "hiddenIds": hidden_ids,
        "messages": [_message_out(m, user_id, privileged, privileged, reported) for m in messages],
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

# ---- sending, with limits ----------------------------------------------------

async def _check_limits(ctx: dict, user_id: str, body: str, db: AsyncSession) -> None:
    """
    Keeps one person from flooding a chat: a burst limit, a per-minute limit, no repeating the
    same line, and a cap on the room's total. Times come from the database's clock.
    """

    room = ctx["room"]
    now: datetime = (await db.execute(select(func.now()))).scalar()
    recent = (await db.execute(
        select(MatchChatMessages.body, MatchChatMessages.created_at)
        .where(MatchChatMessages.room_id == room.id).where(MatchChatMessages.kind == "user")
        .where(MatchChatMessages.sender_account_id == user_id)
        .order_by(MatchChatMessages.id.desc()).limit(PER_MINUTE[0] + 1)
    )).all()

    ages = [(now - created).total_seconds() for _, created in recent]
    if sum(1 for a in ages if a <= BURST[1]) >= BURST[0]:
        raise HTTPException(status_code=429, detail="You're sending messages too fast. Wait a few seconds.")
    if sum(1 for a in ages if a <= PER_MINUTE[1]) >= PER_MINUTE[0]:
        raise HTTPException(status_code=429, detail="You've sent a lot of messages in a minute. Take a short break.")
    if recent and recent[0][0].casefold() == body.casefold() and ages[0] <= DUPLICATE_SECONDS:
        raise HTTPException(status_code=429, detail="You just sent that message.")

    if ctx["role"] == "player":
        total = (await db.execute(
            select(func.count()).select_from(MatchChatMessages)
            .where(MatchChatMessages.room_id == room.id).where(MatchChatMessages.kind == "user")
        )).scalar()
        if total >= ROOM_MESSAGE_CAP:
            raise HTTPException(status_code=409, detail="This chat has reached its message limit. Ask an admin to help.")

# ---- routes ----------------------------------------------------------------

async def get_chat(user_id: str, public_id: str, match_id: str, after: int | None, db: AsyncSession) -> dict:
    ctx = await _with_retry(user_id, public_id, match_id, db)
    return await _state(ctx, user_id, after, db)

async def send_message(user_id: str, public_id: str, match_id: str, body: str, openai: AsyncOpenAI, db: AsyncSession) -> dict:
    ctx = await _with_retry(user_id, public_id, match_id, db)
    if not ctx["open"]:
        raise HTTPException(status_code=409, detail=NOT_OPEN_REASON)
    if ctx["ended"]:
        raise HTTPException(status_code=409, detail=ENDED_REASON)
    if not _can_write(ctx):
        raise HTTPException(status_code=403, detail="Join the chat first to take part.")

    await _check_limits(ctx, user_id, body, db)
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
    return _message_out(message, user_id, _is_privileged(ctx), False, set())

async def request_admin(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    ctx = await _with_retry(user_id, public_id, match_id, db)
    room: MatchChatRooms = ctx["room"]
    if ctx["role"] != "player":
        raise HTTPException(status_code=403, detail="Only the players in this match can request an admin.")
    if not ctx["open"]:
        raise HTTPException(status_code=409, detail=NOT_OPEN_REASON)
    if ctx["ended"]:
        raise HTTPException(status_code=409, detail=ENDED_REASON)

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

async def leave_chat(user_id: str, public_id: str, match_id: str, db: AsyncSession) -> dict:
    """
    An admin steps out. They lose the history again until they rejoin, and the players are told.
    """

    ctx = await _with_retry(user_id, public_id, match_id, db)
    if not ctx["joined"]:
        raise HTTPException(status_code=409, detail="You haven't joined this chat.")

    row = (await db.execute(
        select(MatchChatAdmins).where(MatchChatAdmins.room_id == ctx["room"].id).where(MatchChatAdmins.account_id == user_id)
    )).scalars().first()
    if row:
        chat_rooms.add_system_message(db, ctx["room"], f"Admin {row.name} left the chat.")
        await db.delete(row)
        await db.commit()
    ctx["joined"] = False
    return await _state(ctx, user_id, None, db)

# ---- hiding and reporting ----------------------------------------------------

async def _message_in_room(room: MatchChatRooms, message_id: int, db: AsyncSession) -> MatchChatMessages:
    message = (await db.execute(
        select(MatchChatMessages).where(MatchChatMessages.id == message_id).where(MatchChatMessages.room_id == room.id)
    )).scalars().first()
    if not message or message.kind != "user":
        raise HTTPException(status_code=404, detail="Message not found.")
    return message

async def _snapshot(room: MatchChatRooms, message: MatchChatMessages, db: AsyncSession) -> list[dict]:
    """
    The reported message with a few lines around it, copied so staff can still read it after the
    chat is deleted.
    """

    before = (await db.execute(
        select(MatchChatMessages).where(MatchChatMessages.room_id == room.id).where(MatchChatMessages.id <= message.id)
        .order_by(MatchChatMessages.id.desc()).limit(CONTEXT_BEFORE + 1)
    )).scalars().all()
    after = (await db.execute(
        select(MatchChatMessages).where(MatchChatMessages.room_id == room.id).where(MatchChatMessages.id > message.id)
        .order_by(MatchChatMessages.id).limit(CONTEXT_AFTER)
    )).scalars().all()
    return [
        {
            "id": m.id, "kind": m.kind, "name": m.sender_name, "role": m.sender_role, "body": m.body,
            "at": _iso(m.created_at), "hidden": m.hidden_at is not None,
        }
        for m in [*reversed(before), *after]
    ]

async def _file_report(db: AsyncSession, ctx: dict, message: MatchChatMessages, source: str, category: str, note: str | None, reporter_id: str, reporter_name: str) -> ChatReports:
    event: BracketEvents = ctx["event"]
    report = ChatReports(
        source = source, category = category, note = note, reporter_account_id = reporter_id, reporter_name = reporter_name,
        message_id = message.id, room_id = ctx["room"].id, event_public_id = event.public_id, event_name = event.name,
        match_id = ctx["room"].match_id, sender_account_id = message.sender_account_id, sender_name = message.sender_name,
        sender_role = message.sender_role, message_body = message.body, context = await _snapshot(ctx["room"], message, db),
    )
    db.add(report)
    return report

async def hide_message(user_id: str, public_id: str, match_id: str, message_id: int, db: AsyncSession) -> dict:
    """
    An admin hides a player's message from the players (the text stays for admins and staff) and it
    is reported to uSync staff automatically. Admins can't hide or delete their own or each other's.
    """

    ctx = await _with_retry(user_id, public_id, match_id, db)
    if not _is_privileged(ctx):
        raise HTTPException(status_code=403, detail="Join the chat to moderate it.")

    message = await _message_in_room(ctx["room"], message_id, db)
    if message.sender_role != "player":
        raise HTTPException(status_code=403, detail="Admins can only hide players' messages.")
    if message.hidden_at is not None:
        raise HTTPException(status_code=409, detail="That message is already hidden.")

    name = await _display_name(user_id, db, ctx["mine"])
    message.hidden_at = func.now()
    message.hidden_by_account_id = user_id
    message.hidden_by_name = name
    await _file_report(db, ctx, message, "admin_hide", "other", None, user_id, name)
    chat_rooms.add_system_message(db, ctx["room"], "An admin hid a message. uSync staff will review it.")
    await db.commit()
    return await _state(ctx, user_id, None, db)

async def report_message(user_id: str, public_id: str, match_id: str, message_id: int, category: str, note: str | None, db: AsyncSession) -> dict:
    """
    Anyone who can read the chat reports a message to uSync staff. The event's admins never see it.
    """

    ctx = await _with_retry(user_id, public_id, match_id, db)
    if not _can_read(ctx):
        raise HTTPException(status_code=403, detail="Join the chat to report a message.")

    message = await _message_in_room(ctx["room"], message_id, db)
    if message.sender_account_id and str(message.sender_account_id) == str(user_id):
        raise HTTPException(status_code=409, detail="You can't report your own message.")
    if message.hidden_at is not None and not _is_privileged(ctx):
        raise HTTPException(status_code=404, detail="Message not found.")

    name = await _display_name(user_id, db, ctx["mine"] if ctx["role"] == "admin" else None)
    report = await _file_report(db, ctx, message, "player", category, (note or "").strip() or None, user_id, name)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="You already reported that message.")
    return {"reported": True, "reportId": str(report.id)}
