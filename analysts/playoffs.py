"""Playoff planner: bracket path, seeding race, matchup-graded lineups, deadline buys.

Matchup grade for a player's NFL team in a given week:
  - Vegas implied team points once the book posts them (>=24 favorable, <=19.5 tough);
  - until then, the opponent's points allowed per game this season (softest 8 of
    32 defenses favorable, toughest 8 tough). Team-level only: positional
    defense (vs RB / vs WR) isn't available from a free source.
Lineups rank by market value nudged by matchup, because weekly projections
aren't published this far out.
"""
from __future__ import annotations

import logging

from fetchers.sleeper import _get

log = logging.getLogger(__name__)

NEVER_TRADE = {"Patrick Mahomes"}
OUT = {"Out", "IR", "Sus", "PUP", "NA"}
BUY_MIN_VALUE = 2000
SLOTS = [("QB", {"QB"}, 1), ("RB", {"RB"}, 2), ("WR", {"WR"}, 2), ("TE", {"TE"}, 1),
         ("SUPER_FLEX", {"QB", "RB", "WR", "TE"}, 1), ("FLEX", {"RB", "WR", "TE"}, 3)]
SCORE = {"Favorable": 1, "Neutral": 0, "Tough": -1}
NUDGE = 0.08


def grade(team: str, week_sched: dict, vegas_teams: dict, allowed_rank: dict) -> dict:
    g = (week_sched or {}).get(team)
    if not g:
        return {"grade": "Bye", "opp": None, "home": None, "implied": None, "basis": "bye week"}
    if g["implied"] is not None:
        gr = "Favorable" if g["implied"] >= 24 else "Tough" if g["implied"] <= 19.5 else "Neutral"
        basis = f"Vegas {g['implied']:g} pts"
    else:
        rk = allowed_rank.get(g["opp"])
        gr = "Neutral" if rk is None else "Favorable" if rk <= 8 else "Tough" if rk > 24 else "Neutral"
        pa = (vegas_teams.get(g["opp"]) or {}).get("avg_allowed")
        basis = f"{g['opp']} allow {pa:g}/g" if pa is not None else "no data"
    return {"grade": gr, "opp": g["opp"], "home": g["home"], "implied": g["implied"], "basis": basis}


def _allowed_rank(vegas_teams: dict) -> dict:
    """1 = gives up the most points (softest defense)."""
    ranked = sorted((a for a, t in vegas_teams.items() if t.get("avg_allowed") is not None),
                    key=lambda a: -vegas_teams[a]["avg_allowed"])
    return {a: i + 1 for i, a in enumerate(ranked)}


def _possible(matches: dict, ref) -> set:
    """Roster ids that could occupy a bracket slot (an id, or {'w'|'l': match})."""
    if isinstance(ref, int):
        return {ref}
    if not isinstance(ref, dict) or not ref:
        return set()
    (_, m), = ref.items()
    mm = matches[m]
    return (_possible(matches, mm.get("t1_from") or mm.get("t1"))
            | _possible(matches, mm.get("t2_from") or mm.get("t2")))


def build_bracket(data, league_id: str) -> dict:
    start = (data.league.get("settings") or {}).get("playoff_week_start") or 15
    raw = _get(f"/league/{league_id}/winners_bracket") or []
    name = {r.roster_id: r.team_name for r in data.all_rosters}
    me = data.my_roster.roster_id
    matches = {m["m"]: m for m in raw}
    path = []
    for r in sorted({m["r"] for m in raw}):
        week = start + r - 1
        mine = None
        for m in raw:
            if m["r"] != r or m.get("p") not in (None, 1):     # skip consolation games
                continue
            t1 = _possible(matches, m.get("t1_from") or m.get("t1"))
            t2 = _possible(matches, m.get("t2_from") or m.get("t2"))
            if me in t1 | t2:
                mine = (t2 if me in t1 else t1) - {me}
        if mine is None:
            path.append({"round": r, "week": week, "bye": True, "opponents": []})
        else:
            path.append({"round": r, "week": week, "bye": False,
                         "opponents": [{"roster_id": x, "team": name.get(x, f"Team {x}")} for x in sorted(mine)]})
    return {"start": start, "path": path, "ok": bool(raw)}


def seeding(data, league_id: str, week: int) -> list[dict]:
    """Standings + each team's remaining regular-season opponents."""
    s = data.league.get("settings") or {}
    last = (s.get("playoff_week_start") or 15) - 1
    name = {r.roster_id: r.team_name for r in data.all_rosters}
    remaining: dict[int, list[str]] = {r.roster_id: [] for r in data.all_rosters}
    for w in range(week, last + 1):
        ms = _get(f"/league/{league_id}/matchups/{w}") or []
        by = {}
        for x in ms:
            by.setdefault(x.get("matchup_id"), []).append(x["roster_id"])
        for pair in by.values():
            if len(pair) == 2:
                remaining[pair[0]].append(name.get(pair[1], "?"))
                remaining[pair[1]].append(name.get(pair[0], "?"))
    rows = sorted(data.all_rosters, key=lambda r: (-r.wins, -r.fpts))
    teams = s.get("playoff_teams") or 6
    return [{"seed": i + 1, "roster_id": r.roster_id, "team": r.team_name, "wins": r.wins,
             "losses": r.losses, "pf": r.fpts, "remaining": remaining[r.roster_id],
             "in": i < teams, "bye": i < 2, "me": r.roster_id == data.my_roster.roster_id}
            for i, r in enumerate(rows)]


def best_lineup(players, week_grades: dict) -> list[dict]:
    """Fill QB/2RB/2WR/TE/SF/3FLEX from available players (value + matchup nudge)."""
    def score(p):
        g = week_grades.get(p.player_id, {}).get("grade")
        return p.market_value * (1 + NUDGE * SCORE.get(g, 0))
    pool = [p for p in players if p.slot_type in ("starter", "bench")
            and (p.injury_status or "") not in OUT
            and week_grades.get(p.player_id, {}).get("grade") != "Bye"]
    pool.sort(key=score, reverse=True)
    used, lineup = set(), []
    for label, elig, n in SLOTS:
        for _ in range(n):
            pick = next((p for p in pool if p.player_id not in used and p.position in elig), None)
            if pick:
                used.add(pick.player_id)
                lineup.append({"slot": "SFLEX" if label == "SUPER_FLEX" else label, "player": pick,
                               "grade": week_grades.get(pick.player_id, {})})
    return lineup


def deadline_buys(opponent_profiles, opponent_enriched, contention, needs, fut_grades, weeks) -> list[dict]:
    need_pos = {pos for pos, n in (needs or {}).items() if n["need"] >= 0.3}
    order = {"Long shot": 0, "Bubble": 1, "Contender": 2}
    out = []
    for o in opponent_profiles:
        status = contention[o.owner_id].status
        for p in opponent_enriched.get(o.owner_id, []):
            if p.position not in need_pos or p.market_value < BUY_MIN_VALUE or p.name in NEVER_TRADE:
                continue
            gs = [fut_grades[w].get(p.team) for w in weeks]
            sc = sum(SCORE.get(g["grade"], -1) if g else 0 for g in gs)
            if sc < 1:
                continue
            out.append({"name": p.name, "pos": p.position, "team": p.team, "value": p.market_value,
                        "from": o.team_name, "status": status, "score": sc, "grades": gs})
    out.sort(key=lambda b: (-b["score"], order.get(b["status"], 1), -b["value"]))
    return out[:8]


def build_playoffs(data, league_id, my_enriched, opponent_profiles, opponent_enriched, contention,
                   needs, vegas: dict, future: dict) -> dict:
    s = data.league.get("settings") or {}
    week = s.get("leg") or 1
    start = s.get("playoff_week_start") or 15
    deadline = s.get("trade_deadline")
    vt = (vegas or {}).get("teams", {})
    arank = _allowed_rank(vt)
    weeks = sorted(future)
    team_grades = {w: {t: grade(t, future[w], vt, arank) for t in {x.team for x in my_enriched + [
        q for pl in opponent_enriched.values() for q in pl] if x.team != "FA"}} for w in weeks}
    bracket = build_bracket(data, league_id)
    play_weeks = [b["week"] for b in bracket["path"] if not b["bye"]] or weeks
    lineups = {}
    for w in weeks:
        wg = {p.player_id: team_grades[w].get(p.team, {"grade": "Neutral"}) for p in my_enriched}
        lineups[w] = {"lineup": best_lineup(my_enriched, wg), "plays": w in play_weeks}
    table = []
    for p in sorted(my_enriched, key=lambda z: -z.market_value):
        if p.slot_type in ("starter", "bench") and p.market_value >= 800:
            table.append({"p": p, "weeks": [team_grades[w].get(p.team, {"grade": "Neutral"}) for w in weeks]})
    by_roster = {o.roster_id: o for o in opponent_profiles}
    scouts = {}
    for b in bracket["path"]:
        for o in b["opponents"]:
            prof = by_roster.get(o["roster_id"])
            if prof and o["roster_id"] not in scouts:
                top = sorted(opponent_enriched.get(prof.owner_id, []), key=lambda z: -z.market_value)[:4]
                scouts[o["roster_id"]] = {"team": prof.team_name, "record": f"{prof.wins}-{prof.losses}",
                                          "pf": prof.points_for, "value": prof.total_value,
                                          "status": contention[prof.owner_id].status,
                                          "strong": prof.surplus_positions, "weak": prof.weak_positions,
                                          "top": [f"{t.name} ({t.position})" for t in top]}
    buys = []
    if deadline is None or week <= deadline:
        buys = deadline_buys(opponent_profiles, opponent_enriched, contention, needs, team_grades,
                             [w for w in weeks if w in play_weeks] or weeks)
    log.info("Playoffs: bracket %s, lineups for weeks %s, %d deadline buys",
             "ok" if bracket["ok"] else "unavailable", weeks, len(buys))
    return {"week": week, "start": start, "deadline": deadline, "weeks": weeks, "bracket": bracket,
            "seeding": seeding(data, league_id, week), "lineups": lineups, "table": table,
            "scouts": scouts, "buys": buys, "lines_posted": any(
                g.get("implied") is not None for w in weeks for g in future[w].values())}
