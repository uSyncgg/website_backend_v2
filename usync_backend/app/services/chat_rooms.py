import asyncio
import logging

from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import AsyncSessionLocal
from app.models.brackets import BracketEvents
from app.models.chat import MatchChatRooms, MatchChatMessages, MatchChatAdmins, ChatReports
from app.services import bracket_engine as engine

logger = logging.getLogger(__name__)

# Match chat rooms: the storage side. Nothing in here knows about HTTP or who is allowed to
# do what - app.services.chat does that. app.services.brackets calls sync_rooms whenever a
# bracket is published or changes, so every match has a room as soon as the bracket is out, and
# reset_bracket calls delete_event_rooms.
#
# Chats are not kept forever. When every match of an event has been played its rooms get an
# expires_at three days out (and turn read-only, see event_finished); an event that never
# finishes is cleaned up 30 days after its start date. The sweeper started in main.py's
# lifespan deletes expired rooms (their messages and admin joins go with them). A result that is
# undone moves the clock back to the 30-day backstop.
#
# Reports filed against messages (app.models.chat.ChatReports) keep their own copy so staff can
# still review them; the sweeper deletes those a year after they were reviewed.

CHAT_RETENTION = timedelta(days = 3)
CHAT_BACKSTOP = timedelta(days = 30)
REPORT_RETENTION = timedelta(days = 365)
CHAT_SWEEP_INTERVAL_SECONDS = 3600
SWEEP_BATCH_SIZE = 200

def _now() -> datetime:
    return datetime.now(timezone.utc)

def event_finished(event: BracketEvents) -> bool:
    """
    Whether every match of a published bracket has been played. Chats turn read-only then.
    """

    if not (event.bracket and event.bracket.get("published")):
        return False
    built = engine.build_bracket(event.bracket["format"], event.bracket["seeds"], event.bracket.get("results", {}))
    done, total = engine.progress(built)
    return total > 0 and done == total

def _playable(match: dict) -> bool:
    return match["status"] not in ("bye", "empty")

async def wipe_room(room: MatchChatRooms, db: AsyncSession) -> None:
    """
    Starts a room over: a different pairing has landed in the match (a result upstream was
    changed), so nothing said between the old pairing may reach the new one.
    """

    await db.execute(delete(MatchChatMessages).where(MatchChatMessages.room_id == room.id))
    await db.execute(delete(MatchChatAdmins).where(MatchChatAdmins.room_id == room.id))
    room.admin_requested_at = None
    room.admin_request_reason = None

async def apply_pairing(room: MatchChatRooms, match: dict, db: AsyncSession) -> bool:
    """
    Records who is in the match. Returns True when the room was wiped because the pairing changed.
    """

    a, b = match["a"], match["b"]
    if not (engine.is_entrant(a) and engine.is_entrant(b)):
        return False

    wiped = False
    if room.side_a is not None and (room.side_a, room.side_b) != (a, b):
        await wipe_room(room, db)
        wiped = True
    room.side_a, room.side_b = a, b
    return wiped

async def sync_rooms(db: AsyncSession, event: BracketEvents) -> dict[str, MatchChatRooms]:
    """
    Makes sure every playable match of a published bracket has a room, keeps each room's pairing
    in step with the bracket, and starts or stops the 3-day deletion clock. The caller commits.

    ::return { match_id: room } for the event
    """

    if not (event.bracket and event.bracket.get("published")):
        return {}

    built = engine.build_bracket(event.bracket["format"], event.bracket["seeds"], event.bracket.get("results", {}))
    rooms = {
        r.match_id: r for r in (await db.execute(
            select(MatchChatRooms).where(MatchChatRooms.event_id == event.id)
        )).scalars().all()
    }

    matches = [m for m in engine.all_matches(built) if _playable(m)]
    for match in matches:
        room = rooms.get(match["id"])
        if room is None:
            room = MatchChatRooms(event_id = event.id, match_id = match["id"])
            db.add(room)
            rooms[match["id"]] = room
        await apply_pairing(room, match, db)

    done, total = engine.progress(built)
    finished = total > 0 and done == total
    deadline = _now() + CHAT_RETENTION
    backstop = event.starts_at + CHAT_BACKSTOP
    for room in rooms.values():
        if finished:
            # Three days from the moment it finished; later syncs never push that back.
            room.expires_at = min(room.expires_at, deadline) if room.expires_at else deadline
        else:
            room.expires_at = backstop

    await db.flush()
    return rooms

async def delete_event_rooms(db: AsyncSession, event: BracketEvents) -> None:
    """
    Removes every room of an event (the bracket was reset). Messages and joins cascade.
    """

    await db.execute(delete(MatchChatRooms).where(MatchChatRooms.event_id == event.id))

async def get_room(db: AsyncSession, event: BracketEvents, match_id: str) -> MatchChatRooms | None:
    """
    The room for one match, creating it (and its siblings) if this is the first look since the
    bracket went live or changed. None when the match isn't playable.
    """

    room = (await db.execute(
        select(MatchChatRooms).where(MatchChatRooms.event_id == event.id).where(MatchChatRooms.match_id == match_id)
    )).scalars().first()
    if room is None:
        room = (await sync_rooms(db, event)).get(match_id)
    return room

def add_system_message(db: AsyncSession, room: MatchChatRooms, body: str) -> MatchChatMessages:
    message = MatchChatMessages(room_id = room.id, kind = "system", body = body)
    db.add(message)
    return message

def invite_admin(db: AsyncSession, room: MatchChatRooms, reason: str, body: str) -> bool:
    """
    Marks the room as wanting an admin and says so in the chat. Does nothing (returns False) if one
    was already asked for, so a dispute on top of a request doesn't post twice.
    """

    if room.admin_requested_at is not None:
        return False
    room.admin_requested_at = _now()
    room.admin_request_reason = reason
    add_system_message(db, room, body)
    return True

# ---- deletion ------------------------------------------------------------------

async def purge_expired(db: AsyncSession) -> int:
    """
    Deletes rooms whose 3 days are up, a batch at a time.

    ::return how many rooms were deleted
    """

    ids = (await db.execute(
        select(MatchChatRooms.id).where(MatchChatRooms.expires_at.is_not(None)).where(MatchChatRooms.expires_at <= _now()).limit(SWEEP_BATCH_SIZE)
    )).scalars().all()
    if not ids:
        return 0
    await db.execute(delete(MatchChatRooms).where(MatchChatRooms.id.in_(ids)))
    await db.commit()
    return len(ids)

async def purge_old_reports(db: AsyncSession) -> int:
    """
    Deletes reports staff finished with more than a year ago. Open reports are never deleted.
    """

    result = await db.execute(
        delete(ChatReports).where(ChatReports.status != "open").where(ChatReports.reviewed_at <= _now() - REPORT_RETENTION)
    )
    await db.commit()
    return result.rowcount or 0

async def run_chat_sweeper() -> None:
    """
    Background loop started in main.py's lifespan: deletes expired chats at startup, then hourly.
    """

    while True:
        try:
            total = 0
            async with AsyncSessionLocal() as db:
                deleted = await purge_expired(db)
                total += deleted
                while deleted == SWEEP_BATCH_SIZE:
                    deleted = await purge_expired(db)
                    total += deleted
                reports = await purge_old_reports(db)
            if total or reports:
                logger.info("Chat sweeper deleted %d expired match chat(s) and %d old report(s)", total, reports)
        except Exception:
            logger.exception("Chat sweeper run failed")

        await asyncio.sleep(CHAT_SWEEP_INTERVAL_SECONDS)
