import uuid

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.brackets import BracketEvents, Brackets, BracketSeeds, BracketMatches, MatchReports, EventStandings
from app.services import bracket_engine as engine

# Keeps an event's bracket_matches rows in step with the bracket engine.
#
# The rows hold the state: who's in each match, its status, and its result (the score columns
# and winner). The engine holds the rules: given the seeds and the decided results, which entrant
# sits in which match. After anything changes a result (players agreeing, a ruling, clearing a
# match), sync() rebuilds from the rows and writes back who now plays where - that's how a
# winner advances, and how a reverted winner is taken back out of the next match.
#
# Swiss rounds are paired once: stored rounds are handed back to the engine as fixed, so a later
# change to an earlier round only moves the standings.
#
# Once the bracket is complete, sync() also stores every entrant's final placement
# (event_standings). A later correction that changes them rewrites them, and reopening the
# bracket removes them. For an event with money, each change starts the 24-hour payout clock again
# (payout_due_at); the sweeper pays out once it runs out (app.services.payouts).

PAYOUT_DELAY = timedelta(hours = 24)

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _uuid(value) -> uuid.UUID | None:
    return uuid.UUID(value) if engine.is_entrant(value) else None

def _sid(value) -> str | None:
    return str(value) if value else None

async def seeds_of(bracket: Brackets, db: AsyncSession) -> list[str]:
    rows = (await db.execute(
        select(BracketSeeds.entry_id).where(BracketSeeds.bracket_id == bracket.id).order_by(BracketSeeds.seed)
    )).scalars().all()
    return [str(r) for r in rows]

async def match_rows(bracket: Brackets, db: AsyncSession, lock: bool = False) -> list[BracketMatches]:
    query = select(BracketMatches).where(BracketMatches.bracket_id == bracket.id).order_by(BracketMatches.round, BracketMatches.slot)
    if lock:
        query = query.with_for_update()
    return list((await db.execute(query)).scalars().all())

def _results(rows: list[BracketMatches]) -> dict:
    return {
        r.code: {"a": str(r.entry_a_id), "b": str(r.entry_b_id), "scoreA": r.score_a, "scoreB": r.score_b, "forfeit": r.forfeit}
        for r in rows if r.status == "complete" and r.entry_a_id and r.entry_b_id and r.score_a is not None
    }

def _fixed_swiss(rows: list[BracketMatches]) -> dict:
    fixed: dict = {}
    for r in sorted(rows, key=lambda r: (r.round, r.slot)):
        a = str(r.entry_a_id) if r.entry_a_id else None
        b = engine.BYE if r.code.endswith("bye") else (str(r.entry_b_id) if r.entry_b_id else None)
        fixed.setdefault(r.round, []).append((r.code, a, b))
    return fixed

def build(fmt: str, seeds: list[str], rows: list[BracketMatches] | None = None) -> dict:
    rows = rows or []
    return engine.build_bracket(fmt, seeds, _results(rows), _fixed_swiss(rows) if fmt == "swiss" else None)

def _best_of(event: BracketEvents, fmt: str, built: dict, m: dict) -> int:
    if event.final_best_of and engine.is_final_match(fmt, built, m):
        return event.final_best_of
    return event.best_of

def _is_placeholder(fmt: str, m: dict) -> bool:
    # Swiss rounds not paired yet: no rows until they are.
    return fmt == "swiss" and m["a"] is None and m["b"] is None and m["status"] == "pending"

async def create_matches(event: BracketEvents, bracket: Brackets, db: AsyncSession) -> list[BracketMatches]:
    """
    Publishing: one row per match the engine lays out, linked to where its winner and loser go.
    Returns the matches that are ready to play straight away.
    """

    seeds = await seeds_of(bracket, db)
    built = build(bracket.format, seeds)
    links = engine.match_links(bracket.format, len(seeds))
    rows: dict = {}
    for m in engine.all_matches(built):
        if _is_placeholder(bracket.format, m) or m["id"] == "gf2":
            continue
        # Sides are filled in now, so a Swiss round reads back as already paired.
        rows[m["id"]] = BracketMatches(
            id = uuid.uuid4(), bracket_id = bracket.id, code = m["id"], stage = engine.match_stage(bracket.format, m),
            round = m["round"], slot = m["slot"], best_of = _best_of(event, bracket.format, built, m), status = "pending",
            entry_a_id = _uuid(m["a"]), entry_b_id = _uuid(m["b"]),
        )
    db.add_all(rows.values())
    await db.flush()
    for code, row in rows.items():
        link = links.get(code) or {}
        if link.get("next") and link["next"][0] in rows:
            row.next_match_id, row.next_slot = rows[link["next"][0]].id, link["next"][1]
        if link.get("loser_next") and link["loser_next"][0] in rows:
            row.loser_next_match_id, row.loser_next_slot = rows[link["loser_next"][0]].id, link["loser_next"][1]
    await db.flush()
    return await sync(event, bracket, db)

async def sync(event: BracketEvents, bracket: Brackets, db: AsyncSession, rows: list[BracketMatches] | None = None) -> list[BracketMatches]:
    """
    Rebuilds the bracket from its rows and writes back who plays where. A match whose sides
    changed loses its stale result and reports; a match that just became playable gets ready_at
    (its start, for the no-show timer). New Swiss rounds and a grand final reset get rows when
    they come into being; a reset that's no longer needed is removed. Marks the bracket complete
    (or back to published) to match. Returns the matches that just became ready.
    """

    rows = rows if rows is not None else await match_rows(bracket, db)
    seeds = await seeds_of(bracket, db)
    built = build(bracket.format, seeds, rows)
    by_code = {r.code: r for r in rows}
    now = _now()
    newly_ready: list[BracketMatches] = []
    seen = set()

    for m in engine.all_matches(built):
        if _is_placeholder(bracket.format, m):
            continue
        seen.add(m["id"])
        row = by_code.get(m["id"])
        if row is None:
            row = BracketMatches(
                id = uuid.uuid4(), bracket_id = bracket.id, code = m["id"], stage = engine.match_stage(bracket.format, m),
                round = m["round"], slot = m["slot"], best_of = _best_of(event, bracket.format, built, m), status = "pending",
            )
            db.add(row)
            rows.append(row)
        a, b = _uuid(m["a"]), _uuid(m["b"])
        changed = (row.entry_a_id, row.entry_b_id) != (a, b)
        if changed:
            await db.execute(delete(MatchReports).where(MatchReports.match_id == row.id))
            row.entry_a_id, row.entry_b_id = a, b
        row.best_of = _best_of(event, bracket.format, built, m)
        row.status = m["status"]
        if m["status"] == "complete":
            row.completed_at = row.completed_at or now
        else:
            row.score_a = row.score_b = None
            row.forfeit = row.ruled = False
            row.completed_at = None
            row.winner_entry_id = _uuid(m.get("winner")) if m["status"] == "bye" else None
        if m["status"] == "ready":
            if changed or row.ready_at is None:
                row.ready_at = now
                newly_ready.append(row)
        else:
            row.ready_at = row.ready_at if m["status"] == "complete" else None
            if m["status"] != "complete":
                row.locked = False

    for row in list(rows):
        if row.code not in seen:
            await db.delete(row)
            rows.remove(row)

    done, total = engine.progress(built)
    if total and done == total:
        if bracket.status != "complete":
            bracket.status, bracket.completed_at = "complete", now
    elif bracket.status == "complete":
        bracket.status, bracket.completed_at = "published", None
    final = engine.final_standings(bracket.format, seeds, built) if bracket.status == "complete" else None
    await record_standings(event, final, db)
    await db.flush()
    return newly_ready

async def record_standings(event: BracketEvents, final: list[tuple[str, int]] | None, db: AsyncSession) -> bool:
    """
    Stores the final placements (None = not decided: removes them). Does nothing if they haven't
    changed. When they change on an event with money, the payout is due 24 hours from now (or
    not at all while undecided). Returns whether anything changed.
    """

    wanted = {uuid.UUID(eid): placement for eid, placement in (final or [])}
    stored = dict((await db.execute(
        select(EventStandings.entry_id, EventStandings.placement).where(EventStandings.event_id == event.id)
    )).all())
    if wanted == stored:
        return False
    await db.execute(delete(EventStandings).where(EventStandings.event_id == event.id))
    db.add_all([EventStandings(event_id = event.id, entry_id = entry_id, placement = placement) for entry_id, placement in wanted.items()])
    if event.prize_funding:
        event.payout_due_at = _now() + PAYOUT_DELAY if wanted else None
    await db.flush()
    return True

async def standings_of_event(event: BracketEvents, bracket: Brackets | None, db: AsyncSession) -> list[tuple[uuid.UUID, int, int | None]]:
    """
    [(entry id, placement, prize cents)] best first, from event_standings. Brackets finished
    before standings were stored have no rows; theirs are worked out from the matches instead.
    """

    rows = (await db.execute(
        select(EventStandings.entry_id, EventStandings.placement, EventStandings.prize_cents)
        .where(EventStandings.event_id == event.id).order_by(EventStandings.placement, EventStandings.entry_id)
    )).all()
    if rows or bracket is None or bracket.status != "complete":
        return [tuple(r) for r in rows]
    seeds = await seeds_of(bracket, db)
    final = engine.final_standings(bracket.format, seeds, build(bracket.format, seeds, await match_rows(bracket, db))) or []
    return [(uuid.UUID(eid), placement, None) for eid, placement in final]

def dependents(row: BracketMatches, rows: list[BracketMatches]) -> list[BracketMatches]:
    """
    The matches this match's result feeds: where its winner and loser went (and, for the grand
    final, the reset match).
    """

    ids = {row.next_match_id, row.loser_next_match_id} - {None}
    out = [r for r in rows if r.id in ids]
    if row.code == "gf1":
        out += [r for r in rows if r.code == "gf2" and r.bracket_id == row.bracket_id]
    return out

def built_with_rows(fmt: str, seeds: list[str], rows: list[BracketMatches]) -> dict:
    """
    The engine's bracket for the API, with each match's stored details laid over it (its row id,
    best of, start, lock and ruling) and the final placings.
    """

    built = build(fmt, seeds, rows)
    by_code = {r.code: r for r in rows}
    for m in engine.all_matches(built):
        row = by_code.get(m["id"])
        m["matchId"] = str(row.id) if row else None
        m["bestOf"] = row.best_of if row else None
        m["readyAt"] = row.ready_at.isoformat() if row and row.ready_at else None
        m["completedAt"] = row.completed_at.isoformat() if row and row.completed_at else None
        m["locked"] = bool(row and row.locked)
        m["ruled"] = bool(row and row.ruled)
    done, total = engine.progress(built)
    return {**built, "placements": engine.placements(fmt, built, seeds), "progress": {"done": done, "total": total}}
