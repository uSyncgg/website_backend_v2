"""
End-to-end check of bracket hosting against the real database, through the service layer (the
routers are thin). Creates throwaway accounts named e2e-*, runs a 4v4 event from sign-up to the
final, and deletes everything it made at the end (accounts cascade to profiles, teams, events,
entries and notifications).

Run from usync_backend:  uv run python scripts/e2e_brackets.py
"""
import asyncio
import sys
import uuid
from types import SimpleNamespace as NS
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

from fastapi import HTTPException
from sqlalchemy import select, delete
from app.core.db import AsyncSessionLocal, engine
from app.models.users import UsersParent, Profiles
from app.models.teams import Teams, TeamMembers, TeamInvites
from app.models.brackets import Notifications
from app.services import brackets as svc
from app.services import teams as team_svc
from app.services.notifications import list_notifications
from app.schemas.brackets import EventCreate, Registration, MatchScore, EventDelay

RUN = uuid.uuid4().hex[:6]
accounts = []
failures = []

def check(label, ok):
    print(("  PASS " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)

async def expect_http(label, coro, status):
    try:
        await coro
        check(f"{label} (expected {status}, got success)", False)
    except HTTPException as e:
        check(f"{label} -> {e.status_code}: {e.detail}", e.status_code == status)

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

async def make_team(db, owner_profile, name, game, members, captain=None):
    team = team_svc.new_team(owner_profile.id, f"{name} {RUN}", game)
    db.add(team)
    await db.flush()
    for p in members:
        db.add(TeamMembers(team_id = team.id, profile_id = p.id, role = "captain" if p is captain else "player"))
    await db.flush()
    return NS(id = team.id, public_id = team.public_id)

async def notes(db, uid, type_=None):
    items = await list_notifications(uid, db)
    return [n for n in items if type_ is None or n["type"] == type_]

async def main():
    async with AsyncSessionLocal() as db:
        print("setup")
        host_uid, host = await make_account(db, "host", "host")
        a_uids, a_players = zip(*[await make_account(db, f"a{i}") for i in range(4)])
        b_uids, b_players = zip(*[await make_account(db, f"b{i}") for i in range(4)])
        c_uids, c_players = zip(*[await make_account(db, f"c{i}") for i in range(4)])
        team_a = await make_team(db, a_players[0], "Alpha", "Halo", a_players[:3], captain = a_players[0])
        team_b = await make_team(db, b_players[0], "Bravo", "Halo", b_players, captain = b_players[0])
        team_c = await make_team(db, c_players[0], "Charlie", "Halo", c_players, captain = c_players[0])
        team_wrong = await make_team(db, a_players[1], "WrongGame", "Valorant", a_players[1:], captain = a_players[1])
        team_nogame = await make_team(db, a_players[2], "NoGame", None, a_players[2:], captain = a_players[2])
        invite = TeamInvites(id = uuid.uuid4(), team_id = team_a.id, profile_id = a_players[3].id)
        db.add(invite)
        invite = NS(id = invite.id)
        await db.commit()

        print("create event")
        await expect_http("a player can't post an event", svc.create_event(a_uids[0], EventCreate(
            host_profile = a_players[0].public_id, name = "x", event_type = "tournament", game = "Halo", format = "single_elim",
            team_size = "4v4", best_of = 3, starts_at = datetime.now(timezone.utc)), db), 403)
        ev = await svc.create_event(host_uid, EventCreate(
            host_profile = host.public_id, name = f"E2E Halo 4v4 {RUN}", event_type = "tournament", game = "Halo",
            format = "single_elim", team_size = "4v4", best_of = 3, max_entrants = 2, no_show_minutes = 10,
            starts_at = datetime.now(timezone.utc) - timedelta(minutes = 30)), db)
        eid = ev["id"]
        check("event created with team size 4v4", ev["teamSize"] == "4v4" and ev["canManage"])

        print("sign-ups")
        reg = lambda team: Registration(team_id = team.public_id, email = "cap@example.com")
        await expect_http("team game must match", svc.register(a_uids[1], eid, reg(team_wrong), db), 409)
        await expect_http("team without a game can't enter", svc.register(a_uids[2], eid, reg(team_nogame), db), 409)
        await expect_http("a plain player can't enter their team", svc.register(b_uids[2], eid, reg(team_b), db), 403)
        await expect_http("1v1-style sign-up rejected for a team event", svc.register(b_uids[2], eid, Registration(email = "x@example.com"), db), 422)
        ev = await svc.register(a_uids[0], eid, reg(team_a), db)
        alpha = next(e for e in ev["entrants"] if e["teamId"] == team_a.public_id)
        check("3-player team is pending in a 4v4", alpha["status"] == "pending" and alpha["rosterSize"] == 3)
        await expect_http("can't enter the same team twice", svc.register(a_uids[0], eid, reg(team_a), db), 409)
        ev = await svc.register(b_uids[0], eid, reg(team_b), db)
        ev = await svc.register(c_uids[0], eid, reg(team_c), db)
        status = {e["name"].split()[0]: e["status"] for e in ev["entrants"]}
        check(f"pending Alpha takes no spot, so Bravo and Charlie both fit in max 2: {status}",
              status == {"Alpha": "pending", "Bravo": "signed_up", "Charlie": "signed_up"} or status == {"Alpha": "pending", "Bravo": "signed_up", "Charlie": "signed_up"})

        print("4th player joins Alpha")
        await team_svc.respond_to_invite(a_uids[3], invite.id, True, db)
        ev = await svc.get_event(eid, host_uid, db)
        status = {e["name"].split()[0]: e["status"] for e in ev["entrants"]}
        check(f"Alpha now has 4 players but the event is full, so it waits: {status}", status["Alpha"] == "pending")
        check("Alpha's owner got 'team_joined'", len(await notes(db, a_uids[0], "team_joined")) == 1)

        print("Charlie unregisters -> Alpha takes the spot")
        ev = await svc.unregister(c_uids[0], eid, db)
        status = {e["name"].split()[0]: e["status"] for e in ev["entrants"]}
        check(f"Alpha promoted to signed up: {status}", status == {"Alpha": "signed_up", "Bravo": "signed_up"})
        check("every Alpha player got 'team_signed_up'", all([len(await notes(db, u, "team_signed_up")) == 1 for u in a_uids]))

        print("Alpha drops to 3 before the start -> pending again")
        await team_svc.remove_member(a_uids[0], team_a.public_id, a_players[3].public_id, db)
        ev = await svc.get_event(eid, host_uid, db)
        check("Alpha back to pending", next(e for e in ev["entrants"] if e["name"].startswith("Alpha"))["status"] == "pending")
        check("Alpha got 'team_pending'", len(await notes(db, a_uids[0], "team_pending")) == 1)
        invite2 = TeamInvites(id = uuid.uuid4(), team_id = team_a.id, profile_id = a_players[3].id)
        db.add(invite2)
        invite2 = NS(id = invite2.id)
        await db.commit()
        await team_svc.respond_to_invite(a_uids[3], invite2.id, True, db)
        ev = await svc.get_event(eid, host_uid, db)
        ids = {e["name"].split()[0]: e["id"] for e in ev["entrants"]}
        check("Alpha signed up again after the 4th rejoins", next(e for e in ev["entrants"] if e["name"].startswith("Alpha"))["status"] == "signed_up")

        print("seeding and publishing")
        ev = await svc.register(c_uids[0], eid, reg(team_c), db)
        ids = {e["name"].split()[0]: e["id"] for e in ev["entrants"]}
        await expect_http("pending teams can't be seeded", svc.save_draft(host_uid, eid, [ids["Alpha"], ids["Charlie"]], db), 422)
        await expect_http("only the host can seed", svc.save_draft(a_uids[0], eid, [ids["Alpha"], ids["Bravo"]], db), 404)
        await svc.save_draft(host_uid, eid, [ids["Alpha"], ids["Bravo"]], db)
        ev = await svc.publish(host_uid, eid, db)
        check("published: sign-ups closed, rosters locked", ev["bracket"]["published"] and not ev["registrationOpen"] and ev["rostersLocked"])
        check("both teams got 'match_ready'", len(await notes(db, a_uids[2], "match_ready")) == 1 and len(await notes(db, b_uids[3], "match_ready")) == 1)
        check("pending Charlie got no match", len(await notes(db, c_uids[0], "match_ready")) == 0)

        print("rosters frozen while live")
        await expect_http("can't remove a player while live", team_svc.remove_member(a_uids[0], team_a.public_id, a_players[3].public_id, db), 409)
        await expect_http("can't leave while live", team_svc.leave_team(a_uids[1], team_a.public_id, db), 409)
        await expect_http("can't delete the team while live", team_svc.delete_team(a_uids[0], team_a.public_id, db, None), 409)

        print("score reporting")
        ev = await svc.get_event(eid, a_uids[2], db)
        check("a non-captain sees their entrant but can't act", ev["viewerEntrantId"] == ids["Alpha"] and not ev["viewerCanAct"])
        await expect_http("a non-captain can't report", svc.player_report(a_uids[2], eid, "r0m0", MatchScore(score_a = 2, score_b = 1), db), 403)
        await expect_http("tied scores rejected", svc.player_report(a_uids[0], eid, "r0m0", MatchScore(score_a = 1, score_b = 1), db), 422)
        await expect_http("someone outside the match can't report", svc.player_report(c_uids[0], eid, "r0m0", MatchScore(score_a = 2, score_b = 1), db), 409)
        await svc.player_report(a_uids[0], eid, "r0m0", MatchScore(score_a = 2, score_b = 1), db)
        check("Bravo got 'opponent_reported'", len(await notes(db, b_uids[1], "opponent_reported")) == 1)
        await svc.player_report(b_uids[0], eid, "r0m0", MatchScore(score_a = 0, score_b = 2), db)
        check("both sides got 'score_disputed'", len(await notes(db, a_uids[1], "score_disputed")) == 1 and len(await notes(db, b_uids[1], "score_disputed")) == 1)
        check("the host got 'score_disputed'", len(await notes(db, host_uid, "score_disputed")) == 1)

        print("no-show")
        await svc.report_no_show(b_uids[0], eid, "r0m0", db)
        check("Alpha told it was reported as a no-show", any(n["data"].get("against_you") for n in await notes(db, a_uids[0], "no_show_reported")))
        check("the host told about the no-show", len(await notes(db, host_uid, "no_show_reported")) == 1)

        print("host decides")
        ev = await svc.host_report(host_uid, eid, "r0m0", MatchScore(score_a = 2, score_b = 0), db)
        check("host's call is final and clears reports", ev["bracket"]["results"]["r0m0"]["scoreA"] == 2 and "r0m0" not in ev["bracket"]["reports"])
        check("event complete -> rosters unlock", not ev["rostersLocked"])
        ev = await svc.revert_decision(host_uid, eid, ev["bracket"]["log"][-1]["id"], db)
        check("revert puts the reports back", "r0m0" not in ev["bracket"]["results"] and len(ev["bracket"]["reports"]["r0m0"]) == 2)
        ev = await svc.host_report(host_uid, eid, "r0m0", MatchScore(score_a = 2, score_b = 0), db)

        print("delay")
        await svc.delay_start(host_uid, eid, EventDelay(starts_at = datetime.now(timezone.utc) + timedelta(hours = 1), reason = "Server issues"), db)
        check("every player on both teams got 'event_delayed'", all([len(await notes(db, u, "event_delayed")) == 1 for u in a_uids + b_uids]))
        check("entered list includes the event for a teammate", any(e["id"] == eid for e in await svc.entered_events(a_uids[2], db)))
        check("hosting list includes it for the host", any(e["id"] == eid for e in await svc.hosting_events(host_uid, db)))
        anon = await svc.get_event(eid, None, db)
        check("anonymous viewers don't see emails", all("email" not in e for e in anon["entrants"]))

        print("1v1 event")
        solo = await svc.create_event(host_uid, EventCreate(
            host_profile = host.public_id, name = f"E2E 1v1 {RUN}", event_type = "tournament", game = "Halo",
            format = "double_elim", team_size = "1v1", best_of = 1, max_entrants = 2, starts_at = datetime.now(timezone.utc)), db)
        await expect_http("team entries rejected for 1v1", svc.register(a_uids[0], solo["id"], reg(team_a), db), 422)
        ev = await svc.register(a_uids[0], solo["id"], Registration(email = "p@example.com"), db)
        check("solo entrant signed up under their username", ev["entrants"][0]["status"] == "signed_up" and ev["entrants"][0]["name"] == a_players[0].username)
        await svc.register(c_uids[0], solo["id"], Registration(email = "p@example.com"), db)
        await expect_http("1v1 event full", svc.register(b_uids[0], solo["id"], Registration(email = "p@example.com"), db), 409)
        await expect_http("host can't host as a player-only account", svc.create_event(a_uids[1], EventCreate(
            host_profile = host.public_id, name = "x", event_type = "tournament", game = "Halo", format = "single_elim",
            team_size = "1v1", best_of = 1, starts_at = datetime.now(timezone.utc)), db), 403)

async def cleanup():
    async with AsyncSessionLocal() as db:
        if accounts:
            await db.execute(delete(UsersParent).where(UsersParent.id.in_(accounts)))
            await db.commit()
        left = (await db.execute(select(Profiles.id).where(Profiles.username.like(f"e2e-{RUN}-%")))).all()
        print(f"cleanup: removed {len(accounts)} test accounts, {len(left)} profiles left")
    await engine.dispose()

async def run():
    try:
        await main()
    finally:
        await cleanup()
    print(f"\n{len(failures)} failure(s)")
    for f in failures:
        print(" -", f)

asyncio.run(run())
