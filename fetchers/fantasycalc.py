"""FantasyCalc dynasty values (superflex, 12-team, half-PPR).

Dave trusts FantasyCalc over KTC, so FC is the primary value everywhere
(enrichment, trades, opponents, boards); KTC is the fallback for unmatched
players and supplies rank/trend data. We fetch the public values API via curl
(LibreSSL on this box can't TLS to some hosts — same reason KTC uses curl),
match each FC player to a Sleeper player_id by name, and cache to
state/fc_values.json. Pick entities ("2027 1st", "2026 Pick 1.01", ...) are
kept under a separate "picks" key for pricing the pick portfolio.

Graceful: if the fetch or cache is missing, callers fall back to KTC.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import subprocess
import time
from pathlib import Path

from fetchers.rankings import DynastyRanking, _norm

log = logging.getLogger(__name__)

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
FC_CACHE = STATE_DIR / "fc_values.json"
FC_UNMATCHED_LOG = STATE_DIR / "fc_unmatched.log"
CACHE_TTL_SECONDS = 6 * 3600
FC_URL = ("https://api.fantasycalc.com/values/current"
          "?isDynasty=true&numQbs=2&numTeams=12&ppr=0.5")
PICK_RE = re.compile(r"^20\d\d (Pick \d\.\d\d|[1-4](?:st|nd|rd|th))$")


def _load_cache() -> dict:
    if FC_CACHE.exists():
        return json.loads(FC_CACHE.read_text())
    return {}


def load_fc_values() -> dict[str, int]:
    """player_id -> FC value, or {} if no cache."""
    return _load_cache().get("values", {})


def load_fc_pick_values() -> dict[str, int]:
    """Pick entity name (e.g. "2027 1st", "2026 Pick 1.09") -> FC value."""
    return _load_cache().get("picks", {})


def fetch_fc_values(rankings: list[DynastyRanking], skip: bool = False) -> dict[str, int]:
    """Fetch FC values and match to Sleeper player_ids (via ranking names)."""
    cached = _load_cache()
    if cached:
        age = time.time() - cached.get("fetched_at", 0)
        # "picks" missing = pre-upgrade cache format; force a refetch
        if "picks" in cached and (skip or age < CACHE_TTL_SECONDS):
            log.info("FantasyCalc: using cache (%.1fh old)", age / 3600)
            return cached.get("values", {})

    try:
        raw = subprocess.run(["curl", "-s", "--max-time", "20", FC_URL],
                             capture_output=True, text=True, check=True).stdout
        fc = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("FantasyCalc fetch failed (%s) — falling back to KTC", exc)
        return cached.get("values", {})

    # Split pick entities from players; normalize player names like the KTC matcher.
    pick_values: dict[str, int] = {}
    fc_by_name: dict[str, int] = {}
    for p in fc:
        nm = p["player"]["name"]
        if PICK_RE.match(nm):
            pick_values[nm] = int(p["value"])
        else:
            fc_by_name[_norm(nm)] = int(p["value"])
    name_keys = list(fc_by_name.keys())

    values: dict[str, int] = {}
    unmatched = []
    for r in rankings:
        if not r.player_id:
            continue
        key = _norm(r.name)
        if key in fc_by_name:
            values[r.player_id] = fc_by_name[key]
        else:
            m = difflib.get_close_matches(key, name_keys, n=1, cutoff=0.85)
            if m:
                values[r.player_id] = fc_by_name[m[0]]
            else:
                unmatched.append(r.name)

    STATE_DIR.mkdir(exist_ok=True)
    FC_CACHE.write_text(json.dumps(
        {"fetched_at": time.time(), "values": values, "picks": pick_values}))
    if unmatched:
        FC_UNMATCHED_LOG.write_text("\n".join(unmatched))
    log.info("FantasyCalc: %d/%d players matched (%d unmatched -> %s), %d pick values",
             len(values), sum(1 for r in rankings if r.player_id), len(unmatched),
             FC_UNMATCHED_LOG.name, len(pick_values))
    return values
