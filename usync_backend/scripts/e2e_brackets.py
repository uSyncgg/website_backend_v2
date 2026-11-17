"""
End-to-end check of bracket hosting through the service layer (the routers are thin). Creates
throwaway accounts named e2e-*, runs sign-ups, seeding, publishing, reporting, disputes, the
admin queue, rulings, Swiss and double elimination, then deletes everything it made.

It writes to whatever database ASYNC_SUPABASE_CONNECTION_URL points at, so run it against a local
or throwaway database:

    ASYNC_SUPABASE_CONNECTION_URL=postgresql+asyncpg://... uv run python scripts/e2e_brackets.py

It refuses to run against a non-local host unless E2E_ALLOW_REMOTE=1 is set.
"""
import asyncio
import os
import sys
import uuid
from types import SimpleNamespace as NS
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

sys.path.insert(0, ".")

_host = urlparse(os.environ.get("ASYNC_SUPABASE_CONNECTION_URL", "").replace("+asyncpg", "")).hostname
if _host not in ("127.0.0.1", "localhost") and os.environ.get("E2E_ALLOW_REMOTE") != "1":
    sys.exit(f"Refusing to run against {_host!r}. Point ASYNC_SUPABASE_CONNECTION_URL at a local database (or set E2E_ALLOW_REMOTE=1).")

from fastapi import HTTPException
from sqlalchemy import select, delete, update
from app.core.db import AsyncSessionLocal, engine
from app.models.users import UsersParent, Profiles
from app.models.teams import TeamMembers
from app.models.brackets import Notifications, BracketMatches, MatchReports, EventIssues, Brackets, BracketEvents
from app.services import brackets as svc
from app.services import teams as team_svc
from app.services import event_issues as queue_svc
from app.services import matches as match_svc
from app.services.notifications import list_notifications, mark_read, delete_old_read
from app.schemas.brackets import EventCreate, Registration, MatchScore, Ruling, AdminInvite
from app.schemas.users import TeamUpdate

RUN = uuid.uuid4().hex[:6]
accounts = []
failures = []
NOW = datetime.now(timezone.utc)

def check(label, ok):
    print(("  PASS " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)

async def expect_http(db, label, coro, status):
    try:
        await coro
        check(f"{label} (expected {status}, got success)", False)
    except HTTPException as e:
        check(f"{label} -> {e.status_code}: {e.detail}", e.status_code == status)
    await db.rollback()

async def make_account(db, name, ptype="player"):
    uid = uuid.uuid4()
    db.add(UsersParent(id = uid, email = f"e2e-{RUN}-{name}@example.com"))
    await db.flush()
    profile = Profiles(id = uuid.uuid4(), public_id = f"e2e{RUN}{name}"[:20], account_id = uid, type = ptype,
                       username = f"e2e-{RUN}-{name}", canonical_username = f"e2e-{RUN}-{name}")
    db.add(profile)
    await db.flush()
    accounts.append(uid)
    return str(uid), NS(id = profile.id, public_id = profile.public_id, username = profile.username)

async def make_team(db, name, games, members):
    """members[0] owns it and is its captain."""
    team = team_svc.new_team(members[0].id, f"{name} {RUN}")
    db.add(team)
    await db.flush()
    db.add_all(team_svc.team_game_rows(team, await team_svc.game_ids_for(games, db)))
    for i, p in enumerate(members):
        db.add(TeamMembers(team_id = team.id, profile_id = p.id, role = "captain" if i == 0 else "player"))
    await db.flush()
    return NS(id = team.id, public_id = team.public_id, name = team.name)

async def notes(db, uid, type_=None):
    return [n for n in await list_notifications(uid, db) if type_ is None or n["type"] == type_]

def entrant(ev, team):
    return next(e for e in ev["entrants"] if e["name"] == team.name)

def match(ev, code):
    from app.services.bracket_engine import all_matches
    return next(m for m in all_matches(ev["bracket"]["built"]) if m["id"] == code)

def event_body(host, name, **kw):
    base = dict(host_profile = host.public_id, name = f"E2E {name} {RUN}", event_type = "tournament", game = "halo",
                game_title = "halo_infinite", format = "single_elim", team_size = "2v2", best_of = 3,
                starts_at = NOW - timedelta(minutes = 30))
    return EventCreate(**{**base, **kw})

async def backdate(db, model, where, **values):
    await db.execute(update(model).where(where).values(**values))
    await db.commit()

async def main():
    async with AsyncSessionLocal() as db:
        print("setup")
        host_uid, host = await make_account(db, "host", "host")
        adm_uid, adm = await make_account(db, "adm")
        p = {}
        for t in "abcde":
            for i in range(3):
                p[f"{t}{i}"] = await make_account(db, f"{t}{i}")
        uid = {k: v[0] for k, v in p.items()}
        pr = {k: v[1] for k, v in p.items()}
        team_a = await make_team(db, "Alpha", ["halo"], [pr["a0"], pr["a1"], pr["a2"]])
        team_b = await make_team(db, "Bravo", ["halo"], [pr["b0"], pr["b1"], pr["b2"], pr["a1"]])
        team_c = await make_team(db, "Charlie", ["valorant"], [pr["c0"], pr["c1"], pr["c2"]])
        team_d = await make_team(db, "Delta", ["halo", "cs2"], [pr["d0"], pr["d1"], pr["d2"]])
        team_e = await make_team(db, "Echo", ["halo"], [pr["e0"], pr["e1"], pr["e2"]])
        await db.commit()

        print("create event")
        await expect_http(db, "a game with titles needs one", svc.create_event(host_uid, event_body(host, "x", game_title = None), db), 422)
        await expect_http(db, "a title from another game is refused", svc.create_event(host_uid, event_body(host, "x", game_title = "black_ops_7"), db), 422)
        await expect_http(db, "a mode for a game without modes is refused", svc.create_event(host_uid, event_body(host, "x", game_mode = "slayer"), db), 422)
        await expect_http(db, "unknown format refused", svc.create_event(host_uid, event_body(host, "x", format = "ladder"), db), 422)
        await expect_http(db, "a player can't host", svc.create_event(uid["a0"], event_body(pr["a0"], "x"), db), 403)
        ev = await svc.create_event(host_uid, event_body(host, "Cup", final_best_of = 5, max_entrants = 4, min_entrants = 4, no_show_minutes = 10,
            payments = {"enabled": False, "notifyEmail": "host@example.com"},
            signup_form = {"playerFields": ["Discord"], "questions": [{"label": "Region", "type": "select", "options": "NA, EU", "required": True}]}), db)
        eid = ev["id"]
        check("event created with game, title and lookups", ev["game"] == "Halo" and ev["gameTitle"] == "Halo Infinite" and ev["teamSize"] == "2v2")

        print("admins")
        await svc.invite_admin(host_uid, eid, AdminInvite(profile = adm.public_id), db)
        invites = await svc.my_admin_invites(adm_uid, db)
        check("invite is the bell item", len(invites) == 1 and len(await notes(db, adm_uid, "admin_invite")) == 0)
        await svc.respond_admin_invite(adm_uid, uuid.UUID(invites[0]["id"]), True, db)
        as_admin = await svc.get_event(eid, adm_uid, db)
        check("admin manages but isn't the host", as_admin["canManage"] and not as_admin["isHost"])
        await expect_http(db, "admin can't delete the event", svc.delete_event(adm_uid, eid, db), 404)

        print("sign-ups")
        reg = lambda team, players, answers = {"Region": "NA"}: Registration(team_id = team.public_id, players = [pr[x].public_id for x in players],
                                                                            email = "cap@example.com", answers = answers)
        await expect_http(db, "required question enforced", svc.register(uid["a0"], eid, reg(team_a, ["a0", "a1"], {}), db), 422)
        await expect_http(db, "select answer must be an option", svc.register(uid["a0"], eid, reg(team_a, ["a0", "a1"], {"Region": "Mars"}), db), 422)
        await expect_http(db, "unknown answers refused", svc.register(uid["a0"], eid, reg(team_a, ["a0", "a1"], {"Region": "NA", "Shoe": "9"}), db), 422)
        await expect_http(db, "a non-captain can't enter the team", svc.register(uid["a2"], eid, reg(team_a, ["a0", "a1"]), db), 403)
        await expect_http(db, "players must be on the team", svc.register(uid["a0"], eid, reg(team_a, ["a0", "b1"]), db), 422)
        ev = await svc.register(uid["a0"], eid, reg(team_a, ["a0", "a1"]), db)
        check("Alpha signed up with its two picked players", entrant(ev, team_a)["status"] == "signed_up" and entrant(ev, team_a)["rosterSize"] == 2)
        await expect_http(db, "a player can't play for two entrants", svc.register(uid["b0"], eid, reg(team_b, ["b0", "a1"]), db), 409)
        ev = await svc.register(uid["b0"], eid, reg(team_b, ["b0"]), db)
        check("Bravo pending with one of two players", entrant(ev, team_b)["status"] == "pending")
        ev = await svc.set_players(uid["b0"], eid, [pr["b0"].public_id, pr["b1"].public_id], db)
        check("the captain fills the roster: signed up", entrant(ev, team_b)["status"] == "signed_up")
        await expect_http(db, "a team that doesn't play the game can't enter", svc.register(uid["c0"], eid, reg(team_c, ["c0", "c1"]), db), 409)
        await team_svc.update_team(uid["c0"], team_c.public_id, TeamUpdate(games = ["valorant", "halo"]), db)
        await svc.register(uid["c0"], eid, reg(team_c, ["c0", "c1"]), db)
        await svc.register(uid["d0"], eid, reg(team_d, ["d0", "d1"]), db)
        ev = await svc.register(uid["e0"], eid, reg(team_e, ["e0", "e1"]), db)
        check("event full: Echo waits as pending", entrant(ev, team_e)["status"] == "pending")
        host_player = Profiles(id = uuid.uuid4(), public_id = f"e2e{RUN}hp"[:20], account_id = uuid.UUID(host_uid), type = "player",
                               username = f"e2e-{RUN}-hostplayer", canonical_username = f"e2e-{RUN}-hostplayer")
        host_player_public_id = host_player.public_id
        db.add(host_player)
        await db.flush()
        db.add(TeamMembers(team_id = team_e.id, profile_id = host_player.id, role = "player"))
        await db.commit()
        await expect_http(db, "the host can't sign up for their own event", svc.register(host_uid, eid, Registration(email = "x@example.com", answers = {"Region": "NA"}), db), 409)
        await expect_http(db, "a captain can't pick the host as a player", svc.set_players(uid["e0"], eid, [pr["e0"].public_id, host_player_public_id], db), 409)
        await expect_http(db, "an admin can't sign up to play", svc.register(adm_uid, eid, Registration(email = "x@example.com", answers = {"Region": "NA"}), db), 409)
        clash = await svc.create_event(host_uid, event_body(host, "Clash", team_size = "1v1"), db)
        await expect_http(db, "no two events at the exact same start", svc.register(uid["a0"], clash["id"], Registration(email = "x@example.com"), db), 409)
        await expect_http(db, "an admin's not invited while signed up", svc.invite_admin(host_uid, eid, AdminInvite(profile = pr["c1"].public_id), db), 409)

        print("privacy")
        anon = await svc.get_event(eid, None, db)
        check("public payload hides payment email and sign-up emails", "notifyEmail" not in anon["payments"] and all(e.get("email") is None for e in anon["entrants"]))
        own = await svc.get_event(eid, uid["a0"], db)
        check("the captain sees their own entry's email", entrant(own, team_a)["email"] == "cap@example.com")
        teammate = await svc.get_event(eid, uid["a2"], db)
        check("a teammate sees the entry but can't act", teammate["viewerEntrantId"] == entrant(own, team_a)["id"] and not teammate["viewerCanAct"])

        print("seeding and publishing")
        ids = {t.name: entrant(ev, t)["id"] for t in (team_a, team_b, team_c, team_d)}
        ev = await svc.save_draft(host_uid, eid, [ids[team_a.name], ids[team_b.name], ids[team_c.name], ids[team_d.name]], db)
        check("draft preview built by the server", ev["bracket"]["status"] == "draft" and len(ev["bracket"]["built"]["rounds"]) == 2)
        check("the public can't see the draft", (await svc.get_event(eid, None, db))["bracket"] is None)
        await svc.unregister(uid["d0"], eid, db)
        ev = await svc.get_event(eid, host_uid, db)
        check("Delta withdrew; Echo moved up", entrant(ev, team_e)["status"] == "signed_up")
        await expect_http(db, "publish refuses a stale draft and rebuilds it", svc.publish(host_uid, eid, db), 409)
        ev = await svc.get_event(eid, host_uid, db)
        check("rebuilt draft has Echo in Delta's place", entrant(ev, team_e)["id"] in ev["bracket"]["seeds"] and len(ev["bracket"]["seeds"]) == 4)
        ev = await svc.publish(adm_uid, eid, db)
        check("published by an admin; sign-ups closed", ev["stage"] == "live" and not ev["registrationOpen"])
        await expect_http(db, "event roster locked once live", svc.set_players(uid["a0"], eid, [pr["a0"].public_id, pr["a2"].public_id], db), 409)
        await team_svc.remove_member(uid["b0"], team_b.public_id, pr["b2"].public_id, db)
        check("the team itself never freezes", True)
        A, B, C, E = (entrant(ev, t)["id"] for t in (team_a, team_b, team_c, team_e))
        r0m0, r0m1 = match(ev, "r0m0"), match(ev, "r0m1")
        check("seed 1 v 4 and 2 v 3", (r0m0["a"], r0m0["b"]) == (A, E) and (r0m1["a"], r0m1["b"]) == (B, C))
        check("both sides told about their match", len(await notes(db, uid["e1"], "match_ready")) == 1)

        print("reporting")
        await expect_http(db, "Bo3 can't end 3-0", svc.report(uid["a0"], eid, "r0m0", MatchScore(score_a = 3, score_b = 0), db), 422)
        await expect_http(db, "a non-captain can't report", svc.report(uid["a1"], eid, "r0m0", MatchScore(score_a = 2, score_b = 1), db), 403)
        await svc.report(uid["a0"], eid, "r0m0", MatchScore(score_a = 2, score_b = 1), db)
        check("Echo told to confirm", len(await notes(db, uid["e0"], "opponent_reported")) == 1)
        ev = await svc.report(uid["e0"], eid, "r0m0", MatchScore(score_a = 2, score_b = 0), db)
        check("same winner, different scores: no dispute, Alpha advances", match(ev, "r0m0")["winner"] == A and match(ev, "r1m0")["a"] == A)
        check("the first report's score is recorded", (match(ev, "r0m0")["scoreA"], match(ev, "r0m0")["scoreB"]) == (2, 1))

        await svc.report(uid["b0"], eid, "r0m1", MatchScore(score_a = 2, score_b = 0), db)
        ev = await svc.report(uid["c0"], eid, "r0m1", MatchScore(score_a = 0, score_b = 2), db)
        check("reports naming different winners dispute it", ev["bracket"]["matches"]["r0m1"]["state"] == "disputed")
        check("both sides' players told", len(await notes(db, uid["b1"], "score_disputed")) == 1 and len(await notes(db, uid["c1"], "score_disputed")) == 1)
        check("host and admin told", len(await notes(db, host_uid, "issue_opened")) == 1 and len(await notes(db, adm_uid, "issue_opened")) == 1)
        await expect_http(db, "no more reports while disputed", svc.report(uid["b0"], eid, "r0m1", MatchScore(score_a = 2, score_b = 0), db), 409)

        print("admin queue")
        q = await queue_svc.queue(host_uid, eid, db)
        dispute_id = uuid.UUID(q["open"][0]["id"])
        check("the dispute is in the queue", q["open"][0]["type"] == "dispute")
        claimed = await queue_svc.claim(host_uid, eid, dispute_id, db)
        check("go to issue returns the match", claimed["matchId"] == "r0m1")
        await expect_http(db, "a second claim is refused", queue_svc.claim(adm_uid, eid, dispute_id, db), 409)
        await backdate(db, EventIssues, EventIssues.id == dispute_id, claimed_at = NOW - timedelta(minutes = 11))
        await queue_svc.housekeep(db)
        await db.commit()
        q = await queue_svc.queue(adm_uid, eid, db)
        check("a claim left 10 minutes goes back to open", q["open"][0]["status"] == "open")
        await queue_svc.claim(adm_uid, eid, dispute_id, db)
        await queue_svc.dismiss(host_uid, eid, dispute_id, db)
        await expect_http(db, "after a dismissal, claims are told it's resolved", queue_svc.claim(adm_uid, eid, dispute_id, db), 409)
        await expect_http(db, "a non-manager can't see the queue", queue_svc.queue(uid["a0"], eid, db), 404)
        ev = await svc.rule(adm_uid, eid, "r0m1", Ruling(action = "award", score_a = 2, score_b = 0), db)
        check("an admin's ruling decides it; Bravo to the final", match(ev, "r1m0")["b"] == B and match(ev, "r0m1")["ruled"])
        await expect_http(db, "rulings can't be disputed", svc.dispute(uid["c0"], eid, "r0m1", db), 409)

        print("dispute after advancing")
        ev = await svc.dispute(uid["e0"], eid, "r0m0", db)
        check("Echo disputes within 5 minutes: the final is frozen", ev["bracket"]["built"]["rounds"][1][0]["locked"])
        await expect_http(db, "a side can only dispute once", svc.dispute(uid["e0"], eid, "r0m0", db), 409)
        await expect_http(db, "no reporting on a frozen match", svc.report(uid["a0"], eid, "r1m0", MatchScore(score_a = 3, score_b = 0), db), 409)
        ev = await svc.rule(host_uid, eid, "r0m0", Ruling(action = "award", score_a = 1, score_b = 2), db)
        check("awarding Echo swaps it into the final", match(ev, "r1m0")["a"] == E and not match(ev, "r1m0")["locked"])
        decisions = await svc.decisions(host_uid, eid, db)
        ev = await svc.revert(host_uid, eid, uuid.UUID(decisions[0]["id"]), db)
        check("reverting the ruling puts Alpha back", match(ev, "r1m0")["a"] == A)
        check("the final uses the finals best of", match(ev, "r1m0")["bestOf"] == 5)

        print("final")
        await expect_http(db, "Bo5 final can't end 2-0", svc.report(uid["a0"], eid, "r1m0", MatchScore(score_a = 2, score_b = 0), db), 422)
        await svc.report(uid["a0"], eid, "r1m0", MatchScore(score_a = 3, score_b = 1), db)
        ev = await svc.report(uid["b0"], eid, "r1m0", MatchScore(score_a = 3, score_b = 1), db)
        check("bracket complete with placings", ev["stage"] == "complete" and ev["bracket"]["built"]["placements"][0] == A)
        await expect_http(db, "a decided match feeding a played one can't be changed", svc.rule(host_uid, eid, "r0m0", Ruling(action = "clear"), db), 409)
        page = await svc.entered_events(uid["a2"], db)
        card = next(x for x in page["events"] if x["id"] == eid)
        check("summary shows the winner and the viewer's place", card["winner"] == team_a.name and card["viewerPlace"] == 1)

        print("one-sided reports, no-shows and minimums (1v1 double elimination)")
        de = await svc.create_event(host_uid, event_body(host, "DE", team_size = "1v1", format = "double_elim", best_of = 1,
                                                          no_show_minutes = 5, min_entrants = 3, starts_at = NOW - timedelta(hours = 2)), db)
        did = de["id"]
        for k in ("a2", "b2", "c2", "d2"):
            de = await svc.register(uid[k], did, Registration(email = "p@example.com"), db)
        seeds = [e["id"] for e in de["entrants"]]
        await svc.save_draft(host_uid, did, seeds, db)
        for k in ("c2", "d2"):
            await svc.unregister(uid[k], did, db)
        await expect_http(db, "below the minimum: won't start", svc.publish(host_uid, did, db), 409)
        await svc.register(uid["c2"], did, Registration(email = "p@example.com"), db)
        await expect_http(db, "field changed: rebuilt for review", svc.publish(host_uid, did, db), 409)
        de = await svc.publish(host_uid, did, db)
        ready = next(code for code, v in de["bracket"]["matches"].items() if v["state"] == "ready")
        first = match(de, ready)
        mine = {e["id"]: k for e in de["entrants"] for k in ("a2", "b2", "c2") if e["name"] == pr[k].username}
        a_side, b_side = mine.get(first["a"]), mine.get(first["b"])
        await svc.report(uid[a_side], did, ready, MatchScore(score_a = 1, score_b = 0), db)
        bracket_id = (await db.execute(select(Brackets.id).join(BracketEvents).where(BracketEvents.public_id == did))).scalar()
        m = (await db.execute(select(BracketMatches).where(BracketMatches.bracket_id == bracket_id).where(BracketMatches.code == ready))).scalars().one()
        await backdate(db, MatchReports, MatchReports.match_id == m.id, created_at = NOW - timedelta(minutes = 6))
        raised = await match_svc.flag_one_sided_reports(db)
        await db.commit()
        check("an unanswered report goes to the queue after 5 minutes", raised == 1)
        check("not raised twice", await match_svc.flag_one_sided_reports(db) == 0)
        await svc.report(uid[b_side], did, ready, MatchScore(score_a = 1, score_b = 0), db)
        q = await queue_svc.queue(host_uid, did, db)
        check("the other side agreeing closes it", not q["open"] and q["closed"][0]["status"] == "resolved")
        de = await svc.get_event(did, host_uid, db)
        lr = next(code for code, v in de["bracket"]["matches"].items() if v["state"] == "ready")
        lm = match(de, lr)
        side = mine.get(lm["a"]) or mine.get(lm["b"])
        await expect_http(db, "no-show not before the window", svc.report_no_show(uid[side], did, lr, db), 409)
        row = (await db.execute(select(BracketMatches).where(BracketMatches.bracket_id == bracket_id).where(BracketMatches.code == lr))).scalars().one()
        await backdate(db, BracketMatches, BracketMatches.id == row.id, ready_at = NOW - timedelta(minutes = 6))
        await svc.report_no_show(uid[side], did, lr, db)
        q = await queue_svc.queue(host_uid, did, db)
        check("the no-show is in the queue", q["open"][0]["type"] == "no_show")
        ev = await svc.rule(host_uid, did, lr, Ruling(action = "award", score_a = 1 if lm["a"] and mine.get(lm["a"]) == side else 0,
                                                      score_b = 0 if lm["a"] and mine.get(lm["a"]) == side else 1, forfeit = True), db)
        q = await queue_svc.queue(host_uid, did, db)
        check("ruling on it resolves the queue item", not q["open"])

        print("swiss keeps its pairings")
        sw = await svc.create_event(host_uid, event_body(host, "Swiss", team_size = "1v1", format = "swiss", best_of = 1, starts_at = NOW + timedelta(days = 3)), db)
        sid = sw["id"]
        for k in ("a0", "b0", "c0", "d0"):
            sw = await svc.register(uid[k], sid, Registration(email = "p@example.com"), db)
        await svc.save_draft(host_uid, sid, [e["id"] for e in sw["entrants"]], db)
        sw = await svc.publish(host_uid, sid, db)
        for code in ("sw0m0", "sw0m1"):
            await svc.rule(host_uid, sid, code, Ruling(action = "award", score_a = 1, score_b = 0), db)
        sw = await svc.get_event(sid, host_uid, db)
        before = [(m["a"], m["b"]) for m in sw["bracket"]["built"]["rounds"][1]]
        sw = await svc.rule(host_uid, sid, "sw0m0", Ruling(action = "award", score_a = 0, score_b = 1), db)
        after = [(m["a"], m["b"]) for m in sw["bracket"]["built"]["rounds"][1]]
        check("changing round 1 doesn't re-pair round 2", before == after and all(a and b for a, b in after))

        print("double elimination to a grand final reset")
        d4 = await svc.create_event(host_uid, event_body(host, "DE4", team_size = "1v1", format = "double_elim", best_of = 1, starts_at = NOW + timedelta(days = 4)), db)
        d4id = d4["id"]
        for k in ("a0", "b0", "c0", "d0"):
            d4 = await svc.register(uid[k], d4id, Registration(email = "p@example.com"), db)
        await svc.save_draft(host_uid, d4id, [e["id"] for e in d4["entrants"]], db)
        d4 = await svc.publish(host_uid, d4id, db)
        top = d4["bracket"]["seeds"][0]
        played = 0
        while d4["stage"] != "complete" and played < 20:
            code, view = next((c, v) for c, v in d4["bracket"]["matches"].items() if v["state"] == "ready")
            m = match(d4, code)
            # The top seed wins everything except the first grand final, which forces the reset.
            a_wins = (m["a"] == top) != (code == "gf1")
            d4 = await svc.rule(host_uid, d4id, code, Ruling(action = "award", score_a = 1 if a_wins else 0, score_b = 0 if a_wins else 1), db)
            played += 1
        built = d4["bracket"]["built"]
        check("the reset is played and the top seed is champion", [m["id"] for m in built["grandFinal"]] == ["gf1", "gf2"]
              and built["champion"] == top and d4["stage"] == "complete")
        check("2n - 2 + reset matches for 4 entrants", played == 7)

        print("response models")
        from app.schemas.brackets import EventOut, EventPage, QueueOut
        out = EventOut.model_validate(await svc.get_event(eid, host_uid, db)).model_dump(by_alias = True)
        raw = await svc.get_event(eid, host_uid, db)
        check("EventOut keeps every field the service sends", set(raw) <= set(out) and out["entrants"][0]["pass"] == raw["entrants"][0]["pass"])
        check("entrant emails survive the response model", out["entrants"][0]["email"] == raw["entrants"][0]["email"])
        EventPage.model_validate(await svc.hosting_events(host_uid, db))
        QueueOut.model_validate(await queue_svc.queue(host_uid, eid, db))
        check("page and queue models validate", True)

        print("notifications")
        mine_notes = await list_notifications(uid["a0"], db)
        await mark_read(uid["a0"], [uuid.UUID(mine_notes[0]["id"])], db)
        await backdate(db, Notifications, Notifications.id == uuid.UUID(mine_notes[0]["id"]), read_at = NOW - timedelta(days = 91))
        removed = await delete_old_read(db)
        await db.commit()
        check("read notifications deleted after 90 days", removed >= 1)

        print("paging")
        first_page = await svc.hosting_events(host_uid, db, limit = 2)
        second = await svc.hosting_events(host_uid, db, first_page["nextCursor"], 2)
        ids_seen = [x["id"] for x in first_page["events"] + second["events"]]
        check("pages don't repeat and continue with a cursor", len(set(ids_seen)) == len(ids_seen) == 4 and first_page["nextCursor"])

        print("team ownership on delete")
        await backdate(db, TeamMembers, (TeamMembers.team_id == team_e.id) & (TeamMembers.profile_id == pr["e1"].id), joined_at = NOW - timedelta(days = 1))
        teams_left = await team_svc.before_profiles_deleted({pr["e0"].id}, db)
        await db.execute(delete(Profiles).where(Profiles.id == pr["e0"].id))
        await team_svc.after_profiles_deleted(teams_left, db)
        await db.commit()
        owner = (await db.execute(select(Profiles.username).join(team_svc.Teams, team_svc.Teams.owner_profile_id == Profiles.id)
                                  .where(team_svc.Teams.id == team_e.id))).scalar()
        check("the team passes to the earliest joiner", owner == pr["e1"].username)

async def cleanup():
    async with AsyncSessionLocal() as db:
        await db.rollback()
        if accounts:
            await db.execute(delete(UsersParent).where(UsersParent.id.in_(accounts)))
            await db.commit()
        left = (await db.execute(select(Profiles.id).where(Profiles.username.like(f"e2e-{RUN}-%")))).all()
        print(f"cleanup: removed {len(accounts)} test accounts, {len(left)} profiles left")
    await engine.dispose()

async def run():
    try:
        await main()
    except Exception:
        import traceback
        traceback.print_exc(limit = 6)
        failures.append("crashed")
    finally:
        await cleanup()
    print(f"\n{len(failures)} failure(s)")
    for f in failures:
        print(" -", f)
    sys.exit(1 if failures else 0)

asyncio.run(run())
