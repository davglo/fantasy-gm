"""Roster enrichment: join Sleeper roster x player x KTC ranking.

Produces EnrichedPlayer objects and my-team roster_metrics. Only players that
are rostered somewhere or are top-300 KTC free agents are enriched (we never
load the full 2000-player DB into downstream prompts).
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

from fetchers.rankings import DynastyRanking
from fetchers.sleeper import Player, SleeperData, TeamRoster

log = logging.getLogger(__name__)

# Value tiers (FantasyCalc superflex scale — FC runs lower than KTC mid-range)
TIER_BREAKS = [(5500, "Elite"), (3000, "Starter"), (1000, "Depth"), (350, "Stash")]

# Dynasty age windows: (rising_below, prime_below) -> declining at/above prime_below
AGE_WINDOWS = {
    "QB": (26, 34),
    "RB": (22, 26),
    "WR": (24, 30),
    "TE": (25, 31),
}

KNOWN_INJURY_HISTORY = {
    "T.J. Hockenson": "ACL (2024) — monitor return trajectory",
}

INJURY_FLAG_STATUSES = {"O", "IR", "Sus"}
SF_QB_WEIGHT = 1.3
FA_TOP_N = 300


@dataclass
class EnrichedPlayer:
    player_id: str
    name: str
    position: str
    team: str
    age: int
    years_exp: int
    is_rookie: bool
    injury_status: str
    injury_notes: str

    slot_type: str           # starter | bench | taxi | ir | fa
    owner_id: str | None

    ktc_value: int
    overall_rank: int
    position_rank: int
    trend_7day: int
    value_tier: str

    age_score: float
    dynasty_window: str      # Rising | Prime | Declining | Rookie | Veteran
    positional_value_weight: float
    injury_flag: bool = False
    injury_history: str = ""
    fc_value: int = 0        # FantasyCalc value; 0 if unmatched

    @property
    def market_value(self) -> int:
        """Primary value: FantasyCalc (Dave's trusted source), KTC fallback."""
        return self.fc_value or self.ktc_value

    @property
    def effective_value(self) -> int:
        return int(self.market_value * self.positional_value_weight)


def _value_tier(v: int) -> str:
    for brk, label in TIER_BREAKS:
        if v >= brk:
            return label
    return "Cut"


def _age_window(position: str, age: int, is_rookie: bool) -> tuple[str, float]:
    """Return (window_label, age_score). age_score >1 = young/ascending."""
    if is_rookie:
        return "Rookie", 1.3
    rising, prime = AGE_WINDOWS.get(position, (24, 30))
    if age == 0:
        return "Veteran", 1.0
    if age < rising:
        return "Rising", 1.25
    if age < prime:
        return "Prime", 1.0
    return "Declining", 0.7


def _enrich(p: Player, rk: DynastyRanking | None, slot_type: str, owner_id: str | None,
            fc_values: dict[str, int] | None = None) -> EnrichedPlayer:
    ktc = rk.ktc_value if rk else 0
    fc = (fc_values or {}).get(p.player_id, 0)
    window, age_score = _age_window(p.position, p.age, p.is_rookie)
    weight = SF_QB_WEIGHT if p.position == "QB" else 1.0
    injury_flag = p.injury_status in INJURY_FLAG_STATUSES
    history = KNOWN_INJURY_HISTORY.get(p.name, "")
    return EnrichedPlayer(
        player_id=p.player_id, name=p.name, position=p.position, team=p.team,
        age=p.age, years_exp=p.years_exp, is_rookie=p.is_rookie,
        injury_status=p.injury_status, injury_notes=p.injury_notes,
        slot_type=slot_type, owner_id=owner_id,
        ktc_value=ktc, fc_value=fc,
        overall_rank=rk.overall_rank if rk else 0,
        position_rank=rk.position_rank if rk else 0,
        trend_7day=rk.trend_7day if rk else 0,
        value_tier=_value_tier(fc or ktc),
        age_score=age_score, dynasty_window=window, positional_value_weight=weight,
        injury_flag=injury_flag or bool(history), injury_history=history,
    )


def _slot_map(roster: TeamRoster) -> dict[str, str]:
    m: dict[str, str] = {}
    for pid in roster.starters:
        m[pid] = "starter"
    for pid in roster.bench:
        m[pid] = "bench"
    for pid in roster.taxi:
        m[pid] = "taxi"
    for pid in roster.reserve:
        m[pid] = "ir"
    return m


def enrich_roster(roster: TeamRoster, players: dict[str, Player],
                  rank_by_id: dict[str, DynastyRanking],
                  fc_values: dict[str, int] | None = None) -> list[EnrichedPlayer]:
    out: list[EnrichedPlayer] = []
    for pid, slot in _slot_map(roster).items():
        p = players.get(pid)
        if not p:
            continue
        out.append(_enrich(p, rank_by_id.get(pid), slot, roster.owner_id, fc_values))
    return out


# ---------------------------------------------------------------------------
# Roster metrics (my team)
# ---------------------------------------------------------------------------
# Real weekly starters by position for this league's lineup (QB, 2 RB, 2 WR, TE,
# 3 FLEX, SUPER_FLEX): SUPER_FLEX counted as a 2nd QB, FLEX split 1 RB / 2 WR.
STARTING_SLOTS = {"QB": 2, "RB": 3, "WR": 4, "TE": 1}
STARTER_VALUE = 2000   # true starter quality (FC scale), not depth


def _depth_grade(starter_quality: int, position: str) -> str:
    """Letter grade by count of true starter-quality (>=STARTER_VALUE) players vs slots."""
    need = STARTING_SLOTS.get(position, 2)
    ratio = starter_quality / need if need else 0
    if ratio >= 2.0:
        return "A"
    if ratio >= 1.5:
        return "B"
    if ratio >= 1.0:
        return "C"
    if ratio >= 0.5:
        return "D"
    return "F"


def compute_roster_metrics(enriched: list[EnrichedPlayer]) -> dict:
    by_pos: dict[str, list[EnrichedPlayer]] = {}
    for p in enriched:
        if p.position in ("QB", "RB", "WR", "TE"):
            by_pos.setdefault(p.position, []).append(p)

    breakdown: dict[str, dict] = {}
    surplus, weak = [], []
    age_curve: dict[str, float] = {}
    for pos, plist in by_pos.items():
        plist.sort(key=lambda x: -x.market_value)
        slots = STARTING_SLOTS.get(pos, 2)
        starter_quality = sum(1 for x in plist if x.market_value >= STARTER_VALUE)
        grade = _depth_grade(starter_quality, pos)
        avg_value = sum(x.market_value for x in plist) / len(plist) if plist else 0
        breakdown[pos] = {
            "count": len(plist),
            "starter_quality": starter_quality,
            "avg_value": round(avg_value),
            "top_player": plist[0].name if plist else "",
            "depth_grade": grade,
        }
        if starter_quality >= slots + 2:        # clear excess of startable bodies
            surplus.append(pos)
        elif starter_quality < slots:           # can't field quality starters
            weak.append(pos)
        # weighted (by value) avg age of meaningful players
        meaningful = [x for x in plist if x.market_value >= 350 and x.age]
        if meaningful:
            wsum = sum(x.market_value for x in meaningful)
            age_curve[pos] = round(sum(x.age * x.market_value for x in meaningful) / wsum, 1)

    total_value = sum(p.market_value for p in enriched)
    # QB surplus is special in superflex: >=3 startable QBs is a surplus
    qb_quality = breakdown.get("QB", {}).get("starter_quality", 0)
    if qb_quality >= 3 and "QB" not in surplus:
        surplus.append("QB")

    # Dynasty window heuristic from age + value distribution
    young_value = sum(p.market_value for p in enriched if p.age and p.age <= 25)
    old_value = sum(p.market_value for p in enriched if p.age and p.age >= 29)
    if total_value:
        young_pct = young_value / total_value
        if young_pct > 0.55:
            window = "Building"
        elif young_pct > 0.40:
            window = "Contending/Building"
        elif old_value / total_value > 0.40:
            window = "Win-now (aging)"
        else:
            window = "Contending"
    else:
        window = "Unknown"

    return {
        "total_value": total_value,
        "positional_breakdown": breakdown,
        "surplus_positions": surplus,
        "weak_positions": weak,
        "age_curve": age_curve,
        "dynasty_window": window,
    }


def positional_needs(my_enriched: list[EnrichedPlayer],
                     opponent_enriched: dict[str, list[EnrichedPlayer]]) -> dict[str, dict]:
    """0-1 need score per position, measured against the rest of the league:
    how far my starters trail the league-median starters, how thin my next man
    up is, and how much of my starting value is aging out (dynasty risk)."""
    def lineup(plist: list[EnrichedPlayer], pos: str):
        players = sorted((p for p in plist if p.position == pos), key=lambda p: -p.market_value)
        k = STARTING_SLOTS[pos]
        starters = players[:k]
        start_avg = sum(p.market_value for p in starters) / k
        nxt = players[k] if len(players) > k else None
        depth = nxt.market_value if nxt else 0
        start_val = sum(p.market_value for p in starters) or 1
        aging = sum(p.market_value for p in starters if p.dynasty_window == "Declining") / start_val
        weakest = starters[-1] if len(starters) == k else None
        return start_avg, depth, aging, weakest, nxt

    needs: dict[str, dict] = {}
    for pos in STARTING_SLOTS:
        league = [lineup(pl, pos) for pl in opponent_enriched.values()]
        med_start = statistics.median(x[0] for x in league) or 1
        med_depth = statistics.median(x[1] for x in league) or 1
        start_avg, depth, aging, weakest, nxt = lineup(my_enriched, pos)
        start_gap = max(0.0, (med_start - start_avg) / med_start)
        depth_gap = max(0.0, (med_depth - depth) / med_depth)
        needs[pos] = {
            "need": round(min(1.0, 1.4 * start_gap + 0.6 * depth_gap + 0.4 * aging), 2),
            "start_gap": round(start_gap, 2), "depth_gap": round(depth_gap, 2),
            "aging": round(aging, 2), "depth_value": depth,
            "depth_player": nxt.name if nxt else None,
            "weakest_starter": weakest.name if weakest else None,
        }
    return needs


# ---------------------------------------------------------------------------
# Top-level enrichment for all rosters + free agents
# ---------------------------------------------------------------------------
def enrich_all_rosters(data: SleeperData, rankings: list[DynastyRanking],
                       fc_values: dict[str, int] | None = None):
    rank_by_id = {r.player_id: r for r in rankings if r.player_id}
    fc_values = fc_values or {}

    my_enriched = enrich_roster(data.my_roster, data.players, rank_by_id, fc_values)
    opponent_enriched: dict[str, list[EnrichedPlayer]] = {}
    for r in data.opponent_rosters:
        opponent_enriched[r.owner_id] = enrich_roster(r, data.players, rank_by_id, fc_values)

    # Free agents: top-300 (by market value) players not rostered anywhere.
    rostered: set[str] = set()
    for r in data.all_rosters:
        rostered.update(r.starters + r.bench + r.taxi + r.reserve)
    draft_done = data.draft.get("status") == "complete"
    top300 = sorted([r for r in rankings if r.player_id],
                    key=lambda x: -(fc_values.get(x.player_id) or x.ktc_value))[:FA_TOP_N]
    fa_list: list[EnrichedPlayer] = []
    for rk in top300:
        if rk.player_id in rostered:
            continue
        p = data.players.get(rk.player_id)
        if not p:
            continue
        if p.is_rookie and not draft_done:
            # pre-draft: undrafted rookies are draft prospects, not waiver adds
            continue
        fa_list.append(_enrich(p, rk, "fa", None, fc_values))

    metrics = compute_roster_metrics(my_enriched)
    log.info(
        "Enriched: me=%d players (value %d [FC], window=%s, weak=%s, surplus=%s), %d FAs",
        len(my_enriched), metrics["total_value"], metrics["dynasty_window"],
        metrics["weak_positions"], metrics["surplus_positions"], len(fa_list),
    )
    return my_enriched, opponent_enriched, fa_list, metrics
