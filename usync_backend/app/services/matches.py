import math
import uuid

from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.users import Profiles
from app.models.brackets import (
    BracketEvents, BracketEntries, Brackets, BracketMatches, MatchReports, MatchDecisions, EventIssues,
)
from app.schemas.brackets import MatchScore, Ruling
from app.services import bracket_access as access
from app.services import bracket_sync as bsync
from app.services import event_issues as issues
from app.services.notifications import notify

# Playing the bracket: reports, no-shows, disputes and the host's / admins' rulings.
#
# Reporting:
# - Both sides report the same score: the match is decided and the winner advances.
# - Both report and disagree: it's disputed - both sides' players are told, and it goes in the
#   admin queue. Neither side can report again until the host or an admin rules.
# - One side reports and the other doesn't within 5 minutes: the background sweeper puts a
#   one-sided report in the queue. The other side can still report; agreeing decides the match
#   (and closes the queue item), disagreeing makes it a dispute.
# - No-show: once the no-show window after the match started has passed, a side can report the
#   other as a no-show; it goes in the queue.
# - After advancing: for 5 minutes after a match is decided by the players, as long as the match
#   it fed hasn't been reported, each side can dispute it once. That freezes reporting on the next
#   match and goes in the queue. Rulings are final and can't be disputed.
#
# Rulings (host / admins): award a result (optionally as a forfeit), clear a match so it's played
# or reported again, uphold a result after a dispute, or revert an earlier ruling. A ruling can't
# change a match whose next match has already been played - clear that one first. Each ruling is
# logged in match_decisions with the result before and after.

REPORT_WINDOW_MINUTES = 5
DISPUTE_WINDOW_MINUTES = 5

def _now() -> datetime:
    return datetime.now(timezone.utc)

async def _live(event: BracketEvents, db: AsyncSession, lock: bool = True) -> tuple[Brackets, list[BracketMatches]]:
    bracket = (await db.execute(
        select(Brackets).where(Brackets.event_id == event.id).with_for_update() if lock else select(Brackets).where(Brackets.event_id == event.id)
    )).scalars().first()
    if not bracket or bracket.status == "draft":
        raise HTTPException(status_code=409, detail="The bracket isn't live yet.")
    return bracket, await bsync.match_rows(bracket, db, lock = lock)

def _match(rows: list[BracketMatches], code: str) -> BracketMatches:
    match = next((r for r in rows if r.code == code), None)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found.")
    return match

def _check_score(match: BracketMatches, score_a: int, score_b: int) -> None:
    """
    A best of N is won by the first to ceil(N / 2) games: the winner has exactly that many and
    the loser fewer (a Bo3 ends 2-0 or 2-1).
    """

    needed = math.ceil(match.best_of / 2)
    high, low = max(score_a, score_b), min(score_a, score_b)
    if high != needed or low >= needed:
        raise HTTPException(status_code=422, detail=f"This match is best of {match.best_of}: the winner needs exactly {needed}, the loser fewer.")

def no_show_unlocks_at(event: BracketEvents, match: BracketMatches) -> datetime | None:
    """
    When a side may report the other as a no-show: the no-show window after the match started
    (when both sides became known), never before the event starts.
    """

    if event.no_show_minutes is None or not match.ready_at:
        return None
    return max(event.starts_at, match.ready_at) + timedelta(minutes = event.no_show_minutes)

def dispute_closes_at(match: BracketMatches) -> datetime | None:
    if match.status != "complete" or match.ruled or not match.completed_at:
        return None
    return match.completed_at + timedelta(minutes = DISPUTE_WINDOW_MINUTES)

async def _open_dispute(db: AsyncSession, match: BracketMatches) -> bool:
    return (await db.execute(
        select(EventIssues.id).where(EventIssues.match_id == match.id).where(EventIssues.type == "dispute")
        .where(EventIssues.status.in_(("open", "claimed")))
    )).first() is not None

async def notify_ready(db: AsyncSession, event: BracketEvents, ready: list[BracketMatches]) -> None:
    """
    Tells both sides of every match that just became playable.
    """

    if not ready:
        return
    ids = {x for m in ready for x in (m.entry_a_id, m.entry_b_id) if x}
    entries = {e.id: e for e in (await db.execute(select(BracketEntries).where(BracketEntries.id.in_(ids)))).scalars().all()}
    accounts = await access.entry_accounts(list(entries.values()), db)
    for m in ready:
        for side, other in ((m.entry_a_id, m.entry_b_id), (m.entry_b_id, m.entry_a_id)):
            if side in entries:
                notify(db, accounts.get(side, ()), "match_ready", {
                    "event_id": event.public_id, "event_name": event.name, "match_id": m.code,
                    "opponent": entries[other].name if other in entries else "",
                })

async def _sides(db: AsyncSession, match: BracketMatches) -> dict:
    return {e.id: e for e in (await db.execute(
        select(BracketEntries).where(BracketEntries.id.in_([match.entry_a_id, match.entry_b_id]))
    )).scalars().all()}

def _set_result(match: BracketMatches, score_a: int, score_b: int, forfeit: bool, ruled: bool) -> None:
    match.score_a, match.score_b, match.forfeit, match.ruled = score_a, score_b, forfeit, ruled
    match.winner_entry_id = match.entry_a_id if score_a > score_b else match.entry_b_id
    match.status = "complete"
    match.completed_at = _now()

async def _settle(db: AsyncSession, event: BracketEvents, bracket: Brackets, rows: list[BracketMatches]) -> None:
    await notify_ready(db, event, await bsync.sync(event, bracket, db, rows))

# ---- players -------------------------------------------------------------------

async def report(user_id: str, public_id: str, code: str, body: MatchScore, db: AsyncSession) -> BracketEvents:
    event = await access.event_by_public_id(public_id, db, lock = True)
    bracket, rows = await _live(event, db)
    me = await access.acting_entry(user_id, event, db)
    match = _match(rows, code)
    if match.status != "ready" or me.id not in (match.entry_a_id, match.entry_b_id):
        raise HTTPException(status_code=409, detail="You can only report your own match while it's being played.")
    if match.locked:
        raise HTTPException(status_code=409, detail="This match is on hold while a dispute about the match before it is reviewed.")
    if await _open_dispute(db, match):
        raise HTTPException(status_code=409, detail="This match is disputed. The host or an admin will decide it.")
    _check_score(match, body.score_a, body.score_b)

    reports = {r.entry_id: r for r in (await db.execute(select(MatchReports).where(MatchReports.match_id == match.id))).scalars().all()}
    mine = reports.get(me.id)
    if mine:
        mine.score_a, mine.score_b, mine.created_at = body.score_a, body.score_b, _now()
    else:
        mine = MatchReports(id = uuid.uuid4(), match_id = match.id, entry_id = me.id, score_a = body.score_a, score_b = body.score_b,
                            reported_by_profile_id = await _reporter_profile(user_id, db))
        db.add(mine)
    opponent_id = match.entry_b_id if me.id == match.entry_a_id else match.entry_a_id
    theirs = reports.get(opponent_id)
    sides = await _sides(db, match)
    data = {"event_id": event.public_id, "event_name": event.name, "match_id": match.code}

    if theirs and (theirs.score_a, theirs.score_b) == (body.score_a, body.score_b):
        _set_result(match, body.score_a, body.score_b, forfeit = False, ruled = False)
        await issues.resolve_for_match(db, match, None, ("one_sided_report",))
        await _settle(db, event, bracket, rows)
    elif theirs:
        players = await access.entry_accounts(list(sides.values()), db)
        for side, other in ((me.id, opponent_id), (opponent_id, me.id)):
            notify(db, players.get(side, ()), "score_disputed", {**data, "opponent": sides[other].name})
        await issues.resolve_for_match(db, match, None, ("one_sided_report",))
        await issues.open_issue(db, event, match, "dispute", None, {
            "summary": f"{sides[match.entry_a_id].name} vs {sides[match.entry_b_id].name}: the two reports disagree.",
            "reports": {
                str(e): {"scoreA": r.score_a, "scoreB": r.score_b}
                for e, r in ((me.id, mine), (opponent_id, theirs))
            },
        })
    else:
        notify(db, (await access.entry_accounts([sides[opponent_id]], db)).get(opponent_id, ()), "opponent_reported",
               {**data, "opponent": sides[me.id].name})
    await db.commit()
    return event

async def report_no_show(user_id: str, public_id: str, code: str, db: AsyncSession) -> BracketEvents:
    event = await access.event_by_public_id(public_id, db, lock = True)
    _, rows = await _live(event, db)
    me = await access.acting_entry(user_id, event, db)
    match = _match(rows, code)
    if match.status != "ready" or me.id not in (match.entry_a_id, match.entry_b_id):
        raise HTTPException(status_code=409, detail="You can only report a no-show in your own match.")
    unlocks = no_show_unlocks_at(event, match)
    if unlocks is None:
        raise HTTPException(status_code=409, detail="This event doesn't use a no-show timer.")
    if _now() < unlocks:
        raise HTTPException(status_code=409, detail="No-shows can't be reported yet.")

    sides = await _sides(db, match)
    against = match.entry_b_id if me.id == match.entry_a_id else match.entry_a_id
    issue = await issues.open_issue(db, event, match, "no_show", me, {
        "summary": f"{sides[me.id].name} says {sides[against].name} didn't show up.", "against": str(against),
    })
    if issue is None:
        raise HTTPException(status_code=409, detail="A no-show is already reported for this match.")
    notify(db, (await access.entry_accounts([sides[against]], db)).get(against, ()), "no_show_reported", {
        "event_id": event.public_id, "event_name": event.name, "match_id": match.code, "opponent": sides[me.id].name,
    })
    await db.commit()
    return event

async def dispute_after_advancing(user_id: str, public_id: str, code: str, db: AsyncSession) -> BracketEvents:
    """
    A side disputes a decided match within 5 minutes of it being decided, before the match it fed
    has been reported. Freezes reporting on that next match until the host or an admin decides.
    """

    event = await access.event_by_public_id(public_id, db, lock = True)
    _, rows = await _live(event, db)
    me = await access.acting_entry(user_id, event, db)
    match = _match(rows, code)
    if me.id not in (match.entry_a_id, match.entry_b_id):
        raise HTTPException(status_code=403, detail="Only the two sides of a match can dispute it.")
    closes = dispute_closes_at(match)
    if closes is None:
        raise HTTPException(status_code=409, detail="This result was decided by the host or an admin, so it's final." if match.ruled else "This match hasn't been decided.")
    if _now() > closes:
        raise HTTPException(status_code=409, detail=f"Results can only be disputed for {DISPUTE_WINDOW_MINUTES} minutes after they're decided.")
    following = bsync.dependents(match, rows)
    if following:
        reported = (await db.execute(select(MatchReports.id).where(MatchReports.match_id.in_([f.id for f in following])))).first()
        if reported or any(f.status == "complete" for f in following):
            raise HTTPException(status_code=409, detail="The next match has already been reported, so this result stands.")
    already = (await db.execute(
        select(EventIssues.id).where(EventIssues.match_id == match.id).where(EventIssues.raised_by_entry_id == me.id)
        .where(EventIssues.type == "post_advancement_dispute")
    )).first()
    if already:
        raise HTTPException(status_code=409, detail="You've already disputed this match.")

    sides = await _sides(db, match)
    winner = sides.get(match.winner_entry_id)
    await issues.open_issue(db, event, match, "post_advancement_dispute", me, {
        "summary": f"{sides[me.id].name} disputes {winner.name if winner else 'the'} win ({match.score_a}-{match.score_b}).",
    })
    for f in following:
        f.locked = True
    await db.commit()
    return event

async def _reporter_profile(user_id: str, db: AsyncSession):
    """
    The profile a report is logged under: the account's player profile, or for an org-owned
    team reported by its owner, the org profile.
    """

    rows = dict((await db.execute(select(Profiles.type, Profiles.id).where(Profiles.account_id == user_id))).all())
    return rows.get("player") or rows.get("org") or next(iter(rows.values()), None)

# ---- host and admins ---------------------------------------------------------------

def _guard_dependents(match: BracketMatches, rows: list[BracketMatches]) -> None:
    if any(f.status == "complete" for f in bsync.dependents(match, rows)):
        raise HTTPException(status_code=409, detail="The match this one fed has been played. Clear that match first.")

def _decision(match: BracketMatches, profile_id, action: str, before: tuple, issue_id=None, reverts=None) -> MatchDecisions:
    return MatchDecisions(
        id = uuid.uuid4(), match_id = match.id, decided_by_profile_id = profile_id, action = action, issue_id = issue_id,
        reverts_decision_id = reverts,
        prev_status = before[0], prev_score_a = before[1], prev_score_b = before[2], prev_winner_entry_id = before[3],
        new_status = match.status, new_score_a = match.score_a, new_score_b = match.score_b, new_winner_entry_id = match.winner_entry_id,
    )

def _snapshot(match: BracketMatches) -> tuple:
    return (match.status, match.score_a, match.score_b, match.winner_entry_id)

async def rule(user_id: str, public_id: str, code: str, body: Ruling, db: AsyncSession) -> BracketEvents:
    """
    A host or admin ruling on a match: 'award' (with the score; forfeit for a no-show), 'clear'
    (play / report it again), or 'uphold' (a post-advancement dispute was wrong: keep the result
    and unlock the next match). Rulings are final and close the match's queue items.
    """

    event = await access.managed_event(user_id, public_id, db, lock = True)
    bracket, rows = await _live(event, db)
    match = _match(rows, code)
    me = await issues.acting_profile(user_id, event, db)
    before = _snapshot(match)

    if body.action == "uphold":
        if match.status != "complete":
            raise HTTPException(status_code=409, detail="Only a decided match can be upheld.")
        await issues.resolve_for_match(db, match, me, ("post_advancement_dispute",))
        await issues.unlock_after(db, match.id)
        db.add(_decision(match, me, "uphold", before))
        await db.commit()
        return event

    if match.status not in ("ready", "complete"):
        raise HTTPException(status_code=409, detail="That match isn't ready yet.")
    if match.status == "complete":
        _guard_dependents(match, rows)

    if body.action == "award":
        if body.score_a is None or body.score_b is None:
            raise HTTPException(status_code=422, detail="Give the score to award.")
        _check_score(match, body.score_a, body.score_b)
        _set_result(match, body.score_a, body.score_b, forfeit = body.forfeit, ruled = True)
    elif body.action == "clear":
        match.status, match.score_a, match.score_b, match.winner_entry_id = "ready", None, None, None
        match.forfeit = match.ruled = False
        match.completed_at = None
        match.ready_at = _now()
    else:
        raise HTTPException(status_code=422, detail="Unknown ruling.")

    await db.execute(delete(MatchReports).where(MatchReports.match_id == match.id))
    await issues.resolve_for_match(db, match, me)
    await _unlock_following(db, match, rows)
    db.add(_decision(match, me, "forfeit" if body.action == "award" and body.forfeit else body.action, before))
    await _settle(db, event, bracket, rows)
    await db.commit()
    return event

async def _unlock_following(db: AsyncSession, match: BracketMatches, rows: list[BracketMatches]) -> None:
    for f in bsync.dependents(match, rows):
        f.locked = False

async def revert(user_id: str, public_id: str, decision_id: uuid.UUID, db: AsyncSession) -> BracketEvents:
    """
    Undoes a ruling: puts the match's result back to what it was just before it, as long as the
    match still has the result that ruling gave it.
    """

    event = await access.managed_event(user_id, public_id, db, lock = True)
    bracket, rows = await _live(event, db)
    decision = (await db.execute(
        select(MatchDecisions).where(MatchDecisions.id == decision_id).where(MatchDecisions.match_id.in_([r.id for r in rows]))
    )).scalars().first()
    if not decision or decision.action in ("revert", "uphold"):
        raise HTTPException(status_code=404, detail="Nothing to revert.")
    match = next(r for r in rows if r.id == decision.match_id)
    if _snapshot(match) != (decision.new_status, decision.new_score_a, decision.new_score_b, decision.new_winner_entry_id):
        raise HTTPException(status_code=409, detail="This match has changed since that ruling. Make a new ruling instead.")
    if match.status == "complete":
        _guard_dependents(match, rows)

    me = await issues.acting_profile(user_id, event, db)
    before = _snapshot(match)
    if decision.prev_status == "complete" and decision.prev_score_a is not None:
        _set_result(match, decision.prev_score_a, decision.prev_score_b, forfeit = False, ruled = True)
    else:
        match.status, match.score_a, match.score_b, match.winner_entry_id = "ready", None, None, None
        match.forfeit = match.ruled = False
        match.completed_at = None
    await db.execute(delete(MatchReports).where(MatchReports.match_id == match.id))
    db.add(_decision(match, me, "revert", before, reverts = decision.id))
    await _settle(db, event, bracket, rows)
    await db.commit()
    return event

# ---- the background sweeper -------------------------------------------------------------

async def flag_one_sided_reports(db: AsyncSession) -> int:
    """
    A ready match with only one side's report, older than 5 minutes, and no one-sided issue raised
    since that report: put it in the queue. Returns how many were raised. The caller commits.
    """

    cutoff = _now() - timedelta(minutes = REPORT_WINDOW_MINUTES)
    candidates = (await db.execute(
        select(MatchReports, BracketMatches, Brackets, BracketEvents)
        .join(BracketMatches, BracketMatches.id == MatchReports.match_id)
        .join(Brackets, Brackets.id == BracketMatches.bracket_id)
        .join(BracketEvents, BracketEvents.id == Brackets.event_id)
        .where(BracketMatches.status == "ready").where(MatchReports.created_at < cutoff)
    )).all()
    raised = 0
    for report, match, _, event in candidates:
        count = len((await db.execute(select(MatchReports.id).where(MatchReports.match_id == match.id))).all())
        if count != 1:
            continue
        since = (await db.execute(
            select(EventIssues.id).where(EventIssues.match_id == match.id)
            .where(EventIssues.type.in_(("one_sided_report", "dispute"))).where(EventIssues.created_at >= report.created_at)
        )).first()
        if since:
            continue
        entry = await db.get(BracketEntries, report.entry_id)
        if await issues.open_issue(db, event, match, "one_sided_report", entry, {
            "summary": f"{entry.name} reported {report.score_a}-{report.score_b} and the other side hasn't answered in {REPORT_WINDOW_MINUTES} minutes.",
        }):
            raised += 1
    return raised

