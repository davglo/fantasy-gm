"""Trade discovery engine (Mode A: pre-computed at build time).

Identify my sell candidates, then for each find the best opponent targets where
their surplus overlaps my (relative) need, balanced within +/-12% KTC.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from analysts.opponents import OpponentProfile
from analysts.roster import EnrichedPlayer

log = logging.getLogger(__name__)

BALANCE_PCT = 0.12


@dataclass
class TradeTarget:
    team_name: str
    owner_id: str
    their_player: str
    their_value: int
    rationale: str
    ktc_balance: int            # + = I gain value


@dataclass
class SellCandidate:
    player: EnrichedPlayer
    reasons: list[str]
    urgency: str
    targets: list[TradeTarget] = field(default_factory=list)


@dataclass
class BuyTarget:
    player: EnrichedPlayer
    from_team: str
    owner_id: str
    rationale: str


def _sell_reasons(p: EnrichedPlayer, surplus: list[str], qb_count: int, qb_rank: int,
                  contending: bool) -> list[str]:
    reasons = []
    if p.position == "QB" and qb_count >= 3 and qb_rank >= qb_count - 1:
        reasons.append("Superflex only needs 2 QBs — you're carrying a tradeable QB")
    if contending and p.slot_type == "starter":
        return reasons      # in a title race, producing starters are holds, not sells
    if p.age_score < 0.75 and p.market_value > 2500:
        reasons.append(f"Aging ({p.dynasty_window}, age {p.age}) but still valuable — sell window")
    if p.trend_7day < -150:
        reasons.append(f"KTC sliding ({p.trend_7day:+d} 7-day)")
    if p.position in surplus and p.market_value > 1500:
        reasons.append(f"{p.position} surplus — you have startable depth here")
    if p.injury_flag and p.market_value > 2200:
        note = f" ({p.injury_history})" if p.injury_history else ""
        reasons.append(f"Injury risk{note} while value is still high")
    return reasons


def find_sell_candidates(my_enriched: list[EnrichedPlayer], metrics: dict,
                         opponents: list[OpponentProfile],
                         contending: bool = False) -> list[SellCandidate]:
    surplus = metrics["surplus_positions"]
    qbs = sorted([p for p in my_enriched if p.position == "QB"], key=lambda x: -x.market_value)
    qb_count = len(qbs)
    qb_rank = {p.player_id: i for i, p in enumerate(qbs)}

    candidates: list[SellCandidate] = []
    for p in my_enriched:
        reasons = _sell_reasons(p, surplus, qb_count, qb_rank.get(p.player_id, 0), contending)
        if not reasons:
            continue
        urgency = "This week" if (p.trend_7day < -150 or p.injury_flag) else \
                  "When value peaks" if p.age_score >= 1.0 else "This month"
        candidates.append(SellCandidate(player=p, reasons=reasons, urgency=urgency))

    candidates.sort(key=lambda c: -c.player.market_value)
    return candidates


def _balance(my_val: int, their_val: int) -> tuple[int, float]:
    bal = their_val - my_val      # + = I gain
    pct = bal / my_val if my_val else 0
    return bal, pct


def build_targets(candidate: SellCandidate,
                  opponents: list[OpponentProfile],
                  opponent_enriched: dict[str, list[EnrichedPlayer]],
                  my_weak: list[str]) -> list[TradeTarget]:
    """Find opponents who would want my player and have something I'd take back."""
    p = candidate.player
    targets: list[TradeTarget] = []
    for opp in opponents:
        plist = opponent_enriched.get(opp.owner_id, [])
        # what I'd want: their best player at a position I'm thin/neutral on, near my player's value
        wishlist = [x for x in plist if x.market_value > 700]
        if my_weak:
            wishlist = [x for x in wishlist if x.position in my_weak] or wishlist
        for x in sorted(wishlist, key=lambda z: -z.market_value):
            bal, pct = _balance(p.market_value, x.market_value)
            if abs(pct) <= BALANCE_PCT:
                why = f"{opp.team_name} is {opp.dynasty_tier}/{opp.trade_motivation}"
                if x.position in opp.surplus_positions:
                    why += f"; {x.position} is surplus for them"
                targets.append(TradeTarget(
                    team_name=opp.team_name, owner_id=opp.owner_id,
                    their_player=x.name, their_value=x.market_value,
                    rationale=why, ktc_balance=bal,
                ))
                break   # one best fit per team
    targets.sort(key=lambda t: -t.ktc_balance)
    return targets[:3]


def find_buy_targets(opponents: list[OpponentProfile],
                     opponent_enriched: dict[str, list[EnrichedPlayer]],
                     my_weak: list[str], needs: dict | None = None,
                     contending: bool = False, contention: dict | None = None) -> list[BuyTarget]:
    need_pos = {pos for pos, n in (needs or {}).items() if n["need"] >= 0.3}
    odds = {o: c.status for o, c in (contention or {}).items()}
    buys: list[BuyTarget] = []
    for opp in opponents:
        for p in opponent_enriched.get(opp.owner_id, []):
            young_cheap = p.age_score >= 1.1 and p.market_value < 3500
            rising = p.trend_7day > 100 and p.value_tier in ("Depth", "Stash")
            est_rookie = p.is_rookie and p.market_value > 1500
            fills = p.position in my_weak and p.market_value >= 2000
            upgrade = contending and p.position in need_pos and p.market_value >= 2500
            if not (young_cheap or rising or est_rookie or fills or upgrade):
                continue
            why = []
            if upgrade:
                why.append(f"proven starter at your {p.position} need — a title-run upgrade")
            if fills:
                why.append(f"fills your {p.position} need")
            if young_cheap:
                why.append(f"young ({p.dynasty_window}) below peak price")
            if rising:
                why.append(f"rising {p.trend_7day:+d}")
            if odds.get(opp.owner_id) == "Long shot":
                why.append("their season is slipping — likely seller")
            elif opp.trade_motivation == "Selling":
                why.append("their team is selling")
            buys.append(BuyTarget(player=p, from_team=opp.team_name, owner_id=opp.owner_id,
                                  rationale=", ".join(why)))
    # Realism first: long shots sell, contenders don't. Then by value.
    order = {"Long shot": 0, "Bubble": 1, "Contender": 2}
    buys.sort(key=lambda b: (order.get(odds.get(b.owner_id), 1), -b.player.market_value))
    return buys[:12]


def run_trade_discovery(my_enriched, metrics, opponents, opponent_enriched,
                        needs=None, contending=False, contention=None):
    candidates = find_sell_candidates(my_enriched, metrics, opponents, contending)
    my_weak = metrics["weak_positions"]
    for c in candidates:
        c.targets = build_targets(c, opponents, opponent_enriched, my_weak)
    buys = find_buy_targets(opponents, opponent_enriched, my_weak, needs, contending, contention)
    log.info("Trade discovery (%s mode): %d sell candidates, %d buy targets",
             "contender" if contending else "standard", len(candidates), len(buys))
    return candidates, buys


def reprice_packages(trade_finder: dict, value_by_name: dict[str, int]) -> int:
    """Recompute every Trade Finder package from CURRENT market values so the
    math never goes stale. The analysis supplies who to target; the numbers
    (balance + fairness label) always come from live values. Returns # repriced."""
    n = 0
    for entry in trade_finder.values():
        for t in entry.get("top_targets", []):
            give, get = t.get("i_give") or [], t.get("i_receive") or []
            if not (give and get and all(x in value_by_name for x in give + get)):
                continue
            gv = sum(value_by_name[x] for x in give)
            rv = sum(value_by_name[x] for x in get)
            pct = (rv - gv) / gv if gv else 0
            t["ktc_balance"] = rv - gv
            t["fairness"] = ("You win" if pct >= 0.05 else "Fair" if pct >= -0.05
                             else "Slight overpay" if pct >= -0.15 else "Overpay")
            n += 1
    return n
