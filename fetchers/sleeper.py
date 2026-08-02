"""Sleeper API client for the dynasty consultant.

All network access to Sleeper lives here. Each call is wrapped so a single
endpoint failure degrades gracefully instead of killing the whole run.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE = "https://api.sleeper.app/v1"
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
PLAYERS_CACHE = STATE_DIR / "players_nfl.json"
PLAYERS_TTL_SECONDS = 24 * 3600


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Player:
    player_id: str
    name: str
    first_name: str
    last_name: str
    position: str           # QB/RB/WR/TE/K
    team: str               # NFL team abbreviation, or "FA"
    age: int
    years_exp: int
    is_rookie: bool         # years_exp == 0
    injury_status: str      # "", "Q", "D", "O", "IR", "Sus"
    injury_notes: str


@dataclass
class TradedPick:
    season: str
    round: int
    owner_id: str           # current controller (Sleeper roster_id as str)
    previous_owner_id: str
    roster_id: int          # which team's pick (determines draft slot value)


@dataclass
class TeamRoster:
    owner_id: str
    roster_id: int
    display_name: str
    team_name: str
    avatar: str
    starters: list[str] = field(default_factory=list)
    bench: list[str] = field(default_factory=list)
    taxi: list[str] = field(default_factory=list)
    reserve: list[str] = field(default_factory=list)   # IR
    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: float = 0.0
    future_picks: list[TradedPick] = field(default_factory=list)


@dataclass
class SleeperData:
    user_id: str
    league: dict
    draft: dict                                 # the (rookie) draft object
    my_roster: TeamRoster
    opponent_rosters: list[TeamRoster]
    all_rosters: list[TeamRoster]
    players: dict[str, Player]                  # keyed by player_id
    users: dict[str, dict]                      # keyed by user_id

    @property
    def draft_id(self) -> str:
        return self.draft.get("draft_id", "")


# ---------------------------------------------------------------------------
# Low-level fetch
# ---------------------------------------------------------------------------
def _get(path: str) -> object | None:
    url = f"{BASE}{path}"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        log.error("Sleeper fetch failed for %s: %s", path, exc)
        return None


def _load_players_db() -> dict:
    """Full player DB is huge (~5MB). Cache to disk, refresh if >24h old."""
    if PLAYERS_CACHE.exists():
        age = time.time() - PLAYERS_CACHE.stat().st_mtime
        if age < PLAYERS_TTL_SECONDS:
            log.info("Using cached players DB (%.1fh old)", age / 3600)
            return json.loads(PLAYERS_CACHE.read_text())
    log.info("Fetching fresh players DB from Sleeper...")
    data = _get("/players/nfl")
    if not data:
        if PLAYERS_CACHE.exists():
            log.warning("Players fetch failed; falling back to stale cache")
            return json.loads(PLAYERS_CACHE.read_text())
        return {}
    STATE_DIR.mkdir(exist_ok=True)
    PLAYERS_CACHE.write_text(json.dumps(data))
    return data


def _parse_player(pid: str, raw: dict) -> Player:
    years_exp = raw.get("years_exp") or 0
    first = raw.get("first_name") or ""
    last = raw.get("last_name") or ""
    name = (raw.get("full_name") or f"{first} {last}").strip()
    return Player(
        player_id=pid,
        name=name,
        first_name=first,
        last_name=last,
        position=raw.get("position") or "",
        team=raw.get("team") or "FA",
        age=raw.get("age") or 0,
        years_exp=years_exp,
        is_rookie=years_exp == 0,
        injury_status=raw.get("injury_status") or "",
        injury_notes=raw.get("injury_notes") or "",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def fetch_all_sleeper_data(username: str, league_id: str) -> SleeperData | None:
    user = _get(f"/user/{username}")
    if not user:
        log.error("Could not resolve user %s; aborting Sleeper fetch", username)
        return None
    user_id = user["user_id"]
    log.info("Resolved user %s -> %s", username, user_id)

    league = _get(f"/league/{league_id}") or {}
    rosters_raw = _get(f"/league/{league_id}/rosters") or []
    users_raw = _get(f"/league/{league_id}/users") or []
    traded_raw = _get(f"/league/{league_id}/traded_picks") or []
    drafts_raw = _get(f"/league/{league_id}/drafts") or []

    users = {u["user_id"]: u for u in users_raw}
    players_db = _load_players_db()
    players = {pid: _parse_player(pid, raw) for pid, raw in players_db.items()}

    # The rookie draft for this league (most recent / pre_draft one).
    draft = {}
    if drafts_raw:
        pre = [d for d in drafts_raw if d.get("status") == "pre_draft"]
        draft = (pre or drafts_raw)[0]

    # Group traded picks by current controlling roster_id.
    picks_by_owner: dict[int, list[TradedPick]] = {}
    for tp in traded_raw:
        pick = TradedPick(
            season=str(tp.get("season")),
            round=int(tp.get("round")),
            owner_id=str(tp.get("owner_id")),
            previous_owner_id=str(tp.get("previous_owner_id")),
            roster_id=int(tp.get("roster_id")),
        )
        picks_by_owner.setdefault(int(tp.get("owner_id")), []).append(pick)

    all_rosters: list[TeamRoster] = []
    for r in rosters_raw:
        owner_id = r.get("owner_id") or ""
        u = users.get(owner_id, {})
        meta = u.get("metadata") or {}
        settings = r.get("settings") or {}
        starters = [s for s in (r.get("starters") or []) if s and s != "0"]
        taxi = r.get("taxi") or []
        reserve = r.get("reserve") or []
        all_players = r.get("players") or []
        bench = [p for p in all_players if p not in starters and p not in taxi and p not in reserve]
        roster = TeamRoster(
            owner_id=owner_id,
            roster_id=int(r.get("roster_id")),
            display_name=u.get("display_name") or "Unknown",
            team_name=meta.get("team_name") or u.get("display_name") or f"Team {r.get('roster_id')}",
            avatar=u.get("avatar") or "",
            starters=starters,
            bench=bench,
            taxi=taxi,
            reserve=reserve,
            wins=settings.get("wins") or 0,
            losses=settings.get("losses") or 0,
            ties=settings.get("ties") or 0,
            fpts=float(settings.get("fpts") or 0),
            future_picks=picks_by_owner.get(int(r.get("roster_id")), []),
        )
        all_rosters.append(roster)

    my_roster = next((r for r in all_rosters if r.owner_id == user_id), None)
    if my_roster is None:
        log.error("Could not find a roster owned by %s in league %s", user_id, league_id)
        return None
    opponents = [r for r in all_rosters if r.owner_id != user_id]

    log.info(
        "Fetched league '%s': %d rosters, draft=%s (%s), my roster_id=%d (%d players)",
        league.get("name"), len(all_rosters), draft.get("draft_id"),
        draft.get("status"), my_roster.roster_id, len(my_roster.starters) + len(my_roster.bench),
    )

    return SleeperData(
        user_id=user_id,
        league=league,
        draft=draft,
        my_roster=my_roster,
        opponent_rosters=opponents,
        all_rosters=all_rosters,
        players=players,
        users=users,
    )


def fetch_draft_picks(draft_id: str) -> list[dict]:
    """Live picks for draft-night mode. Empty list pre-draft."""
    if not draft_id:
        return []
    return _get(f"/draft/{draft_id}/picks") or []
