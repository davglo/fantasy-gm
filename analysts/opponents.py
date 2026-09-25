"""Opponent intelligence.

Classify the 12 teams on roster strength (FantasyCalc value), roster age, and
future pick capital. Real W-L records and points-for are carried through for the
in-season standings view once games are scored.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from analysts.roster import EnrichedPlayer, STARTING_SLOTS, STARTER_VALUE
from fetchers.sleeper import SleeperData, TeamRoster

log = logging.getLogger(__name__)


@dataclass
class OpponentProfile:
    owner_id: str
    roster_id: int
    team_name: str
    display_name: str

    total_value: int                # FC market value (KTC fallback)
    roster_percentile: int          # 0-100 vs all 12 teams
    avg_age: float
    future_pick_count: int

    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0

    surplus_positions: list[str] = field(default_factory=list)
    weak_positions: list[str] = field(default_factory=list)

    dynasty_tier: str = "Balanced"  # Win-Now | Balanced | Rebuilding
    trade_motivation: str = "Neutral"  # Buying | Selling | Neutral
    top_tradeable_players: list[str] = field(default_factory=list)


def _pos_strength(enriched: list[EnrichedPlayer]) -> tuple[list[str], list[str]]:
    by_pos: dict[str, list[EnrichedPlayer]] = {}
    for p in enriched:
        if p.position in STARTING_SLOTS:
            by_pos.setdefault(p.position, []).append(p)
    surplus, weak = [], []
    for pos, slots in STARTING_SLOTS.items():
        startable = sum(1 for x in by_pos.get(pos, []) if x.market_value >= STARTER_VALUE)
        if startable >= slots + 2:
            surplus.append(pos)
        elif startable < slots:
            weak.append(pos)
    return surplus, weak


def _weighted_age(enriched: list[EnrichedPlayer]) -> float:
    meaningful = [p for p in enriched if p.market_value >= 350 and p.age]
    if not meaningful:
        return 0.0
    wsum = sum(p.market_value for p in meaningful)
    return round(sum(p.age * p.market_value for p in meaningful) / wsum, 1)


def classify_opponents(data: SleeperData,
                       opponent_enriched: dict[str, list[EnrichedPlayer]],
                       my_enriched: list[EnrichedPlayer]) -> list[OpponentProfile]:
    # totals across ALL 12 teams (incl. me) for percentile ranking
    enriched_by_owner = dict(opponent_enriched)
    all_totals = {ow: sum(p.market_value for p in plist) for ow, plist in enriched_by_owner.items()}
    all_totals[data.user_id] = sum(p.market_value for p in my_enriched)
    sorted_vals = sorted(all_totals.values())
    profiles: list[OpponentProfile] = []
    for r in data.opponent_rosters:
        enriched = enriched_by_owner.get(r.owner_id, [])
        total = all_totals.get(r.owner_id, 0)
        pct = int(100 * sum(1 for v in sorted_vals if v <= total) / max(len(sorted_vals), 1))
        avg_age = _weighted_age(enriched)
        surplus, weak = _pos_strength(enriched)
        pick_count = len(r.future_picks)

        tier, motiv = _classify(total, sorted_vals, avg_age, pick_count)
        tradeable = [p.name for p in sorted(enriched, key=lambda x: -x.market_value)
                     if p.position in surplus][:4]

        profiles.append(OpponentProfile(
            owner_id=r.owner_id, roster_id=r.roster_id, team_name=r.team_name,
            display_name=r.display_name, total_value=total, roster_percentile=pct,
            avg_age=avg_age, future_pick_count=pick_count,
            wins=r.wins, losses=r.losses, ties=r.ties, points_for=r.fpts,
            surplus_positions=surplus, weak_positions=weak,
            dynasty_tier=tier, trade_motivation=motiv, top_tradeable_players=tradeable,
        ))
    profiles.sort(key=lambda p: -p.total_value)
    log.info("Classified %d opponents by roster value + age + pick capital", len(profiles))
    return profiles


@dataclass
class Contention:
    status: str             # Contender | Bubble | Long shot
    rank: int               # composite rank, 1 = best title odds
    record_rank: int
    pf_rank: int
    value_rank: int
    games: int


def assess_contention(data: SleeperData, profiles: list[OpponentProfile],
                      my_total: int) -> dict[str, Contention]:
    """Can each team win THIS year? Same yardstick for all 12 (keyed by owner_id):
    record + points-for once 3+ games are played, roster value before that."""
    value = {p.owner_id: p.total_value for p in profiles}
    value[data.user_id] = my_total
    rows = data.all_rosters
    games = max((r.wins + r.losses + r.ties for r in rows), default=0)

    def rank(key) -> dict[str, int]:
        return {r.owner_id: i + 1 for i, r in enumerate(sorted(rows, key=key))}

    def win_pct(r) -> float:
        return (r.wins + 0.5 * r.ties) / max(r.wins + r.losses + r.ties, 1)

    rec = rank(lambda r: (-win_pct(r), -r.fpts))
    pf = rank(lambda r: -r.fpts)
    val = rank(lambda r: -value.get(r.owner_id, 0))
    if games >= 3:
        score = {o: 0.5 * rec[o] + 0.3 * pf[o] + 0.2 * val[o] for o in rec}
    else:
        score = {o: float(v) for o, v in val.items()}

    playoff = (data.league.get("settings") or {}).get("playoff_teams") or 6
    out: dict[str, Contention] = {}
    for i, o in enumerate(sorted(score, key=score.get), 1):
        status = "Contender" if i <= playoff - 2 else "Bubble" if i <= playoff + 1 else "Long shot"
        out[o] = Contention(status, i, rec[o], pf[o], val[o], games)
    return out


def _classify(total: int, all_vals: list[int], avg_age: float, pick_count: int):
    """No records exist; use roster value + age + pick capital."""
    hi = sorted(all_vals, reverse=True)
    top_third = hi[len(hi) // 3] if hi else 0
    bot_third = hi[-(len(hi) // 3 or 1)] if hi else 0

    young = avg_age and avg_age <= 25.5
    old = avg_age and avg_age >= 27.5
    strong = total >= top_third
    weak_roster = total <= bot_third

    if strong and (old or pick_count <= 1):
        return "Win-Now", "Buying"
    if weak_roster and (young or pick_count >= 2):
        return "Rebuilding", "Selling"
    return "Balanced", "Neutral"
