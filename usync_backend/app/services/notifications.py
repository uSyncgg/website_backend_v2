import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.brackets import Notifications

# Account notifications for the navbar bell. Team invites aren't stored here (they're their own
# table and shown alongside); these are everything else. Types and their `data`:
#
#   team_joined       { team_id, team_name, who }                     someone accepted an invite to your team
#   team_signed_up    { event_id, event_name, team_name }             your pending team now has enough players
#   team_pending      { event_id, event_name, team_name, have, need }  your team dropped below the team size
#   event_delayed     { event_id, event_name, starts_at, reason }
#   match_ready       { event_id, event_name, match_id, opponent }    you have a match to play
#   opponent_reported { event_id, event_name, match_id, opponent }    the other side reported a score - confirm yours
#   score_disputed    { event_id, event_name, match_id, opponent }    the two reports disagree; the host decides
#   no_show_reported  { event_id, event_name, match_id, opponent, against_you }
#   chat_admin_requested { event_id, event_name, match_id, opponent, reason, host }  a player asked for an admin in a match chat
#
# Helpers here only add rows to the session; the caller commits with the rest of its change.

NOTIFICATION_LIMIT = 50

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

    query = update(Notifications).where(Notifications.account_id == user_id).values(read = True)

    if ids is not None:
        query = query.where(Notifications.id.in_(ids))

    await db.execute(query)
    await db.commit()

    return await list_notifications(user_id, db)
