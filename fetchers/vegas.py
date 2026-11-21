"""NFL betting lines -> implied team totals (free ESPN feeds, no key).

Implied total = what Vegas expects a team to score:
    home = (over_under - home_spread) / 2,  away = over_under - home.
- This week: lines from the ESPN scoreboard (refreshed every build, 3h cache).
- Past weeks: closing lines from each game's ESPN summary ("pickcenter") plus
  the final score. Finished weeks never change, so they're cached for good.
Per team we report this week's implied total/opponent and the season-to-date
"luck": actual points minus implied, per game. Graceful: any failed request
just leaves that game out.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
CACHE = STATE_DIR / "vegas_cache.json"
CURRENT_TTL = 3 * 3600
BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
TEAM_FIX = {"WSH": "WAS"}   # ESPN -> Sleeper abbreviations


def _get(url: str) -> dict:
    try:
        out = subprocess.run(["curl", "-s", "--max-time", "15", url],
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vegas fetch failed for %s (%s)", url, exc)
        return {}


def _abbr(c: dict) -> str:
    a = c["team"]["abbreviation"]
    return TEAM_FIX.get(a, a)


def _implied(odds: dict) -> tuple[float, float] | None:
    ou, spread = odds.get("overUnder"), odds.get("spread")
    if ou is None or spread is None:
        return None
    home = (ou - spread) / 2
    return home, ou - home


PLAYERS_DB = STATE_DIR / "players_nfl.json"
_POS_LOOKUP: dict = {}


def _pos_lookup() -> dict:
    """(normalized name, team) -> position, from the cached Sleeper player DB."""
    if not _POS_LOOKUP and PLAYERS_DB.exists():
        from fetchers.rankings import _norm
        for p in json.load(open(PLAYERS_DB)).values():
            if p.get("position") in ("QB", "RB", "WR", "TE") and p.get("full_name"):
                _POS_LOOKUP[(_norm(p["full_name"]), p.get("team"))] = p["position"]
    return _POS_LOOKUP


def _num(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _pos_points(box_team: dict, team: str) -> dict:
    """Half-PPR fantasy points a team's offense scored, by position (from the box score)."""
    from fetchers.rankings import _norm
    look, pts = _pos_lookup(), {"QB": 0.0, "RB": 0.0, "WR": 0.0, "TE": 0.0}
    for cat in box_team.get("statistics", []):
        n = cat["name"]
        for a in cat["athletes"]:
            pos = look.get((_norm(a["athlete"]["displayName"]), team))
            if not pos:
                continue
            st = dict(zip(cat["labels"], a["stats"]))
            if n == "passing":
                pts[pos] += _num(st.get("YDS")) * 0.04 + _num(st.get("TD")) * 4 - _num(st.get("INT")) * 2
            elif n == "rushing":
                pts[pos] += _num(st.get("YDS")) * 0.1 + _num(st.get("TD")) * 6
            elif n == "receiving":
                pts[pos] += _num(st.get("REC")) * 0.5 + _num(st.get("YDS")) * 0.1 + _num(st.get("TD")) * 6
            elif n == "fumbles":
                pts[pos] -= _num(st.get("LOST")) * 2
    return {k: round(v, 1) for k, v in pts.items()}


def _week_games(season: str, week: int, final: bool) -> list[dict]:
    sb = _get(f"{BASE}/scoreboard?dates={season}&seasontype=2&week={week}")
    games = []
    for e in sb.get("events", []):
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        if final:
            if not comp["status"]["type"].get("completed"):
                continue
            summ = _get(f"{BASE}/summary?event={e['id']}")
            pc = summ.get("pickcenter") or []
            odds = pc[0] if pc else {}
            box = {t["team"]["abbreviation"]: t for t in summ.get("boxscore", {}).get("players", [])}
        else:
            odds, box = (comp.get("odds") or [{}])[0], {}
        imp = _implied(odds)
        if not imp and not final:
            continue
        h, a = _abbr(teams["home"]), _abbr(teams["away"])
        games.append({
            "home": h, "away": a,
            "home_implied": imp[0] if imp else None, "away_implied": imp[1] if imp else None,
            "home_pos": _pos_points(box.get(teams["home"]["team"]["abbreviation"], {}), h),
            "away_pos": _pos_points(box.get(teams["away"]["team"]["abbreviation"], {}), a),
            "home_score": int(teams["home"].get("score") or 0),
            "away_score": int(teams["away"].get("score") or 0),
        })
    return games


def fetch_vegas(season: str, week: int) -> dict:
    """Return {"week", "teams": {abbr: {...}}}; {} if nothing could be fetched."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("season") != season:
        cache = {"season": season, "past": {}}
    past = cache["past"]
    for w in range(1, week):
        if str(w) not in past or any("home_pos" not in g for g in past[str(w)]):
            past[str(w)] = _week_games(season, w, final=True)
            log.info("Vegas: cached week %d closing lines (%d games)", w, len(past[str(w)]))
    cur = cache.get("current") or {}
    if cur.get("week") != week or time.time() - cur.get("fetched_at", 0) > CURRENT_TTL:
        cur = {"week": week, "fetched_at": time.time(),
               "games": _week_games(season, week, final=False)}
        cache["current"] = cur
    CACHE.write_text(json.dumps(cache))

    teams: dict[str, dict] = {}

    def team(a):
        return teams.setdefault(a, {"implied": None, "opp": None, "home": None,
                                    "games": 0, "actual": 0, "expected": 0.0, "allowed": 0,
                                    "lined": 0, "pos_allowed": {}, "pos_games": 0})

    for games in past.values():
        for g in games:
            for side in ("home", "away"):
                t = team(g[side])
                other = "away" if side == "home" else "home"
                t["games"] += 1
                t["allowed"] += g[f"{other}_score"]
                if g[f"{side}_implied"] is not None:
                    t["lined"] += 1
                    t["actual"] += g[f"{side}_score"]
                    t["expected"] += g[f"{side}_implied"]
                if g.get(f"{other}_pos"):
                    t["pos_games"] += 1
                    for pos, v in g[f"{other}_pos"].items():
                        t["pos_allowed"][pos] = t["pos_allowed"].get(pos, 0) + v
    for g in cur["games"]:
        for side, other in (("home", "away"), ("away", "home")):
            t = team(g[side])
            t.update(implied=round(g[f"{side}_implied"], 1), opp=g[other], home=side == "home")
    for t in teams.values():
        n = t["games"] or 1
        nl = t["lined"] or 1
        t["avg_implied"] = round(t["expected"] / nl, 1)
        t["avg_allowed"] = round(t["allowed"] / n, 1)
        t["luck"] = round((t["actual"] - t["expected"]) / nl, 1)   # + = beating Vegas
        t["pos_allowed"] = {k: round(v / (t["pos_games"] or 1), 1) for k, v in t["pos_allowed"].items()}
    log.info("Vegas: %d teams with week %d lines, %d past weeks of closing lines",
             sum(1 for t in teams.values() if t["implied"] is not None), week, len(past))
    return {"week": week, "teams": teams} if teams else {}


def fetch_future(season: str, weeks: list[int]) -> dict[int, dict]:
    """NFL schedule (+ Vegas implied totals once the book posts them) for later weeks.
    Returns {week: {team: {"opp", "home", "implied" (None until lines post)}}}."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {"season": season, "past": {}}
    fut = cache.get("future") or {}
    out: dict[int, dict] = {}
    for w in weeks:
        c = fut.get(str(w)) or {}
        if not c or time.time() - c.get("fetched_at", 0) > CURRENT_TTL:
            sb = _get(f"{BASE}/scoreboard?dates={season}&seasontype=2&week={w}")
            teams: dict[str, dict] = {}
            for e in sb.get("events", []):
                comp = e["competitions"][0]
                sides = {x["homeAway"]: x for x in comp["competitors"]}
                imp = _implied((comp.get("odds") or [{}])[0])
                for side, other in (("home", "away"), ("away", "home")):
                    teams[_abbr(sides[side])] = {
                        "opp": _abbr(sides[other]), "home": side == "home",
                        "implied": round(imp[0 if side == "home" else 1], 1) if imp else None}
            c = {"fetched_at": time.time(), "teams": teams}
            if teams:
                fut[str(w)] = c
        out[w] = c.get("teams", {})
    cache["future"] = fut
    CACHE.write_text(json.dumps(cache))
    return out
