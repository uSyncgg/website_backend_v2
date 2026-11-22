import asyncio
import logging
import os

import httpx
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Sends chat events to browsers over Supabase Realtime Broadcast, on the private channel
# chat:{chat_id} (docs/adr/0002-chat-realtime-via-broadcast.md). Only open chat members can
# listen (the realtime.messages policy in migration a7c3e9f1b2d4); only this backend can send.
#
# Code never broadcasts directly. It calls queue() inside its transaction, and the queued events
# go out only once that transaction commits - so a rolled-back message is never announced. A
# broadcast that fails is logged and dropped: browsers fetch anything they missed over REST
# (GET /chats/{id}/messages?after=) when they open a chat or reconnect.

_INFO_KEY = "chat_broadcasts"
_tasks: set = set()  # keeps fire-and-forget sends alive until they finish

def topic(chat_id) -> str:
    return f"chat:{chat_id}"

def queue(db: AsyncSession, chat_id, kind: str, payload: dict) -> None:
    """
    Broadcasts {type: kind, ...payload} on the chat's channel once db's transaction commits.
    """

    db.sync_session.info.setdefault(_INFO_KEY, []).append((topic(chat_id), kind, {"type": kind, **payload}))

async def _send(messages: list[tuple[str, str, dict]]) -> None:
    body = {"messages": [{"topic": t, "event": kind, "payload": payload, "private": True} for t, kind, payload in messages]}
    try:
        async with httpx.AsyncClient(timeout = 5.0) as client:
            response = await client.post(
                f"{os.getenv('SUPABASE_URL', '').rstrip('/')}/realtime/v1/api/broadcast",
                json = body, headers = {"apikey": os.getenv("SUPABASE_SECRET_KEY", "")},
            )
        if response.status_code >= 300:
            logger.warning("Chat broadcast refused (%s): %s", response.status_code, response.text[:200])
    except httpx.HTTPError as exc:
        logger.warning("Chat broadcast failed: %s", exc)

@event.listens_for(Session, "after_commit")
def _send_after_commit(session: Session) -> None:
    messages = session.info.pop(_INFO_KEY, None)
    if not messages:
        return
    task = asyncio.get_running_loop().create_task(_send(messages))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop(_INFO_KEY, None)
