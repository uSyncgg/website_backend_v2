import uuid

from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.brackets import BracketEvents, BracketEventAdmins, BracketMatches, BracketEntries, EventIssues, EventIssueActions
from app.services import bracket_access as access
from app.services.notifications import notify

# The per-event admin queue. Anything that needs the host or an admin - a disputed score, a
# one-sided report nobody answered for 5 minutes, a no-show, a dispute after a team advanced -
# goes in as an issue, oldest first, and the host and admins are notified. Only they see it.
#
# Every state change is one conditional UPDATE, so concurrent clicks can't both win:
# - claim ("go to issue") only takes an OPEN issue;
# - dismiss takes an open OR claimed issue - dismissal always beats a claim;
# - a claim not closed within 10 minutes goes back to open;
# - an issue whose match has since been decided closes itself (resolved by the system).
# Each change is logged in event_issue_actions with who and when (no one = the system).

CLAIM_MINUTES = 10
LABELS = {
    "dispute": "Disputed score",
    "one_sided_report": "One-sided report",
    "no_show": "No-show reported",
    "post_advancement_dispute": "Dispute after advancing",
}
# Types that are settled once their match has a result.
SETTLED_BY_RESULT = ("dispute", "one_sided_report", "no_show")

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _log(db: AsyncSession, issue_id, profile_id, action: str) -> None:
    db.add(EventIssueActions(id = uuid.uuid4(), issue_id = issue_id, profile_id = profile_id, action = action))

async def open_issue(db: AsyncSession, event: BracketEvents, match: BracketMatches, type_: str,
                     raised_by: BracketEntries | None, data: dict) -> EventIssues | None:
    """
    Puts an issue in the event's queue and tells the host and admins. Does nothing if the same
    kind of issue is already open for the match. The caller commits.
    """

    existing = (await db.execute(
        select(EventIssues.id).where(EventIssues.match_id == match.id).where(EventIssues.type == type_)
        .where(EventIssues.status.in_(("open", "claimed")))
    )).first()
    if existing and type_ != "post_advancement_dispute":
        return None
    issue = EventIssues(id = uuid.uuid4(), event_id = event.id, match_id = match.id, type = type_,
                        raised_by_entry_id = raised_by.id if raised_by else None, data = data, status = "open")
    db.add(issue)
    await db.flush()
    notify(db, await access.manager_accounts(event, db), "issue_opened", {
        "event_id": event.public_id, "event_name": event.name, "issue_type": type_, "label": LABELS[type_],
        "match_id": match.code, "summary": data.get("summary", ""),
    })
    return issue

async def resolve_for_match(db: AsyncSession, match: BracketMatches, profile_id, types: tuple | None = None) -> None:
    """
    Closes the match's open or claimed issues because someone acted on the match (a ruling, or the
    players agreeing). profile_id None means the system did it.
    """

    query = select(EventIssues).where(EventIssues.match_id == match.id).where(EventIssues.status.in_(("open", "claimed")))
    if types:
        query = query.where(EventIssues.type.in_(types))
    for issue in (await db.execute(query.with_for_update())).scalars().all():
        issue.status, issue.closed_by_profile_id, issue.closed_at = "resolved", profile_id, _now()
        _log(db, issue.id, profile_id, "resolve" if profile_id else "auto_resolve")

async def housekeep(db: AsyncSession, event_id=None) -> None:
    """
    Claims older than 10 minutes go back to open; issues whose match now has a result close
    themselves. Runs before the queue is shown and from the background sweeper. Caller commits.
    """

    cutoff = _now() - timedelta(minutes = CLAIM_MINUTES)
    stale = select(EventIssues).where(EventIssues.status == "claimed").where(EventIssues.claimed_at < cutoff)
    settled = (
        select(EventIssues).join(BracketMatches, BracketMatches.id == EventIssues.match_id)
        .where(EventIssues.status.in_(("open", "claimed"))).where(EventIssues.type.in_(SETTLED_BY_RESULT))
        .where(BracketMatches.status == "complete")
    )
    if event_id is not None:
        stale, settled = stale.where(EventIssues.event_id == event_id), settled.where(EventIssues.event_id == event_id)
    for issue in (await db.execute(settled.with_for_update(of = EventIssues, skip_locked = True))).scalars().all():
        issue.status, issue.closed_at = "resolved", _now()
        _log(db, issue.id, None, "auto_resolve")
    for issue in (await db.execute(stale.with_for_update(skip_locked = True))).scalars().all():
        issue.status, issue.claimed_by_profile_id, issue.claimed_at = "open", None, None
        _log(db, issue.id, None, "release")

def _issue_out(issue: EventIssues, match: BracketMatches | None, names: dict) -> dict:
    return {
        "id": str(issue.id),
        "type": issue.type,
        "label": LABELS.get(issue.type, issue.type),
        "status": issue.status,
        "matchId": match.code if match else None,
        "raisedBy": names.get(issue.raised_by_entry_id),
        "data": issue.data or {},
        "createdAt": issue.created_at.isoformat() if issue.created_at else None,
        "claimedBy": names.get(issue.claimed_by_profile_id),
        "claimedAt": issue.claimed_at.isoformat() if issue.claimed_at else None,
        "closedBy": names.get(issue.closed_by_profile_id),
        "closedAt": issue.closed_at.isoformat() if issue.closed_at else None,
    }

async def queue(user_id: str, public_id: str, db: AsyncSession) -> dict:
    """
    The event's queue for the host and admins: open and claimed issues oldest first, then the 20
    most recently closed.
    """

    event = await access.managed_event(user_id, public_id, db)
    await housekeep(db, event.id)
    await db.commit()
    active = (await db.execute(
        select(EventIssues, BracketMatches).outerjoin(BracketMatches, BracketMatches.id == EventIssues.match_id)
        .where(EventIssues.event_id == event.id).where(EventIssues.status.in_(("open", "claimed")))
        .order_by(EventIssues.created_at, EventIssues.id)
    )).all()
    closed = (await db.execute(
        select(EventIssues, BracketMatches).outerjoin(BracketMatches, BracketMatches.id == EventIssues.match_id)
        .where(EventIssues.event_id == event.id).where(EventIssues.status.in_(("dismissed", "resolved")))
        .order_by(EventIssues.closed_at.desc().nulls_last()).limit(20)
    )).all()
    issues = [i for i, _ in (*active, *closed)]
    profile_ids = {i.claimed_by_profile_id for i in issues} | {i.closed_by_profile_id for i in issues}
    entry_ids = {i.raised_by_entry_id for i in issues}
    names = {}
    if profile_ids - {None}:
        names.update(dict((await db.execute(select(Profiles.id, Profiles.username).where(Profiles.id.in_(profile_ids - {None})))).all()))
    if entry_ids - {None}:
        names.update(dict((await db.execute(select(BracketEntries.id, BracketEntries.name).where(BracketEntries.id.in_(entry_ids - {None})))).all()))
    return {
        "open": [_issue_out(i, m, names) for i, m in active],
        "closed": [_issue_out(i, m, names) for i, m in closed],
    }

async def acting_profile(user_id: str, event: BracketEvents, db: AsyncSession):
    """
    The profile an admin action is logged under: the host profile, or the account's accepted
    admin profile for this event.
    """

    mine = await access.my_profile_ids(user_id, db)
    if event.host_profile_id in mine:
        return event.host_profile_id
    return (await db.execute(
        select(BracketEventAdmins.profile_id).where(BracketEventAdmins.event_id == event.id)
        .where(BracketEventAdmins.profile_id.in_(mine)).where(BracketEventAdmins.status == "accepted")
    )).scalar()

async def _refused(issue_id, db: AsyncSession):
    status = (await db.execute(select(EventIssues.status).where(EventIssues.id == issue_id))).scalar()
    if status is None:
        raise HTTPException(status_code=404, detail="Issue not found.")
    if status == "claimed":
        raise HTTPException(status_code=409, detail="Another admin claimed this.")
    raise HTTPException(status_code=409, detail="This notification has been resolved.")

async def claim(user_id: str, public_id: str, issue_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    "Go to issue": takes the issue if it's still open. Returns where to go.
    """

    event = await access.managed_event(user_id, public_id, db)
    me = await acting_profile(user_id, event, db)
    claimed = (await db.execute(
        update(EventIssues)
        .where(EventIssues.id == issue_id).where(EventIssues.event_id == event.id).where(EventIssues.status == "open")
        .values(status = "claimed", claimed_by_profile_id = me, claimed_at = _now())
        .returning(EventIssues.match_id)
    )).first()
    if not claimed:
        await db.rollback()
        await _refused(issue_id, db)
    _log(db, issue_id, me, "claim")
    code = (await db.execute(select(BracketMatches.code).where(BracketMatches.id == claimed[0]))).scalar() if claimed[0] else None
    await db.commit()
    return {"matchId": code}

async def dismiss(user_id: str, public_id: str, issue_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    Dismisses the issue whether it's open or claimed (dismissal wins over a claim). Dismissing a
    post-advancement dispute keeps the result and unlocks the match it froze.
    """

    event = await access.managed_event(user_id, public_id, db)
    me = await acting_profile(user_id, event, db)
    row = (await db.execute(
        update(EventIssues)
        .where(EventIssues.id == issue_id).where(EventIssues.event_id == event.id).where(EventIssues.status.in_(("open", "claimed")))
        .values(status = "dismissed", closed_by_profile_id = me, closed_at = _now())
        .returning(EventIssues.type, EventIssues.match_id)
    )).first()
    if not row:
        await db.rollback()
        await _refused(issue_id, db)
    _log(db, issue_id, me, "dismiss")
    if row[0] == "post_advancement_dispute" and row[1]:
        await unlock_after(db, row[1])
    await db.commit()
    return await queue(user_id, public_id, db)

async def unlock_after(db: AsyncSession, match_id) -> None:
    """
    Unlocks the matches a post-advancement dispute froze (where the disputed match's winner and
    loser went), unless another dispute on it is still open.
    """

    match = await db.get(BracketMatches, match_id)
    if not match:
        return
    still_open = (await db.execute(
        select(EventIssues.id).where(EventIssues.match_id == match.id).where(EventIssues.type == "post_advancement_dispute")
        .where(EventIssues.status.in_(("open", "claimed")))
    )).first()
    if still_open:
        return
    ids = {match.next_match_id, match.loser_next_match_id} - {None}
    if match.code == "gf1":
        gf2 = (await db.execute(select(BracketMatches.id).where(BracketMatches.bracket_id == match.bracket_id).where(BracketMatches.code == "gf2"))).scalar()
        ids |= {gf2} - {None}
    if ids:
        await db.execute(update(BracketMatches).where(BracketMatches.id.in_(ids)).values(locked = False))

async def open_counts(event_ids, db: AsyncSession) -> dict:
    """
    { event_id: number of open or claimed issues }, for the managers' event cards.
    """

    if not event_ids:
        return {}
    return dict((await db.execute(
        select(EventIssues.event_id, func.count()).where(EventIssues.event_id.in_(event_ids))
        .where(EventIssues.status.in_(("open", "claimed"))).group_by(EventIssues.event_id)
    )).all())
