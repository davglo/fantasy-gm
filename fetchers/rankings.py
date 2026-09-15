"""KeepTradeCut superflex dynasty rankings.

KTC serves player data in a `<script id="ktc-players">…JSON…</script>` element
on the dynasty-rankings page (older pages used an inline `var playersArray = [...]`
literal — kept as a fallback). We extract the JSON, json.load it, and fuzzy-match
each KTC player to a Sleeper player_id.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from fetchers.sleeper import Player

log = logging.getLogger(__name__)

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
RANKINGS_CACHE = STATE_DIR / "rankings_cache.json"
UNMATCHED_LOG = STATE_DIR / "unmatched.log"
CACHE_TTL_SECONDS = 6 * 3600
KTC_URL = "https://keeptradecut.com/dynasty-rankings"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
MATCH_THRESHOLD = 0.85


@dataclass
class DynastyRanking:
    player_id: str          # Sleeper ID after matching; "" if unmatched
    name: str
    position: str
    ktc_value: int          # superflex value 0-9999; -1 if unmatched
    overall_rank: int
    position_rank: int
    trend_7day: int         # positive = rising
    is_rookie: bool
    age: float


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).replace(" jr", "").replace(" sr", "").strip()


def _fetch_html() -> str:
    """Fetch KTC page. Prefer curl (modern TLS) since the system Python ships an
    ancient LibreSSL that KTC's server rejects; fall back to requests."""
    try:
        out = subprocess.run(
            ["curl", "-s", "-A", UA, "--max-time", "25", KTC_URL],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0 and "playersArray" in out.stdout:
            return out.stdout
        log.warning("curl fetch incomplete (rc=%d); trying requests", out.returncode)
    except Exception as exc:  # noqa: BLE001
        log.warning("curl fetch failed (%s); trying requests", exc)
    resp = requests.get(KTC_URL, headers={"User-Agent": UA}, timeout=25)
    resp.raise_for_status()
    return resp.text


def _scrape_ktc(html: str | None = None) -> list[dict]:
    log.info("Scraping KTC dynasty rankings...")
    html = html or _fetch_html()
    # Current: data lives in <script id="ktc-players">…JSON…</script>.
    m = re.search(r'id=["\']ktc-players["\'][^>]*>(.*?)</', html, re.DOTALL)
    if not m:
        # Legacy fallback: inline `var playersArray = [...]`.
        m = re.search(r"var playersArray\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not m:
        raise ValueError("KTC player data not found (layout may have changed)")
    arr = json.loads(m.group(1).strip())
    log.info("KTC returned %d players", len(arr))
    return arr


def _match_players(ktc_players: list[dict], sleeper_players: dict[str, Player]) -> list[DynastyRanking]:
    # Build lookup of sleeper players keyed by (normalized name, position).
    by_key: dict[tuple[str, str], str] = {}
    by_pos: dict[str, list[tuple[str, str]]] = {}  # position -> [(norm_name, pid)]
    for pid, p in sleeper_players.items():
        if not p.position or not p.name:
            continue
        key = (_norm(p.name), p.position)
        by_key[key] = pid
        by_pos.setdefault(p.position, []).append((_norm(p.name), pid))

    rankings: list[DynastyRanking] = []
    unmatched: list[str] = []
    for kp in ktc_players:
        name = kp.get("playerName") or ""
        pos = kp.get("position") or ""
        sf = kp.get("superflexValues") or {}
        norm = _norm(name)
        pid = by_key.get((norm, pos), "")
        if not pid:
            # fuzzy within position
            best, best_score = "", 0.0
            for cand_name, cand_pid in by_pos.get(pos, []):
                score = difflib.SequenceMatcher(None, norm, cand_name).ratio()
                if score > best_score:
                    best, best_score = cand_pid, score
            if best_score >= MATCH_THRESHOLD:
                pid = best
            else:
                unmatched.append(f"{name} ({pos}) KTC={sf.get('value')} best={best_score:.2f}")

        rankings.append(DynastyRanking(
            player_id=pid,
            name=name,
            position=pos,
            ktc_value=int(sf.get("value") or 0),
            overall_rank=int(sf.get("rank") or 0),
            position_rank=int(sf.get("positionalRank") or 0),
            trend_7day=int(sf.get("overall7DayTrend") or 0),
            is_rookie=bool(kp.get("rookie")),
            age=float(kp.get("age") or 0),
        ))

    if unmatched:
        STATE_DIR.mkdir(exist_ok=True)
        UNMATCHED_LOG.write_text("\n".join(unmatched))
        log.warning("%d KTC players unmatched -> %s", len(unmatched), UNMATCHED_LOG)
    return rankings


def _serialize(rankings: list[DynastyRanking]) -> dict:
    return {"timestamp": time.time(), "players": [r.__dict__ for r in rankings]}


def _deserialize(data: dict) -> list[DynastyRanking]:
    return [DynastyRanking(**p) for p in data.get("players", [])]


def load_rankings_cache() -> list[DynastyRanking]:
    if RANKINGS_CACHE.exists():
        return _deserialize(json.loads(RANKINGS_CACHE.read_text()))
    log.warning("No rankings cache found")
    return []


def fetch_ktc_rankings(sleeper_players: dict[str, Player], skip: bool = False) -> list[DynastyRanking]:
    """Return rankings, using cache if fresh (<6h) or skip=True forces cache."""
    if RANKINGS_CACHE.exists():
        cached = json.loads(RANKINGS_CACHE.read_text())
        age = time.time() - cached.get("timestamp", 0)
        if skip or age < CACHE_TTL_SECONDS:
            log.info("Using cached KTC rankings (%.1fh old)%s", age / 3600, " [--skip-rankings]" if skip else "")
            return _deserialize(cached)

    try:
        ktc_players = _scrape_ktc()
    except Exception as exc:  # noqa: BLE001
        log.error("KTC scrape failed: %s", exc)
        if RANKINGS_CACHE.exists():
            log.warning("Falling back to stale rankings cache")
            return _deserialize(json.loads(RANKINGS_CACHE.read_text()))
        return []

    rankings = _match_players(ktc_players, sleeper_players)
    matched = sum(1 for r in rankings if r.player_id)
    log.info("Matched %d/%d KTC players to Sleeper IDs", matched, len(rankings))
    STATE_DIR.mkdir(exist_ok=True)
    RANKINGS_CACHE.write_text(json.dumps(_serialize(rankings)))
    return rankings
