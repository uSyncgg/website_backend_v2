import uuid

from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.brackets import (
    BracketEntries, BracketEntryPlayers, BracketEvents, BracketMatches, Brackets, EventIssues, MatchReports,
)
from app.models.chat import Chats, ChatMembers, MatchChats
from app.models.users import Profiles
from app.services import bracket_access as access
from app.services import chats
from app.services import event_issues as issues

# Match chats (GLOSSARY.md "Match Chat"): one per bracket match, between the picked players of
# both sides, which the event's host and accepted admins can join.
#
# - Opens when both sides are known (the match becomes ready). Byes and empty matches never get
#   one. If a ruling changes who plays a match whose chat is open, that chat was for a pairing
#   that no longer exists: it's deleted and a new one opens for the new sides.
# - Closes for good 15 minutes (the chat grace period) after the match's dispute window ends and
#   nothing about the match is left in the admin queue. The dispute window ends 5 minutes after
#   the result, when the next match gets a report, or at a ruling, whichever is first; a
#   dispute or admin call settled later pushes the close back to 15 minutes after it's settled.
#   Members are warned 2 minutes before. The background sweeper works this out every minute.
# - A match that goes back to having an unknown side loses its chat (it's deleted).

GRACE = timedelta(minutes = 15)
WARN_BEFORE = timedelta(minutes = 2)

def _now() -> datetime:
    return datetime.now(timezone.utc)

async def _picked(db: AsyncSession, entry_ids) -> list[tuple]:
    """
    [(entry id, profile id, account id)] for the picked players of the entries.
    """

    return list((await db.execute(
        select(BracketEntryPlayers.entry_id, Profiles.id, Profiles.account_id)
        .join(Profiles, Profiles.id == BracketEntryPlayers.profile_id)
        .where(BracketEntryPlayers.entry_id.in_(list(entry_ids)))
    )).all())

async def _delete(db: AsyncSession, chat: Chats) -> None:
    chats.close(db, chat)  # tells anyone listening before it goes
    await db.delete(chat)

async def open_for_ready(db: AsyncSession, event: BracketEvents, ready: list[BracketMatches]) -> None:
    """
    Opens a chat for every match that just became ready, replacing one whose sides changed.
    The caller commits.
    """

    for match in ready:
        if not (match.entry_a_id and match.entry_b_id):
            continue
        picked = await _picked(db, (match.entry_a_id, match.entry_b_id))
        existing = (await db.execute(
            select(Chats).join(MatchChats, MatchChats.chat_id == Chats.id).where(MatchChats.match_id == match.id)
        )).scalars().first()
        if existing:
            players = set((await db.execute(
                select(ChatMembers.profile_id).where(ChatMembers.chat_id == existing.id).where(ChatMembers.role == "player")
            )).scalars().all())
            if players == {profile_id for _, profile_id, _ in picked}:
                continue
            await _delete(db, existing)
            await db.flush()
        chat = Chats(id = uuid.uuid4(), kind = "match")
        db.add(chat)
        await db.flush()
        db.add(MatchChats(chat_id = chat.id, match_id = match.id, event_id = event.id))
        db.add_all([
            ChatMembers(chat_id = chat.id, profile_id = profile_id, account_id = account_id, role = "player")
            for _, profile_id, account_id in picked
        ])
    await db.flush()

async def _match_and_chat(public_id: str, code: str, db: AsyncSession, event: BracketEvents | None = None):
    event = event or await access.event_by_public_id(public_id, db)
    row = (await db.execute(
        select(BracketMatches, Chats)
        .join(Brackets, Brackets.id == BracketMatches.bracket_id)
        .outerjoin(MatchChats, MatchChats.match_id == BracketMatches.id)
        .outerjoin(Chats, Chats.id == MatchChats.chat_id)
        .where(Brackets.event_id == event.id).where(BracketMatches.code == code)
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Match not found.")
    match, chat = row
    if chat is None:
        raise HTTPException(status_code=404, detail="This match has no chat.")
    if chat.closed_at is not None:
        raise HTTPException(status_code=410, detail="This chat has closed.")
    return event, match, chat

async def join(user_id: str, public_id: str, code: str, db: AsyncSession) -> dict:
    """
    The host or an accepted event admin joins a match chat, seeing its whole history.
    """

    event = await access.managed_event(user_id, public_id, db)
    _, _, chat = await _match_and_chat(public_id, code, db, event)
    await chats.open_chat(chat.id, db, lock = True)
    profile_id = await issues.acting_profile(user_id, event, db)
    if profile_id is None:
        raise HTTPException(status_code=403, detail="Only the host or an event admin can join a match chat.")
    already = await db.get(ChatMembers, (chat.id, profile_id))
    if already is None:
        profile = await db.get(Profiles, profile_id)
        db.add(ChatMembers(chat_id = chat.id, profile_id = profile_id, account_id = profile.account_id, role = "admin"))
        await db.flush()
        await chats.post(db, chat.id, f"{profile.username} (Event Admin) joined the chat.")
        await db.commit()
    return (await chats.my_chats(user_id, db, [chat.id]))[0]

async def call_admin(user_id: str, public_id: str, code: str, db: AsyncSession) -> dict:
    """
    A player in the match asks for the host or an admin: it goes in the event's admin queue
    (one open call per match) and the chat is told.
    """

    event, match, chat = await _match_and_chat(public_id, code, db)
    me = await chats.member_of(user_id, chat.id, db)
    if me.role != "player":
        raise HTTPException(status_code=403, detail="Only the players in this match can call an admin.")
    await chats.open_chat(chat.id, db, lock = True)
    entry = (await db.execute(
        select(BracketEntries).join(BracketEntryPlayers, BracketEntryPlayers.entry_id == BracketEntries.id)
        .where(BracketEntryPlayers.profile_id == me.profile_id)
        .where(BracketEntries.id.in_([match.entry_a_id, match.entry_b_id]))
    )).scalars().first()
    profile = await db.get(Profiles, me.profile_id)
    try:
        issue = await issues.open_issue(db, event, match, "admin_requested", entry, {
            "summary": f"{profile.username} ({entry.name if entry else 'a player'}) asked for an admin in the match chat.",
        })
    except IntegrityError:
        issue = None
    if issue is None:
        await db.rollback()
        raise HTTPException(status_code=409, detail="An admin has already been called for this match.")
    await chats.post(db, chat.id, f"{profile.username} called an admin.")
    await db.commit()
    return (await chats.my_chats(user_id, db, [chat.id]))[0]

async def open_chat_ids(event_id, db: AsyncSession) -> dict:
    """
    { match id: open chat id } for the event, for the match views.
    """

    return dict((await db.execute(
        select(MatchChats.match_id, MatchChats.chat_id).join(Chats, Chats.id == MatchChats.chat_id)
        .where(MatchChats.event_id == event_id).where(Chats.closed_at.is_(None))
    )).all())

# ---- closing (the background sweeper) --------------------------------------------------

async def _closes_at(db: AsyncSession, match: BracketMatches) -> datetime | None:
    """
    When the match's chat should close, or None while it can't yet: the match isn't decided, or
    something about it is still in the admin queue.
    """

    from app.services.matches import DISPUTE_WINDOW_MINUTES

    if match.status != "complete" or match.completed_at is None:
        return None
    unsettled = (await db.execute(
        select(EventIssues.id).where(EventIssues.match_id == match.id).where(EventIssues.status.in_(("open", "claimed")))
    )).first()
    if unsettled:
        return None

    end = match.completed_at if match.ruled else match.completed_at + timedelta(minutes = DISPUTE_WINDOW_MINUTES)
    following = select(BracketMatches.id).where(BracketMatches.id.in_([x for x in (match.next_match_id, match.loser_next_match_id) if x]))
    if match.code == "gf1":
        following = following.union(select(BracketMatches.id).where(BracketMatches.bracket_id == match.bracket_id).where(BracketMatches.code == "gf2"))
    following_ids = list((await db.execute(following)).scalars().all())
    if following_ids:
        first_report = (await db.execute(select(func.min(MatchReports.created_at)).where(MatchReports.match_id.in_(following_ids)))).scalar()
        first_done = (await db.execute(select(func.min(BracketMatches.completed_at)).where(BracketMatches.id.in_(following_ids)))).scalar()
        for at in (first_report, first_done):
            if at is not None:
                end = min(end, max(at, match.completed_at))
    settled = (await db.execute(select(func.max(EventIssues.closed_at)).where(EventIssues.match_id == match.id))).scalar()
    if settled is not None:
        end = max(end, settled)
    return end + GRACE

async def sweep(db: AsyncSession) -> int:
    """
    Works out every open match chat's close time; warns members 2 minutes before and closes the
    chat when it's reached. Deletes a chat whose match no longer has both sides. The caller
    commits. Returns how many chats closed.
    """

    now = _now()
    closed = 0
    rows = (await db.execute(
        select(Chats, BracketMatches).join(MatchChats, MatchChats.chat_id == Chats.id)
        .join(BracketMatches, BracketMatches.id == MatchChats.match_id)
        .where(Chats.closed_at.is_(None))
        .with_for_update(of = Chats, skip_locked = True)
    )).all()
    for chat, match in rows:
        if match.status not in ("ready", "complete"):
            await _delete(db, chat)
            continue
        closes = await _closes_at(db, match)
        chat.closes_at = closes
        if closes is None:
            chat.closing_warned_at = None
        elif now >= closes:
            chats.close(db, chat)
            closed += 1
        elif now >= closes - WARN_BEFORE and chat.closing_warned_at is None:
            chat.closing_warned_at = now
            await chats.post(db, chat.id, "This chat closes in 2 minutes. Once it closes it can't be opened again.")
    await db.flush()
    return closed
