"""
Pure bracket logic - the only copy. app.services.bracket_sync turns its output into
bracket_matches rows, and the API sends the built bracket to the frontend, which only draws it.

A bracket is built from (seeds + results); results come from the stored match rows. Swiss is the
exception that keeps its history: a round is paired once, stored, and passed back in as `fixed`
so it's never paired again (changing an earlier result only moves the standings).

A match is a dict: { id, round, slot, a, b, status, winner, loser, scoreA?, scoreB?, forfeit? }.
status is 'empty' (bye vs bye) | 'bye' | 'pending' (waiting on an earlier match) | 'ready' | 'complete'.
"""

import math

BYE = "__bye__"

# Formats with a single deciding final, the only ones a different finals best of applies to.
FINAL_FORMATS = ("single_elim", "double_elim")


def is_entrant(entrant_id) -> bool:
    return bool(entrant_id) and entrant_id != BYE


def seed_placement(size: int) -> list[int]:
    order = [1]
    while len(order) < size:
        total = len(order) * 2 + 1
        order = [s for seed in order for s in (seed, total - seed)]
    return order


def _next_power_of_two(n: int) -> int:
    size = 2
    while size < n:
        size *= 2
    return size


def same_matchup(record, match) -> bool:
    return bool(record) and record.get("a") == match["a"] and record.get("b") == match["b"]


def _decide(match: dict, results: dict) -> dict:
    a, b = match["a"], match["b"]
    if a == BYE and b == BYE:
        return {**match, "status": "empty", "winner": BYE, "loser": BYE}
    if a == BYE or b == BYE:
        other = b if a == BYE else a
        if not other:
            return {**match, "status": "pending", "winner": None, "loser": None}
        return {**match, "status": "bye", "winner": other, "loser": BYE}
    if not a or not b:
        return {**match, "status": "pending", "winner": None, "loser": None}
    r = results.get(match["id"])
    if not same_matchup(r, match):
        return {**match, "status": "ready", "winner": None, "loser": None}
    a_won = r["scoreA"] > r["scoreB"]
    return {
        **match, "status": "complete", "scoreA": r["scoreA"], "scoreB": r["scoreB"], "forfeit": bool(r.get("forfeit")),
        "winner": a if a_won else b, "loser": b if a_won else a,
    }


def build_single_elim(seeds: list, results: dict) -> dict:
    if len(seeds) < 2:
        return {"rounds": [], "champion": None}
    size = _next_power_of_two(len(seeds))
    placement = seed_placement(size)

    def seed_at(n):
        return seeds[n - 1] if n - 1 < len(seeds) else BYE

    def make(mid, rnd, slot, a, b):
        return _decide({"id": mid, "round": rnd, "slot": slot, "a": a, "b": b}, results)

    rounds = [[make(f"r0m{slot}", 0, slot, seed_at(placement[slot * 2]), seed_at(placement[slot * 2 + 1])) for slot in range(size // 2)]]
    rnd = 1
    while size // (2 ** (rnd + 1)) >= 1:
        prev = rounds[rnd - 1]
        rounds.append([make(f"r{rnd}m{slot}", rnd, slot, prev[slot * 2]["winner"], prev[slot * 2 + 1]["winner"]) for slot in range(len(prev) // 2)])
        rnd += 1

    final = rounds[-1][0]
    return {"rounds": rounds, "champion": final["winner"] if final["status"] == "complete" else None}


def build_double_elim(seeds: list, results: dict) -> dict:
    wb = build_single_elim(seeds, results)
    if not wb["rounds"]:
        return {"rounds": [], "losersRounds": [], "grandFinal": [], "champion": None}

    def make(mid, rnd, slot, a, b, bracket):
        return _decide({"id": mid, "round": rnd, "slot": slot, "a": a, "b": b, "bracket": bracket}, results)

    k = len(wb["rounds"])
    lr = []
    if k >= 2:
        first = wb["rounds"][0]
        lr.append([make(f"l0m{i}", 0, i, first[i * 2]["loser"], first[i * 2 + 1]["loser"], "losers") for i in range(len(first) // 2)])
        for j in range(1, k):
            prev = lr[-1]
            dropped = [m["loser"] for m in wb["rounds"][j]]
            ordered = list(reversed(dropped)) if j % 2 else dropped
            major = len(lr)
            lr.append([make(f"l{major}m{i}", major, i, ordered[i], m["winner"], "losers") for i, m in enumerate(prev)])
            if j < k - 1:
                p = lr[-1]
                minor = len(lr)
                lr.append([make(f"l{minor}m{i}", minor, i, p[i * 2]["winner"], p[i * 2 + 1]["winner"], "losers") for i in range(len(p) // 2)])

    wb_final = wb["rounds"][k - 1][0]
    wb_champ = wb_final["winner"] if wb_final["status"] == "complete" else None
    lb_champ = wb_final["loser"] if k == 1 else lr[-1][0]["winner"]

    gf1 = make("gf1", 0, 0, wb_champ, lb_champ, "grand")
    grand = [gf1]
    champion = None
    if gf1["status"] == "bye":
        champion = gf1["winner"]
    if gf1["status"] == "complete":
        if gf1["winner"] == wb_champ:
            champion = wb_champ
        else:
            gf2 = make("gf2", 1, 0, wb_champ, lb_champ, "grand")
            grand.append(gf2)
            if gf2["status"] == "complete":
                champion = gf2["winner"]

    return {"rounds": wb["rounds"], "losersRounds": lr, "grandFinal": grand, "champion": champion}


def build_round_robin(seeds: list, results: dict) -> dict:
    if len(seeds) < 2:
        return {"rounds": []}
    players = list(seeds) + ([None] if len(seeds) % 2 else [])
    n = len(players)
    rotating = players[1:]
    rounds = []
    for rnd in range(n - 1):
        lineup = [players[0], *rotating]
        matches = []
        for i in range(n // 2):
            a, b = lineup[i], lineup[n - 1 - i]
            if not a or not b:
                continue
            matches.append(_decide({"id": f"rr{rnd}m{i}", "round": rnd, "slot": i, "a": a, "b": b}, results))
        rounds.append(matches)
        rotating.insert(0, rotating.pop())
    return {"rounds": rounds}


def standings_of(seeds: list, matches: list[dict]) -> list[dict]:
    table = {sid: {"id": sid, "seed": i, "played": 0, "wins": 0, "losses": 0, "diff": 0} for i, sid in enumerate(seeds)}
    for m in matches:
        if m["status"] == "bye" and m["winner"] in table:
            table[m["winner"]]["wins"] += 1
            continue
        if m["status"] != "complete":
            continue
        margin = abs(m["scoreA"] - m["scoreB"])
        table[m["winner"]]["wins"] += 1
        table[m["winner"]]["diff"] += margin
        table[m["loser"]]["losses"] += 1
        table[m["loser"]]["diff"] -= margin
        table[m["a"]]["played"] += 1
        table[m["b"]]["played"] += 1
    return sorted(table.values(), key = lambda r: (-r["wins"], -r["diff"], r["seed"]))


PAIRING_BUDGET = 20000


def _pair_up(pool: list, played) -> list[tuple]:
    """
    Pairs a ranked list down the table with no rematches when that's possible (a bounded
    backtracking search, the same as bracket.js); otherwise pairs neighbours in order.
    """

    steps = 0

    def search(rest):
        nonlocal steps
        if not rest:
            return []
        steps += 1
        if steps > PAIRING_BUDGET:
            return None
        a, others = rest[0], rest[1:]
        for j, b in enumerate(others):
            if played(a, b):
                continue
            tail = search(others[:j] + others[j + 1:])
            if tail is not None:
                return [(a, b), *tail]
        return None

    found = search(pool)
    if found is not None:
        return found
    return [(pool[i], pool[i + 1]) for i in range(0, len(pool), 2)]


def swiss_round_count(n: int) -> int:
    return max(1, math.ceil(math.log2(n)))


def build_swiss(seeds: list, results: dict, fixed: dict | None = None) -> dict:
    """
    `fixed` ({round: [(code, a, b), ...]}) holds rounds already paired and stored; those pairings
    are kept as they are. A new round is paired only once every match before it is played.
    """

    fixed = fixed or {}
    if len(seeds) < 2:
        return {"rounds": [], "standings": []}
    total = swiss_round_count(len(seeds))
    per_round = len(seeds) // 2
    rounds: list[list[dict]] = []
    byes: set = set()
    met: set = set()

    def key(a, b):
        return "|".join(sorted([a, b]))

    for rnd in range(total):
        prev = rounds[rnd - 1] if rnd else None
        if prev and any(m["status"] not in ("complete", "bye") for m in prev):
            rounds.append([{"id": f"sw{rnd}m{i}", "round": rnd, "slot": i, "a": None, "b": None, "status": "pending", "winner": None, "loser": None}
                           for i in range(per_round)])
            continue
        if rnd in fixed:
            matches = []
            for code, a, b in fixed[rnd]:
                if a and b:
                    met.add(key(a, b) if b != BYE else f"{a}|bye")
                if b == BYE:
                    byes.add(a)
                matches.append(_decide({"id": code, "round": rnd, "slot": len(matches), "a": a, "b": b}, results))
            rounds.append(matches)
            continue
        pool = list(seeds) if rnd == 0 else [r["id"] for r in standings_of(seeds, [m for r in rounds for m in r])]
        bye = None
        if len(pool) % 2:
            i = len(pool) - 1
            while i > 0 and pool[i] in byes:
                i -= 1
            bye = pool.pop(i)
            byes.add(bye)
        pairs = []
        if rnd == 0:
            half = len(pool) // 2
            pairs = [(pool[i], pool[i + half]) for i in range(half)]
        else:
            pairs = _pair_up(pool, lambda a, b: key(a, b) in met)
        matches = []
        for i, (a, b) in enumerate(pairs):
            met.add(key(a, b))
            matches.append(_decide({"id": f"sw{rnd}m{i}", "round": rnd, "slot": i, "a": a, "b": b}, results))
        if bye:
            matches.append(_decide({"id": f"sw{rnd}bye", "round": rnd, "slot": len(pairs), "a": bye, "b": BYE}, results))
        rounds.append(matches)
    return {"rounds": rounds, "standings": standings_of(seeds, [m for r in rounds for m in r])}


def is_final_match(fmt: str, built: dict, match: dict) -> bool:
    if fmt == "double_elim":
        return match.get("bracket") == "grand"
    if fmt == "single_elim":
        return match["round"] == len(built["rounds"]) - 1
    return False


def build_bracket(fmt: str, seeds: list, results: dict, fixed: dict | None = None) -> dict:
    if fmt == "swiss":
        return build_swiss(seeds, results, fixed)
    if fmt == "round_robin":
        built = build_round_robin(seeds, results)
        return {**built, "standings": standings_of(seeds, [m for r in built["rounds"] for m in r])}
    if fmt == "double_elim":
        return build_double_elim(seeds, results)
    return build_single_elim(seeds, results)


def all_matches(built: dict) -> list[dict]:
    return [m for r in built["rounds"] for m in r] + [m for r in built.get("losersRounds", []) for m in r] + list(built.get("grandFinal", []))


def find_match(built: dict, match_id: str) -> dict | None:
    return next((m for m in all_matches(built) if m["id"] == match_id), None)


def progress(built: dict) -> tuple[int, int]:
    playable = [m for m in all_matches(built) if m["status"] not in ("bye", "empty")]
    return sum(1 for m in playable if m["status"] == "complete"), len(playable)


def ready_matches(built: dict) -> list[dict]:
    return [m for m in all_matches(built) if m["status"] == "ready"]


def match_stage(fmt: str, match: dict) -> str:
    if fmt == "double_elim":
        return {"losers": "losers", "grand": "grand"}.get(match.get("bracket"), "winners")
    return "main"


def match_links(fmt: str, seed_count: int) -> dict:
    """
    {code: {"next": (code, slot) | None, "loser_next": (code, slot) | None}} - where each match's
    winner and loser go, for elimination formats. Found by building the bracket once with
    placeholder results ("W:<code>" wins every match, "L:<code>" loses) and reading where each
    placeholder lands, so the links always match the builders above. The grand final reset (gf2)
    only exists when the losers-side player wins gf1, so nothing links into it.
    """

    if fmt not in FINAL_FORMATS or seed_count < 2:
        return {}

    global _decide
    real = _decide

    def symbolic(match: dict, _results: dict) -> dict:
        a, b = match["a"], match["b"]
        if a == BYE or b == BYE:
            return real(match, {})
        return {**match, "status": "complete", "winner": f"W:{match['id']}", "loser": f"L:{match['id']}", "scoreA": 1, "scoreB": 0}

    _decide = symbolic
    try:
        built = build_bracket(fmt, [f"s{i}" for i in range(seed_count)], {})
    finally:
        _decide = real

    links: dict = {}
    for m in all_matches(built):
        links.setdefault(m["id"], {"next": None, "loser_next": None})
        if m["id"] == "gf2":
            continue
        for side in ("a", "b"):
            value = m[side]
            if isinstance(value, str) and value[:2] in ("W:", "L:"):
                source = links.setdefault(value[2:], {"next": None, "loser_next": None})
                source["next" if value[0] == "W" else "loser_next"] = (m["id"], side)
    return links


def placements(fmt: str, built: dict) -> list | None:
    """
    Final placings once the event is decided: [1st, 2nd, 3rd] (3rd may be None).
    - Single elim: 3rd is the semifinal loser who lost to the champion.
    - Double elim: 2nd is the grand final loser, 3rd the losers final loser.
    - Round robin / swiss: the top three in the standings once every match is played.
    """

    if fmt in ("round_robin", "swiss"):
        done, total = progress(built)
        if not total or done < total:
            return None
        return [row["id"] for row in built["standings"][:3]]
    if not built.get("champion"):
        return None
    if fmt == "double_elim":
        gf = built["grandFinal"][-1]
        lf = built["losersRounds"][-1][0] if built["losersRounds"] else None
        second = gf["loser"] if gf["status"] == "complete" else None
        third = lf["loser"] if lf and is_entrant(lf.get("loser")) else None
        return [built["champion"], second, third]
    rounds = built["rounds"]
    final = rounds[-1][0]
    semis = rounds[-2] if len(rounds) >= 2 else []
    champions_semi = next((m for m in semis if m["winner"] == built["champion"]), None)
    third = champions_semi["loser"] if champions_semi and is_entrant(champions_semi.get("loser")) else None
    return [built["champion"], final["loser"], third]
