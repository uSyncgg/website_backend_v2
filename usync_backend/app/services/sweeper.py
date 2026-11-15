import asyncio
import logging

from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select, text
from app.core.db import AsyncSessionLocal
from app.models.teams import TeamInvites
from app.services import event_issues
from app.services import matches
from app.services.notifications import delete_old_read

logger = logging.getLogger(__name__)

# The bracket background job, once a minute:
# - one-sided reports nobody answered for 5 minutes go in the admin queue;
# - admin queue claims older than 10 minutes go back to open, and queue items whose match now has
#   a result close themselves;
# - read notifications older than 90 days, and answered team invites older than 90 days, are
#   deleted.
#
# Each run takes a transaction-level advisory lock first, so with several workers (or several
# servers) only one does the work each minute; the others skip that tick.

SWEEP_INTERVAL_SECONDS = 60
INVITE_RETENTION_DAYS = 90
LOCK_KEY = 74_512_001  # any fixed number unique to this job

async def sweep_once() -> None:
    async with AsyncSessionLocal() as db:
        got_lock = (await db.execute(select(text(f"pg_try_advisory_xact_lock({LOCK_KEY})")))).scalar()
        if not got_lock:
            return
        raised = await matches.flag_one_sided_reports(db)
        await event_issues.housekeep(db)
        removed = await delete_old_read(db)
        cutoff = datetime.now(timezone.utc) - timedelta(days = INVITE_RETENTION_DAYS)
        invites = (await db.execute(
            delete(TeamInvites).where(TeamInvites.status != "pending").where(TeamInvites.responded_at < cutoff)
        )).rowcount or 0
        await db.commit()
        if raised or removed or invites:
            logger.info("Bracket sweep: %s one-sided reports queued, %s notifications and %s invites deleted.", raised, removed, invites)

async def run_bracket_sweeper() -> None:
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Bracket sweep failed.")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
