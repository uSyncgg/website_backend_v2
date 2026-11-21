import uuid

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.brackets import Notifications

# Account notifications for the navbar bell. Team invites aren't stored here (they're their own
# table and shown alongside); these are everything else. Types and their `data`:
#
#   team_joined       { team_id, team_name, who }                     someone accepted an invite to your team
#   team_signed_up    { event_id, event_name, team_name }             your entry has its full roster again
#   team_pending      { event_id, event_name, team_name, have, need }  your entry lost a player; pick another (it keeps its spot)
#   entry_dropped     { event_id, event_name, team_name }             the bracket was published while your entry was still short
#   event_delayed     { event_id, event_name, starts_at, reason }
#   match_ready       { event_id, event_name, match_id, opponent }    you have a match to play
#   opponent_reported { event_id, event_name, match_id, opponent }    the other side reported a score - confirm yours
#   score_disputed    { event_id, event_name, match_id, opponent }    the two reports disagree; the host decides
#   no_show_reported  { event_id, event_name, match_id, opponent }        you were reported as a no-show
#   issue_opened      { event_id, event_name, issue_type, label, match_id, summary }   host / admins: something's in the queue
#   admin_joined      { event_id, event_name, player }                 your admin invite was accepted
#   team_ownership    { team_id, team_name }                            you now own a team
#
# Each user reads only their own rows. The browser gets them live over Supabase Realtime (the
# notifications_select_own RLS policy), and through GET /notifications on load. Reading or
# dismissing one marks it read; read notifications are deleted 90 days later.
#
# Helpers here only add rows to the session; the caller commits with the rest of its change.

NOTIFICATION_LIMIT = 50
RETENTION_DAYS = 90

def notify(db: AsyncSession, account_ids, type_: str, data: dict) -> None:
    for account_id in {str(a) for a in account_ids if a}:
        db.add(Notifications(id = uuid.uuid4(), account_id = account_id, type = type_, data = data))

async def list_notifications(user_id: str, db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        select(Notifications)
        .where(Notifications.account_id == user_id)
        .order_by(Notifications.created_at.desc())
        .limit(NOTIFICATION_LIMIT)
    )).scalars().all()

    return [
        {"id": str(n.id), "type": n.type, "data": n.data, "read": n.read, "created_at": n.created_at}
        for n in rows
    ]

async def mark_read(user_id: str, ids: list[uuid.UUID] | None, db: AsyncSession) -> list[dict]:
    """
    Marks the given notifications (or all of them when `ids` is None) as read.
    """

    query = update(Notifications).where(Notifications.account_id == user_id).where(Notifications.read.is_(False)) \
        .values(read = True, read_at = datetime.now(timezone.utc))

    if ids is not None:
        query = query.where(Notifications.id.in_(ids))

    await db.execute(query)
    await db.commit()

    return await list_notifications(user_id, db)

async def delete_old_read(db: AsyncSession) -> int:
    """
    Deletes notifications read more than 90 days ago (the background sweeper; the caller commits).
    """

    cutoff = datetime.now(timezone.utc) - timedelta(days = RETENTION_DAYS)
    result = await db.execute(delete(Notifications).where(Notifications.read.is_(True)).where(Notifications.read_at < cutoff))
    return result.rowcount or 0
