"""
Pure bracket logic, ported from the frontend's utils/bracket.js so the server builds exactly the
bracket players see. Brackets are derived from (seeds + results) every time, so changing an early
result automatically clears any later match it no longer feeds into.

Match ids are stable strings the frontend uses too: "r<round>m<slot>" (winners / single elim),
"l<round>m<slot>" (losers bracket), "gf1" / "gf2" (grand final and reset), "rr<round>m<slot>"
(pool play). A stored result only counts while the same two entrants are in that match.
"""

from dataclasses import dataclass, field

FORMATS = ("single_elim", "double_elim", "round_robin")

# Stands in for "nobody" where a bracket isn't full: a first-round bye, or the loser of a bye
# match dropping into the losers bracket.
BYE = "__bye__"

def is_entrant(entrant_id: str | None) -> bool:
    return bool(entrant_id) and entrant_id != BYE

@dataclass
class Match:
    id: str
    round: int
    slot: int
    a: str | None
    b: str | None
    seed_a: int | None = None
    seed_b: int | None = None
    bracket: str | None = None
    # 'empty' (bye vs bye) | 'bye' | 'pending' (waiting on an earlier match) | 'ready' | 'complete'
    status: str = "pending"
    winner: str | None = None
    loser: str | None = None
    score_a: int | None = None
    score_b: int | None = None
    forfeit: bool = False

@dataclass
class Built:
    rounds: list[list[Match]] = field(default_factory = list)
    losers_rounds: list[list[Match]] = field(default_factory = list)
    grand_final: list[Match] = field(default_factory = list)
    champion: str | None = None
    standings: list[dict] = field(default_factory = list)

def same_matchup(record: dict | None, match: Match) -> bool:
    """
    A stored result, report or no-show only applies to the two entrants it was made for.
    """

    return bool(record) and record.get("a") == match.a and record.get("b") == match.b

def seed_placement(size: int) -> list[int]:
    """
    Standard placement so seeds 1 and 2 can only meet in the final: 8 -> [1, 8, 4, 5, 2, 7, 3, 6].
    """

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

def _decide(match: Match, results: dict) -> Match:
    a, b = match.a, match.b
    if a == BYE and b == BYE:
        match.status, match.winner, match.loser = "empty", BYE, BYE
        return match
    if a == BYE or b == BYE:
        other = b if a == BYE else a
        if not other:
            match.status = "pending"
            return match
        match.status, match.winner, match.loser = "bye", other, BYE
        return match
    if not a or not b:
        match.status = "pending"
        return match

    result = results.get(match.id)
    if not same_matchup(result, match):
        match.status = "ready"
        return match

    a_won = result["score_a"] > result["score_b"]
    match.status = "complete"
    match.score_a, match.score_b = result["score_a"], result["score_b"]
    match.forfeit = bool(result.get("forfeit"))
    match.winner, match.loser = (a, b) if a_won else (b, a)
    return match

def _seed_lookup(seeds: list[str]):
    return lambda entrant_id: seeds.index(entrant_id) + 1 if is_entrant(entrant_id) and entrant_id in seeds else None

def build_single_elim(seeds: list[str], results: dict | None = None) -> Built:
    results = results or {}
    if len(seeds) < 2:
        return Built()

    size = _next_power_of_two(len(seeds))
    placement = seed_placement(size)
    seed_of = _seed_lookup(seeds)

    def make(match_id, round_, slot, a, b):
        return _decide(Match(match_id, round_, slot, a, b, seed_of(a), seed_of(b)), results)

    def seed_at(position):
        return seeds[position - 1] if position - 1 < len(seeds) else BYE

    rounds = [[make(f"r0m{slot}", 0, slot, seed_at(placement[slot * 2]), seed_at(placement[slot * 2 + 1])) for slot in range(size // 2)]]

    round_ = 1
    while size // 2 ** (round_ + 1) >= 1:
        prev = rounds[round_ - 1]
        rounds.append([
            make(f"r{round_}m{slot}", round_, slot, prev[slot * 2].winner, prev[slot * 2 + 1].winner)
            for slot in range(len(prev) // 2)
        ])
        round_ += 1

    final = rounds[-1][0]
    return Built(rounds = rounds, champion = final.winner if final.status == "complete" else None)

def build_double_elim(seeds: list[str], results: dict | None = None) -> Built:
    """
    Winners bracket is a normal single-elim bracket. Losers drop into the losers bracket, which
    alternates "minor" rounds (survivors play each other) and "major" rounds (survivors play the
    players just dropped). If the losers-side player wins the grand final, a reset match decides it.
    """

    results = results or {}
    wb = build_single_elim(seeds, results)
    if not wb.rounds:
        return Built()

    seed_of = _seed_lookup(seeds)

    def make(match_id, round_, slot, a, b, bracket):
        return _decide(Match(match_id, round_, slot, a, b, seed_of(a), seed_of(b), bracket), results)

    k = len(wb.rounds)
    lr: list[list[Match]] = []

    if k >= 2:
        first = wb.rounds[0]
        lr.append([make(f"l0m{i}", 0, i, first[i * 2].loser, first[i * 2 + 1].loser, "losers") for i in range(len(first) // 2)])

        for j in range(1, k):
            prev = lr[-1]
            dropped = [m.loser for m in wb.rounds[j]]
            # Flip the drop order every other round so players don't rematch straight away.
            ordered = list(reversed(dropped)) if j % 2 else dropped
            major = len(lr)
            lr.append([make(f"l{major}m{i}", major, i, ordered[i], m.winner, "losers") for i, m in enumerate(prev)])

            if j < k - 1:
                p = lr[-1]
                minor = len(lr)
                lr.append([make(f"l{minor}m{i}", minor, i, p[i * 2].winner, p[i * 2 + 1].winner, "losers") for i in range(len(p) // 2)])

    wb_final = wb.rounds[k - 1][0]
    wb_champ = wb_final.winner if wb_final.status == "complete" else None
    lb_champ = wb_final.loser if k == 1 else lr[-1][0].winner

    gf1 = make("gf1", 0, 0, wb_champ, lb_champ, "grand")
    grand_final = [gf1]
    champion = None
    if gf1.status == "bye":
        champion = gf1.winner
    if gf1.status == "complete":
        if gf1.winner == wb_champ:
            champion = wb_champ
        else:
            gf2 = make("gf2", 1, 0, wb_champ, lb_champ, "grand")
            grand_final.append(gf2)
            if gf2.status == "complete":
                champion = gf2.winner

    return Built(rounds = wb.rounds, losers_rounds = lr, grand_final = grand_final, champion = champion)

def build_round_robin(seeds: list[str], results: dict | None = None) -> Built:
    """
    Circle method: everyone plays everyone once. Odd counts get a rotating bye.
    """

    results = results or {}
    if len(seeds) < 2:
        return Built()

    players: list[str | None] = list(seeds) + ([None] if len(seeds) % 2 else [])
    n = len(players)
    rotating = players[1:]
    rounds = []

    for round_ in range(n - 1):
        lineup = [players[0], *rotating]
        matches = []
        for i in range(n // 2):
            a, b = lineup[i], lineup[n - 1 - i]
            if not a or not b:
                continue
            matches.append(_decide(Match(f"rr{round_}m{i}", round_, i, a, b), results))
        rounds.append(matches)
        rotating.insert(0, rotating.pop())

    table = {entrant_id: {"id": entrant_id, "played": 0, "wins": 0, "losses": 0, "diff": 0} for entrant_id in seeds}
    for match in (m for r in rounds for m in r):
        if match.status != "complete":
            continue
        margin = abs(match.score_a - match.score_b)
        table[match.winner]["wins"] += 1
        table[match.winner]["diff"] += margin
        table[match.loser]["losses"] += 1
        table[match.loser]["diff"] -= margin
        table[match.a]["played"] += 1
        table[match.b]["played"] += 1

    # Same order as the frontend's stable sort: wins, then score difference, then seed.
    standings = sorted(table.values(), key = lambda row: (-row["wins"], -row["diff"]))
    return Built(rounds = rounds, standings = standings)

def build_bracket(format_: str, seeds: list[str], results: dict | None = None) -> Built:
    if format_ == "round_robin":
        return build_round_robin(seeds, results)
    if format_ == "double_elim":
        return build_double_elim(seeds, results)
    return build_single_elim(seeds, results)

def all_matches(built: Built) -> list[Match]:
    return [m for r in built.rounds for m in r] + [m for r in built.losers_rounds for m in r] + list(built.grand_final)

def find_match(built: Built, match_id: str) -> Match | None:
    return next((m for m in all_matches(built) if m.id == match_id), None)

def bracket_progress(built: Built) -> tuple[int, int]:
    """
    (matches done, matches to play) - byes and empty matches aren't played.
    """

    playable = [m for m in all_matches(built) if m.status not in ("bye", "empty")]
    return sum(m.status == "complete" for m in playable), len(playable)

def placements(format_: str, built: Built) -> list[str | None] | None:
    """
    Final placings once the event is decided: [1st, 2nd, 3rd] (3rd may be None).
    - Single elim: 3rd goes to the semifinal loser who lost to the eventual champion.
    - Double elim: 2nd is the grand-final loser, 3rd is the losers-final loser.
    - Pool play: the top three in the standings once every match is played.
    """

    if format_ == "round_robin":
        done, total = bracket_progress(built)
        if not total or done < total:
            return None
        return [row["id"] for row in built.standings[:3]]

    if not built.champion:
        return None

    if format_ == "double_elim":
        gf = built.grand_final[-1]
        lf = built.losers_rounds[-1][0] if built.losers_rounds else None
        second = gf.loser if gf.status == "complete" else None
        third = lf.loser if lf and is_entrant(lf.loser) else None
        return [built.champion, second, third]

    final = built.rounds[-1][0]
    semis = built.rounds[-2] if len(built.rounds) >= 2 else []
    champions_semi = next((m for m in semis if m.winner == built.champion), None)
    third = champions_semi.loser if champions_semi and is_entrant(champions_semi.loser) else None
    return [built.champion, final.loser, third]
