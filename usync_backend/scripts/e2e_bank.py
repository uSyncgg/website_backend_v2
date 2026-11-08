"""
Run with: uv run python scripts/e2e_bank.py

End-to-end check of the Bank against the real database inside ONE transaction that is rolled
back at the end: nothing it creates (accounts, profiles, teams, events, money) is kept.
"""
import asyncio
import secrets
import sys
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import AsyncSessionLocal, engine
from app.models import UsersParent, Profiles, Teams, TeamMembers, BracketEvents, Notifications
from app.models.wallet import WalletTransactions, PlayerEventResults
from app.schemas.brackets import Registration, MatchScore, Prize
from app.services import brackets as svc
from app.services import payouts, wallet

PASS = FAIL = 0


def check(label, ok, extra=""):
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += (not ok)
    print(("PASS " if ok else "FAIL ") + label + (f"  [{extra}]" if extra and not ok else ""))


async def fails(coro):
    try:
        await coro
        return None
    except HTTPException as e:
        return e.status_code


async def make_user(db, ptype, tag):
    uid = uuid.uuid4()
    db.add(UsersParent(id=uid, email=f"{tag}-{secrets.token_hex(3)}@example.test"))
    await db.flush()
    name = f"{tag}{secrets.token_hex(3)}"
    prof = Profiles(id=uuid.uuid4(), public_id="p-" + secrets.token_hex(5), account_id=uid, type=ptype, username=name, canonical_username=name.lower())
    db.add(prof)
    await db.flush()
    return uid, prof


async def make_team(db, owner, members, tag):
    t = Teams(id=uuid.uuid4(), public_id="t-" + secrets.token_hex(5), owner_profile_id=owner.id, name=tag + secrets.token_hex(2),
              canonical_name=(tag + secrets.token_hex(3)).lower(), game="Call of Duty")
    db.add(t)
    await db.flush()
    for i, m in enumerate(members):
        db.add(TeamMembers(team_id=t.id, profile_id=m.id, role="captain" if i == 0 else "player"))
    await db.flush()
    return t


async def make_event(db, host, tiers, prizes, name="E2E Cup", team_size=2):
    ev = BracketEvents(
        id=uuid.uuid4(), public_id="evt-" + secrets.token_hex(5), host_profile_id=host.id, name=name, event_type="tournament",
        game="Call of Duty", format="single_elim", team_size=team_size, best_of=1, starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        payments={"enabled": True, "tiers": tiers} if tiers else {}, prizes=prizes, registration_open=True,
    )
    db.add(ev)
    await db.flush()
    return ev


def reg(team, pay_for=(), pass_name="Std"):
    return Registration.model_validate({"teamId": team.public_id, "email": "x@example.test", "pass": pass_name, "payFor": list(pay_for)})


async def bal(db, uid):
    return await wallet.balance_of(uid, db)


async def main():
    async with AsyncSessionLocal() as db:
        db.commit = db.flush  # nothing is kept; rolled back at the end
        try:
            await run(db)
        finally:
            await db.rollback()
    await engine.dispose()
    print(f"\n{PASS} passed, {FAIL} failed")


async def run(db):
    host_id, host = await make_user(db, "host", "host")
    a_id, a = await make_user(db, "player", "alice")
    b_id, b = await make_user(db, "player", "bob")
    c_id, c = await make_user(db, "player", "cara")
    d_id, d = await make_user(db, "player", "dan")
    t1 = await make_team(db, a, [a, b], "T1")
    t2 = await make_team(db, c, [c, d], "T2")
    ev = await make_event(db, host, [{"name": "Std", "price": "10"}], [{"place": 1, "amountCents": 1500}, {"place": 2, "percent": 20}])

    print("-- team sign-up with payments")
    await wallet.credit(a_id, 5000, "deposit", "Deposit", db)
    await wallet.credit(c_id, 3000, "deposit", "Deposit", db)

    out = await svc.register(a_id, ev.public_id, reg(t1), db)
    e1 = next(e for e in out["entrants"] if e["name"] == t1.name)
    check("captain is charged own share, $10.50", await bal(db, a_id) == 5000 - 1050, await bal(db, a_id))
    check("teammate with no balance leaves the team pending, not fully paid", e1["status"] == "pending" and e1["fullyPaid"] is False, e1)
    check("host/team view lists who has paid", [p["paid"] for p in e1["payment"]["players"]] == [True, False], e1["payment"])
    due = (await db.execute(select(Notifications).where(Notifications.account_id == b_id).where(Notifications.type == "entry_payment_due"))).scalars().all()
    check("unfunded teammate is notified", len(due) == 1)

    await wallet.credit(b_id, 2000, "deposit", "Deposit", db)
    await svc.settle_payments_for_account(b_id, db)
    out = await svc.get_event(ev.public_id, a_id, db)
    e1 = next(e for e in out["entrants"] if e["name"] == t1.name)
    check("after the teammate adds funds their share is taken", await bal(db, b_id) == 2000 - 1050, await bal(db, b_id))
    check("team is now signed up and fully paid", e1["status"] == "signed_up" and e1["fullyPaid"] is True, e1["status"])

    print("-- paying for a teammate")
    await svc.register(c_id, ev.public_id, reg(t2, pay_for=[d.username]), db)
    check("captain covering a teammate pays both shares ($21)", await bal(db, c_id) == 3000 - 2100, await bal(db, c_id))
    check("covered teammate's balance is untouched", await bal(db, d_id) == 0)
    out = await svc.get_event(ev.public_id, host_id, db)
    e2 = next(e for e in out["entrants"] if e["name"] == t2.name)
    check("host sees the covered share was paid by the captain", e2["payment"]["players"][1] == {"username": d.username, "paid": True, "paidBy": c.username}, e2["payment"])

    print("-- leaving before registration closes refunds")
    await svc.unregister(a_id, ev.public_id, db)
    check("captain's share refunded", await bal(db, a_id) == 5000, await bal(db, a_id))
    check("teammate's share refunded to the teammate", await bal(db, b_id) == 2000, await bal(db, b_id))
    refunds = (await db.execute(select(WalletTransactions).where(WalletTransactions.type == "entry_refund").where(WalletTransactions.account_id.in_([a_id, b_id])))).scalars().all()
    check("refunds point at the charge they reverse", all(r.related_id for r in refunds) and len(refunds) == 2)
    await svc.register(a_id, ev.public_id, reg(t1), db)
    check("re-entering charges both again", await bal(db, a_id) == 5000 - 1050 and await bal(db, b_id) == 2000 - 1050)

    print("-- a player leaves a team entry")
    await db.delete(await db.get(TeamMembers, (t1.id, b.id)))
    await db.flush()
    await svc.sync_team(t1.id, db)
    check("a player who leaves while sign-ups are open is refunded", await bal(db, b_id) == 2000, await bal(db, b_id))
    db.add(TeamMembers(team_id=t1.id, profile_id=b.id, role="player"))
    await db.flush()
    await svc.sync_team(t1.id, db)
    check("rejoining pays the share again", await bal(db, b_id) == 2000 - 1050, await bal(db, b_id))

    print("-- bracket, close and payouts")
    entries = await svc._entries(ev.id, db)
    seeds = [str(e.id) for e in entries]
    await svc.save_draft(host_id, ev.public_id, seeds, db)
    await svc.publish(host_id, ev.public_id, db)
    event = await svc._event_by_public_id(ev.public_id, db)
    built = svc._built(event)
    match = built["rounds"][0][0]
    check("can't close before the bracket is decided (409)", await fails(payouts.close_event(host_id, ev.public_id, db)) == 409)
    await svc.host_report(host_id, ev.public_id, match["id"], MatchScore(score_a=2, score_b=1), db)
    winner_entry = match["a"]
    check("close works once the bracket is decided", (await payouts.close_event(host_id, ev.public_id, db))["closedAt"] is not None)
    event = await svc._event_by_public_id(ev.public_id, db)
    check("payout is due 45 minutes after closing", abs((event.payout_due_at - event.closed_at).total_seconds() - 45 * 60) < 1)
    reopened = await payouts.reopen_event(host_id, ev.public_id, db)
    check("reopening clears the pending payout", reopened["closedAt"] is None and reopened["payoutAt"] is None)
    await payouts.close_event(host_id, ev.public_id, db)
    fin = await payouts.event_finances(host_id, ev.public_id, db)
    check("host finances: $40 collected, $0.50 fee per share", fin["collectedCents"] == 4000 and fin["feesCents"] == 200, fin)
    check("host finances project prizes $23 and host net $16.57", fin["prizesCents"] == 2300 and fin["hostNetCents"] == 1657 and fin["usyncFeeCents"] == 43, fin)

    event = await svc._event_by_public_id(ev.public_id, db)
    names = {a_id: "a", b_id: "b", c_id: "c", d_id: "d"}
    before = {k: await bal(db, v) for k, v in dict(a=a_id, b=b_id, c=c_id, d=d_id, host=host_id).items()}
    event.payout_due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    check("settle pays the event out", await payouts.settle_event(event, db) is True)
    check("a second settle does nothing", await payouts.settle_event(event, db) is False)

    t1_entry = next(e for e in await svc._entries(ev.id, db) if e.team_id == t1.id)
    t1_won = winner_entry == str(t1_entry.id)
    first, second = ((a_id, b_id), (c_id, d_id)) if t1_won else ((c_id, d_id), (a_id, b_id))
    for acct in first:
        check("1st place player gets half of $15 = $7.50", await bal(db, acct) - before[names[acct]] == 750)
    for acct in second:
        check("2nd place player gets half of 20% = $4.00", await bal(db, acct) - before[names[acct]] == 400)
    check("host keeps revenue - prizes - 2.5% = $16.57", await bal(db, host_id) - before["host"] == 1657, await bal(db, host_id) - before["host"])
    prize_txs = (await db.execute(select(WalletTransactions).where(WalletTransactions.type == "prize").where(WalletTransactions.event_public_id == ev.public_id))).scalars().all()
    check("4 prize transactions recorded", len(prize_txs) == 4)
    hp = (await db.execute(select(WalletTransactions).where(WalletTransactions.type == "host_payout").where(WalletTransactions.event_public_id == ev.public_id))).scalars().one()
    check("host payout records the 2.5% fee ($0.43)", hp.fee_cents == 43)

    rows = (await db.execute(select(PlayerEventResults).where(PlayerEventResults.event_id == ev.id))).scalars().all()
    by_profile = {r.profile_id: r for r in rows}
    check("a result row per player (4)", len(rows) == 4)
    win_profiles = [a.id, b.id] if t1_won else [c.id, d.id]
    lose_profiles = [c.id, d.id] if t1_won else [a.id, b.id]
    check("winners: placement 1, 1 win, $7.50 earned", all(by_profile[p].placement == 1 and by_profile[p].wins == 1 and by_profile[p].losses == 0 and by_profile[p].earnings_cents == 750 for p in win_profiles))
    check("runners-up: placement 2, 1 loss, $4.00 earned", all(by_profile[p].placement == 2 and by_profile[p].losses == 1 and by_profile[p].earnings_cents == 400 for p in lose_profiles))
    stats = await wallet.get_stats(a_id, db)
    check("stats totals the results", stats["eventsPlayed"] == 1 and stats["wins"] + stats["losses"] == 1, stats)
    check("cancelling a paid-out event is refused (409)", await fails(payouts.cancel_event(host_id, ev.public_id, db)) == 409)

    print("-- cancelling an event refunds everyone")
    ev2 = await make_event(db, host, [{"name": "Std", "price": "10"}], [], name="Cancel Cup")
    ta = await make_team(db, a, [a, b], "TA")
    await wallet.credit(a_id, 5000, "deposit", "Deposit", db)
    await wallet.credit(b_id, 5000, "deposit", "Deposit", db)
    a1, b1 = await bal(db, a_id), await bal(db, b_id)
    await svc.register(a_id, ev2.public_id, reg(ta), db)
    check("both players charged for the second event", await bal(db, a_id) == a1 - 1050 and await bal(db, b_id) == b1 - 1050)
    check("a non-host can't cancel (404)", await fails(payouts.cancel_event(a_id, ev2.public_id, db)) == 404)
    out = await payouts.cancel_event(host_id, ev2.public_id, db)
    check("cancel refunds every share, fee included", await bal(db, a_id) == a1 and await bal(db, b_id) == b1)
    check("cancelled event shows as cancelled with sign-ups closed", out["cancelledAt"] and out["registrationOpen"] is False)
    check("sign-ups to a cancelled event are refused (409)", await fails(svc.register(a_id, ev2.public_id, reg(ta), db)) == 409)
    note = (await db.execute(select(Notifications).where(Notifications.account_id == a_id).where(Notifications.type == "event_cancelled"))).scalars().all()
    check("players are told it was cancelled", len(note) == 1)

    print("-- leaving after registration closes forfeits")
    ev3 = await make_event(db, host, [{"name": "Std", "price": "10"}], [], name="Late Cup")
    tb = await make_team(db, a, [a, b], "TB")
    x1, y1 = await bal(db, a_id), await bal(db, b_id)
    await svc.register(a_id, ev3.public_id, reg(tb), db)
    event3 = await svc._event_by_public_id(ev3.public_id, db)
    event3.registration_open = False
    await db.flush()
    await svc.unregister(a_id, ev3.public_id, db)
    check("leaving after sign-ups close does not refund", await bal(db, a_id) == x1 - 1050 and await bal(db, b_id) == y1 - 1050)
    fin3 = await payouts.event_finances(host_id, ev3.public_id, db)
    check("forfeited shares still count as host revenue", fin3["collectedCents"] == 2000 and fin3["forfeitedPlayers"] == 2, fin3)
    await payouts.cancel_event(host_id, ev3.public_id, db)
    check("cancelling later returns the forfeited money too", await bal(db, a_id) == x1 and await bal(db, b_id) == y1)

    print("-- free events and prize rules")
    ev4 = await make_event(db, host, [], [], name="Free Cup")
    tc = await make_team(db, a, [a, b], "TC")
    out = await svc.register(a_id, ev4.public_id, reg(tc, pass_name=None), db)
    check("free events don't charge", await bal(db, a_id) == x1 and out["entrants"][0]["status"] == "signed_up")
    try:
        svc._prizes_stored([Prize(place=1, amount=10)], {})
        check("prizes need payments turned on", False)
    except HTTPException as e:
        check("prizes need payments turned on", e.status_code == 422)
    try:
        svc._prizes_stored([Prize(place=1, percent=70), Prize(place=2, percent=40)], {"enabled": True})
        check("percents over 100 are refused", False)
    except HTTPException as e:
        check("percents over 100 are refused", e.status_code == 422)
    plan, scaled = payouts.plan_prizes([{"place": 1, "amountCents": 10000}, {"place": 2, "percent": 50}], 4000, None)
    check("prizes over the revenue scale down (never pay out more than collected)", scaled and sum(c for _, c in plan) <= 4000, plan)
    check("host split never goes negative", payouts.host_split(1000, 5000) == {"hostProfitCents": 0, "usyncFeeCents": 0, "hostNetCents": 0})

    print("-- withdrawals: net 30 job, receipts, exports")
    await wallet.request_withdrawal(a_id, Decimal("5"), db)
    tx = (await db.execute(select(WalletTransactions).where(WalletTransactions.account_id == a_id).where(WalletTransactions.type == "withdrawal"))).scalars().first()
    tx.available_on = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db.flush()

    class SameSession:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *args):
            return False

    sent = []
    import app.services.email as email_mod

    async def fake_email(to, amount_cents, number):
        sent.append((to, amount_cents, number))

    wallet.AsyncSessionLocal = lambda: SameSession()
    email_mod.send_withdrawal_paid_email = fake_email
    check("the job marks a due withdrawal paid", await wallet.settle_due_withdrawals() >= 1)
    await db.refresh(tx)
    check("status is completed with a paid-on time", tx.status == "completed" and tx.settled_at is not None)
    check("an email is sent for it", len(sent) >= 1 and sent[0][1] == 500 and sent[0][2].startswith("USY-"), sent)
    check("running the job again pays nothing twice", await wallet.settle_due_withdrawals() == 0)
    check("a paid withdrawal can't be cancelled (409)", await fails(wallet.cancel_withdrawal(a_id, tx.id, db)) == 409)

    entry_tx = (await db.execute(select(WalletTransactions).where(WalletTransactions.account_id == a_id).where(WalletTransactions.type == "entry_fee"))).scalars().first()
    receipt = await wallet.get_receipt(a_id, entry_tx.id, db)
    check("receipt lines add up to the total", sum(l["cents"] for l in receipt["lines"]) == receipt["totalCents"] == 1050, receipt)
    prize_tx = next(t for t in prize_txs if t.account_id in (a_id, b_id, c_id, d_id))
    receipt = await wallet.get_receipt(prize_tx.account_id, prize_tx.id, db)
    check("prize receipt shows the prize", receipt["totalCents"] == prize_tx.amount_cents and receipt["receiptNumber"].startswith("USY-"))
    csv_text = await wallet.export_csv(a_id, None, db)
    check("CSV export has a header and a row per transaction", csv_text.splitlines()[0].startswith("Date (UTC)") and len(csv_text.splitlines()) > 5)
    year = datetime.now(timezone.utc).year
    tax = await wallet.tax_summary(a_id, year, db)
    check("tax summary totals entry fees and withdrawals", tax["withdrawalsPaidCents"] == 500 and tax["entryFeesCents"] > 0, tax)
    check("tax CSV renders", "tax summary" in (await wallet.tax_summary_csv(a_id, year, db)))
    check("CSV text starting with = is neutralised", wallet._safe("=cmd|x").startswith("'"))

    print("-- an unaffordable sign-up costs nothing (this rolls the session back, so it runs last)")
    e_id, e = await make_user(db, "player", "eve")
    te = await make_team(db, e, [e, d], "TE")
    ev5 = await make_event(db, host, [{"name": "Std", "price": "10"}], [], name="Broke Cup")
    check("a captain with no funds can't sign the team up (402)", await fails(svc.register(e_id, ev5.public_id, reg(te), db)) == 402)
    # (the rollback above removed the test data, so build a little more)
    host_id, host = await make_user(db, "host", "host2")
    e_id, e = await make_user(db, "player", "eve2")
    f_id, f = await make_user(db, "player", "fay")
    te = await make_team(db, e, [e, f], "TE2")
    ev5 = await make_event(db, host, [{"name": "Std", "price": "10"}], [], name="Cover Cup")
    await wallet.credit(e_id, 5000, "deposit", "Deposit", db)
    check("covering someone who isn't on the team is refused (422)", await fails(svc.register(e_id, ev5.public_id, reg(te, pay_for=["nobody"]), db)) == 422)


asyncio.run(main())
