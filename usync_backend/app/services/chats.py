import uuid

from datetime import datetime, timedelta, timezone
from better_profanity import profanity
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.brackets import BracketEntries, BracketEvents, BracketMatches
from app.models.chat import Chats, ChatMembers, ChatMessages, MatchChats
from app.models.users import Profiles
from app.services import chat_broadcast

# Chats in general (GLOSSARY.md "Chat"): listing a member's open chats, reading and sending
# messages, and marking them read. What's specific to one kind of chat - when a match chat opens
# and closes, who may join it - lives in that kind's module (app.services.match_chats).
#
# Members only: anyone else gets a 403, and once a chat closes everyone gets a 410. Messages are
# plain text, profanity masked (the original is kept for uSync staff only), never edited. Each
# member may send 5 messages in 10 seconds and 20 a minute in a chat; a chat holds at most 1000.
# Sends take the chat's row lock, so the limits are exact even with two tabs sending at once.

MAX_LENGTH = 500
MAX_MESSAGES_PER_CHAT = 1000
RATE_LIMITS = ((10, 5), (60, 20))  # (seconds, messages) per member per chat

profanity.load_censor_words()

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

def mask(text: str) -> tuple[str, str | None]:
    """
    (what members see, what was typed if masking changed it).
    """

    masked = profanity.censor(text, "*")[:MAX_LENGTH]
    return masked, (text if masked != text else None)

def message_out(message: ChatMessages, sender: Profiles | None) -> dict:
    return {
        "id": message.id,
        "senderProfileId": str(message.sender_profile_id) if message.sender_profile_id else None,
        "senderName": sender.username if sender else None,
        "senderRole": message.sender_role,
        "body": message.body,
        "createdAt": _iso(message.created_at),
    }

async def member_of(user_id: str, chat_id, db: AsyncSession) -> ChatMembers:
    """
    The signed-in account's membership of the chat (its player one, if it has several), or a 403.
    """

    rows = (await db.execute(
        select(ChatMembers).where(ChatMembers.chat_id == chat_id).where(ChatMembers.account_id == user_id)
    )).scalars().all()
    if not rows:
        raise HTTPException(status_code=403, detail="You're not in this chat.")
    return min(rows, key = lambda m: m.role != "player")

async def open_chat(chat_id, db: AsyncSession, lock: bool = False) -> Chats:
    chat = await db.get(Chats, chat_id, with_for_update = lock)
    if chat is None or chat.closed_at is not None:
        raise HTTPException(status_code=410, detail="This chat has closed.")
    return chat

async def post(db: AsyncSession, chat_id, body: str, sender: ChatMembers | None = None, sender_profile: Profiles | None = None) -> dict:
    """
    Adds a message (a system one when there's no sender) and queues its broadcast for after the
    commit. The caller checks the chat is open and commits.
    """

    masked, original = mask(body) if sender else (body[:MAX_LENGTH], None)
    message = ChatMessages(
        chat_id = chat_id, sender_profile_id = sender.profile_id if sender else None,
        sender_role = sender.role if sender else "system", body = masked, original_body = original, created_at = _now(),
    )
    db.add(message)
    await db.flush()
    if sender:
        sender.last_read_message_id = message.id
    out = message_out(message, sender_profile)
    chat_broadcast.queue(db, chat_id, "message", {"message": out})
    return out

def close(db: AsyncSession, chat: Chats) -> None:
    """
    Closes a chat for good and tells anyone listening. The caller commits.
    """

    chat.closed_at = _now()
    chat_broadcast.queue(db, chat.id, "closed", {})

# ---- members -------------------------------------------------------------------

async def send(user_id: str, chat_id: uuid.UUID, body: str, db: AsyncSession) -> dict:
    me = await member_of(user_id, chat_id, db)
    await open_chat(chat_id, db, lock = True)
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write a message first.")

    now = _now()
    for seconds, limit in RATE_LIMITS:
        recent = (await db.execute(
            select(func.count()).select_from(ChatMessages).where(ChatMessages.chat_id == chat_id)
            .where(ChatMessages.sender_profile_id == me.profile_id).where(ChatMessages.created_at > now - timedelta(seconds = seconds))
        )).scalar()
        if recent >= limit:
            raise HTTPException(status_code=429, detail="You're sending messages too fast. Wait a moment.", headers = {"Retry-After": str(seconds)})
    total = (await db.execute(select(func.count()).select_from(ChatMessages).where(ChatMessages.chat_id == chat_id))).scalar()
    if total >= MAX_MESSAGES_PER_CHAT:
        raise HTTPException(status_code=409, detail="This chat has reached its message limit.")

    out = await post(db, chat_id, text, me, await db.get(Profiles, me.profile_id))
    await db.commit()
    return out

async def messages(user_id: str, chat_id: uuid.UUID, after: int, db: AsyncSession) -> list[dict]:
    """
    The chat's messages after the given id (0 = from the start), oldest first.
    """

    await member_of(user_id, chat_id, db)
    await open_chat(chat_id, db)
    rows = (await db.execute(
        select(ChatMessages, Profiles).outerjoin(Profiles, Profiles.id == ChatMessages.sender_profile_id)
        .where(ChatMessages.chat_id == chat_id).where(ChatMessages.id > after)
        .order_by(ChatMessages.id).limit(MAX_MESSAGES_PER_CHAT)
    )).all()
    return [message_out(m, p) for m, p in rows]

async def mark_read(user_id: str, chat_id: uuid.UUID, last_message_id: int, db: AsyncSession) -> None:
    await member_of(user_id, chat_id, db)
    await db.execute(
        update(ChatMembers).where(ChatMembers.chat_id == chat_id).where(ChatMembers.account_id == user_id)
        .values(last_read_message_id = func.greatest(func.coalesce(ChatMembers.last_read_message_id, 0), last_message_id))
    )
    await db.commit()

async def my_chats(user_id: str, db: AsyncSession, chat_ids: list | None = None) -> list[dict]:
    """
    The signed-in account's open chats, the most recently active first, each with its title and
    whether it has messages they haven't read.
    """

    query = (
        select(Chats, ChatMembers).join(ChatMembers, ChatMembers.chat_id == Chats.id)
        .where(ChatMembers.account_id == user_id).where(Chats.closed_at.is_(None))
    )
    if chat_ids is not None:
        query = query.where(Chats.id.in_(chat_ids))
    mine: dict = {}
    for chat, member in (await db.execute(query)).all():
        if chat.id not in mine or member.role == "player":
            mine[chat.id] = (chat, member)
    if not mine:
        return []

    last = dict((await db.execute(
        select(ChatMessages.chat_id, func.max(ChatMessages.id)).where(ChatMessages.chat_id.in_(mine)).group_by(ChatMessages.chat_id)
    )).all())
    last_at = dict((await db.execute(
        select(ChatMessages.chat_id, ChatMessages.created_at).where(ChatMessages.id.in_(list(last.values())))
    )).all()) if last else {}
    titles = await _match_titles(list(mine), db)

    out = []
    for chat_id, (chat, member) in mine.items():
        title = titles.get(chat_id, {})
        out.append({
            "id": str(chat_id),
            "kind": chat.kind,
            "role": member.role,
            "title": title.get("title", "Chat"),
            "subtitle": title.get("subtitle"),
            "eventPublicId": title.get("eventPublicId"),
            "matchCode": title.get("matchCode"),
            "hasUnread": (last.get(chat_id) or 0) > (member.last_read_message_id or 0),
            "lastMessageAt": _iso(last_at.get(chat_id)),
            "closesAt": _iso(chat.closes_at),
            "openedAt": _iso(chat.opened_at),
        })
    out.sort(key = lambda c: c["lastMessageAt"] or c["openedAt"], reverse = True)
    return out

async def _match_titles(chat_ids: list, db: AsyncSession) -> dict:
    """
    { chat id: title parts } for the match chats among them: "Alpha vs Bravo", "Winter Cup · r1m2".
    """

    rows = (await db.execute(
        select(MatchChats.chat_id, BracketMatches, BracketEvents)
        .join(BracketMatches, BracketMatches.id == MatchChats.match_id)
        .join(BracketEvents, BracketEvents.id == MatchChats.event_id)
        .where(MatchChats.chat_id.in_(chat_ids))
    )).all()
    entry_ids = {x for _, m, _ in rows for x in (m.entry_a_id, m.entry_b_id) if x}
    names = dict((await db.execute(select(BracketEntries.id, BracketEntries.name).where(BracketEntries.id.in_(entry_ids)))).all()) if entry_ids else {}
    return {
        chat_id: {
            "title": f"{names.get(m.entry_a_id, 'TBD')} vs {names.get(m.entry_b_id, 'TBD')}",
            "subtitle": f"{e.name} · {m.code.upper()}",
            "eventPublicId": e.public_id,
            "matchCode": m.code,
        }
        for chat_id, m, e in rows
    }
