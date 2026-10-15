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


def _week_games(season: str, week: int, final: bool) -> list[dict]:
    sb = _get(f"{BASE}/scoreboard?dates={season}&seasontype=2&week={week}")
    games = []
    for e in sb.get("events", []):
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        if final:
            if not comp["status"]["type"].get("completed"):
                continue
            pc = _get(f"{BASE}/summary?event={e['id']}").get("pickcenter") or []
            odds = pc[0] if pc else {}
        else:
            odds = (comp.get("odds") or [{}])[0]
        imp = _implied(odds)
        if not imp:
            continue
        games.append({
            "home": _abbr(teams["home"]), "away": _abbr(teams["away"]),
            "home_implied": imp[0], "away_implied": imp[1],
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
        if str(w) not in past:
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
                                    "games": 0, "actual": 0, "expected": 0.0})

    for games in past.values():
        for g in games:
            for side in ("home", "away"):
                t = team(g[side])
                t["games"] += 1
                t["actual"] += g[f"{side}_score"]
                t["expected"] += g[f"{side}_implied"]
    for g in cur["games"]:
        for side, other in (("home", "away"), ("away", "home")):
            t = team(g[side])
            t.update(implied=round(g[f"{side}_implied"], 1), opp=g[other], home=side == "home")
    for t in teams.values():
        n = t["games"] or 1
        t["avg_implied"] = round(t["expected"] / n, 1)
        t["luck"] = round((t["actual"] - t["expected"]) / n, 1)   # + = beating Vegas
    log.info("Vegas: %d teams with week %d lines, %d past weeks of closing lines",
             sum(1 for t in teams.values() if t["implied"] is not None), week, len(past))
    return {"week": week, "teams": teams} if teams else {}
