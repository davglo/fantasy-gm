"""Pick value engine + rookie draft War Room.

Draft is at slot 9, 12-team, 4 rounds. Sleeper currently reports type='linear'
but Dave expects snake; we compute from the actual draft type and flag the
conflict. All pick values are estimates and labelled "~est" in the UI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from analysts.roster import EnrichedPlayer
from fetchers.rankings import DynastyRanking
from fetchers.sleeper import SleeperData

log = logging.getLogger(__name__)

TEAMS = 12
EXPECTED_TYPE = "snake"     # Dave's stated format; flagged if API disagrees

# 1st-round slot values (12-team superflex).
ROUND1_VALUES = {
    1: 8200, 2: 7400, 3: 6800, 4: 6000, 5: 5400, 6: 4800,
    7: 4200, 8: 3700, 9: 3200, 10: 2900, 11: 2600, 12: 2300,
}
# need-multiplier for draft targeting (depth = no penalty; you draft BPA for youth)
NEED_MULT = {"fills": 1.3, "neutral": 1.0, "depth": 1.0}


@dataclass
class PortfolioPick:
    season: str
    round: int
    label: str              # "2026 1.09"
    est_value: int
    tradeable: bool
    flag: str               # "DO NOT TRADE" | "OK to package" | "Protected" | ""


@dataclass
class RookieProspect:
    name: str
    position: str
    nfl_team: str
    ktc_value: int
    rookie_rank: int        # overall rank among rookies (by board_value)
    available_at: dict[str, str]   # pick_label -> "Likely Gone"|"May Be Available"|"Target"
    need_flag: str          # "fills"|"depth"|"surplus"
    target_score: float
    fc_value: int | None = None    # FantasyCalc value if matched
    board_value: int = 0           # value used for ranking (FC if present, else KTC)


@dataclass
class WarRoom:
    draft_type_actual: str
    draft_type_conflict: bool
    my_picks: list[str]            # pick labels at my slot, e.g. ["1.09","2.04",...]
    big_board: list[RookieProspect]
    targets_by_pick: dict[str, list[RookieProspect]]   # pick_label -> top targets


# ---------------------------------------------------------------------------
# Pick slot math
# ---------------------------------------------------------------------------
def slot_for_round(base_slot: int, rnd: int, draft_type: str) -> int:
    """Snake reverses even rounds; linear keeps the same slot every round."""
    if draft_type == "snake" and rnd % 2 == 0:
        return TEAMS - base_slot + 1
    return base_slot


def pick_label(rnd: int, slot: int) -> str:
    return f"{rnd}.{slot:02d}"


def round_value(rnd: int, slot: int) -> int:
    if rnd == 1:
        return ROUND1_VALUES.get(slot, 2300)
    if rnd == 2:
        return 1800 if slot <= 4 else 1400 if slot <= 8 else 1000
    if rnd == 3:
        return 450
    return 175


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------
_ROUND_SUFFIX = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def _fc_pick_price(fc_picks: dict[str, int], season: str, rnd: int) -> int | None:
    """FantasyCalc market price for a generic future pick, e.g. '2028 1st'."""
    return fc_picks.get(f"{season} {_ROUND_SUFFIX.get(rnd, f'{rnd}th')}")


def build_pick_portfolio(data: SleeperData, my_ktc_slot: int,
                         fc_picks: dict[str, int] | None = None) -> tuple[list[PortfolioPick], int]:
    """Own picks for the next seasons (minus any traded away) plus acquired
    picks from traded_picks. Priced by FantasyCalc pick values when available,
    the hardcoded slot table otherwise."""
    fc_picks = fc_picks or {}
    base_slot = data.draft.get("draft_order", {}).get(data.user_id) or 9
    dtype = data.draft.get("type") or "linear"
    rounds = (data.draft.get("settings") or {}).get("rounds") or 4
    draft_season = int(data.draft.get("season") or 2026)
    draft_done = data.draft.get("status") == "complete"
    start = draft_season + 1 if draft_done else draft_season
    seasons = [str(y) for y in range(start, start + 3)]

    my_rid = data.my_roster.roster_id
    # my original picks now controlled by someone else = traded away
    traded_away: set[tuple[str, int]] = set()
    for r in data.all_rosters:
        if r.roster_id == my_rid:
            continue
        for tp in r.future_picks:
            if tp.roster_id == my_rid:
                traded_away.add((tp.season, tp.round))

    picks: list[PortfolioPick] = []
    for season in seasons:
        for rnd in range(1, rounds + 1):
            if (season, rnd) in traded_away:
                continue
            current = (not draft_done) and season == str(draft_season)
            slot = slot_for_round(base_slot if current else my_ktc_slot, rnd, dtype)
            est = _fc_pick_price(fc_picks, season, rnd) or round_value(rnd, slot)
            label = (f"{season} {pick_label(rnd, slot)}" if current
                     else f"{season} R{rnd} (~slot {my_ktc_slot})")
            flag, tradeable = _pick_flag(rnd, current)
            picks.append(PortfolioPick(season, rnd, label, est, tradeable, flag))

    # picks acquired via trade (controlled by me, originally someone else's)
    team_by_rid = {r.roster_id: r.team_name for r in data.all_rosters}
    for tp in data.my_roster.future_picks:
        if tp.roster_id == my_rid:
            continue    # my own pick returned to me — already counted above
        est = _fc_pick_price(fc_picks, tp.season, tp.round) or round_value(tp.round, 6)
        label = f"{tp.season} R{tp.round} (from {team_by_rid.get(tp.roster_id, '?')})"
        flag = "Acquired — hold" if tp.round == 1 else "Acquired"
        picks.append(PortfolioPick(tp.season, tp.round, label, est, tp.round > 1, flag))

    picks.sort(key=lambda p: (p.season, p.round))
    total = sum(p.est_value for p in picks)
    return picks, total


def _pick_flag(rnd: int, current_season: bool) -> tuple[str, bool]:
    if current_season and rnd == 1:
        return "DO NOT TRADE (this draft)", False
    if rnd == 1:
        return "Protected (elite returns only)", False
    return "OK to package", True


# ---------------------------------------------------------------------------
# War Room
# ---------------------------------------------------------------------------
def _availability(rookie_rank: int, pick_round: int, pick_slot: int) -> str:
    """ADP heuristic relative to the overall pick number this round."""
    overall = (pick_round - 1) * TEAMS + pick_slot
    if rookie_rank < overall - 3:
        return "Likely Gone"
    if rookie_rank <= overall + 3:
        return "Target"
    return "May Be Available"


def _need_flag(position: str, weak: list[str], surplus: list[str]) -> str:
    """For a rookie draft you add youth regardless of current surplus, so only
    a weak-position fit gets a boost; everything else is best-player-available."""
    if position in weak:
        return "fills"
    if position in surplus:
        return "depth"      # informational only; no scoring penalty (see NEED_MULT)
    return "neutral"


def build_war_room(data: SleeperData, rankings: list[DynastyRanking],
                   metrics: dict, my_ktc_slot: int,
                   fc_values: dict[str, int] | None = None) -> WarRoom:
    fc_values = fc_values or {}
    base_slot = data.draft.get("draft_order", {}).get(data.user_id) or 9
    dtype = data.draft.get("type") or "linear"
    rounds = (data.draft.get("settings") or {}).get("rounds") or 4
    conflict = dtype != EXPECTED_TYPE
    if conflict:
        log.warning("DRAFT TYPE CONFLICT: Sleeper says '%s' but you expect '%s'. "
                    "Round 2/4 slots differ — fix the league setting before Friday.", dtype, EXPECTED_TYPE)

    weak, surplus = metrics["weak_positions"], metrics["surplus_positions"]

    # Rank by FantasyCalc value when matched, else KTC (Dave trusts FC).
    def board_val(r: DynastyRanking) -> int:
        return fc_values.get(r.player_id) or r.ktc_value

    rookies = sorted([r for r in rankings if r.is_rookie and r.ktc_value > 0],
                     key=lambda x: -board_val(x))
    if fc_values:
        log.info("War Room board ranked by FantasyCalc values")
    my_pick_labels = [pick_label(rnd, slot_for_round(base_slot, rnd, dtype)) for rnd in range(1, rounds + 1)]

    big_board: list[RookieProspect] = []
    for i, r in enumerate(rookies[:30], start=1):
        need = _need_flag(r.position, weak, surplus)
        bval = board_val(r)
        avail = {}
        for rnd in range(1, rounds + 1):
            slot = slot_for_round(base_slot, rnd, dtype)
            avail[pick_label(rnd, slot)] = _availability(i, rnd, slot)
        big_board.append(RookieProspect(
            name=r.name, position=r.position, nfl_team="",
            ktc_value=r.ktc_value, rookie_rank=i, available_at=avail,
            need_flag=need, target_score=bval * NEED_MULT[need],
            fc_value=fc_values.get(r.player_id), board_value=bval,
        ))

    # Targets at each of my picks: realistic (not "Likely Gone"), ranked by target_score.
    targets_by_pick: dict[str, list[RookieProspect]] = {}
    for rnd in range(1, rounds + 1):
        slot = slot_for_round(base_slot, rnd, dtype)
        lbl = pick_label(rnd, slot)
        realistic = [p for p in big_board if p.available_at[lbl] != "Likely Gone"]
        realistic.sort(key=lambda x: -x.target_score)
        n = 5 if rnd == 1 else 3
        targets_by_pick[lbl] = realistic[:n]

    log.info("War Room: type=%s%s, my picks=%s, %d rookies on board",
             dtype, " [CONFLICT vs snake]" if conflict else "", my_pick_labels, len(big_board))
    return WarRoom(
        draft_type_actual=dtype, draft_type_conflict=conflict,
        my_picks=my_pick_labels, big_board=big_board, targets_by_pick=targets_by_pick,
    )
