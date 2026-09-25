"""Free-agent scanner: scores unowned players on roster need, current value, and
future upside, weighted by whether this team is contending this year.

The summary text is generated from the same scores as the list, so the two can
never contradict each other.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from analysts.roster import EnrichedPlayer

log = logging.getLogger(__name__)

# (need weight, youth weight) by title odds: contenders chase current help at
# thin spots, long shots chase youth/upside, bubble teams split the difference.
MODE = {"Contender": (1.0, 0.2), "Bubble": (0.8, 0.5), "Long shot": (0.5, 1.0)}
FOCUS = {"Contender": "current production at thin spots",
         "Bubble": "a mix of immediate help and upside",
         "Long shot": "young upside over short-term help"}
NEED_BAR = 0.3      # need score at which a position counts as a real need
SET_BAR = 0.15      # below this, the position is set
UPGRADE_MARGIN = 1.15   # must beat my next-man-up by 15%+ to be worth a roster move


@dataclass
class FreeAgent:
    player: EnrichedPlayer
    add_priority: str       # Immediate Add | Watchlist | Deep Stash
    fills_need: bool
    score: float = 0.0
    reason: str = ""


def scan_free_agents(fa_list: list[EnrichedPlayer], needs: dict[str, dict],
                     status: str) -> list[FreeAgent]:
    need_w, youth_w = MODE.get(status, MODE["Bubble"])
    out: list[FreeAgent] = []
    for p in fa_list:
        n = needs.get(p.position)
        if not n or p.market_value <= 350:
            continue
        need = n["need"]
        current = min(p.market_value / 3000, 1.5)
        score = current * (0.5 + need_w * need) * (1 + youth_w * (p.age_score - 1))
        upgrade = p.market_value > n["depth_value"] * UPGRADE_MARGIN   # clearly beats my next man up
        young = p.age_score >= 1.25
        if upgrade and need >= NEED_BAR:
            priority = "Immediate Add"
        elif upgrade or (young and youth_w >= 0.5):
            priority = "Watchlist"
        elif young:
            priority = "Deep Stash"
        else:
            continue    # no roster improvement now and no upside — not worth a spot

        bits = []
        if need >= NEED_BAR:
            bits.append(f"fills your {p.position} need")
        if upgrade:
            bits.append(f"beats your next-man-up at {p.position}"
                        + (f" ({n['depth_player']}, {n['depth_value']:,})" if n["depth_player"] else ""))
        elif need < SET_BAR:
            bits.append(f"{p.position} is set — depth only")
        if young:
            bits.append(p.dynasty_window.lower() + (f", age {p.age}" if p.age else ""))
        elif p.dynasty_window == "Declining":
            bits.append("declining — short-term help only")
        out.append(FreeAgent(p, priority, need >= NEED_BAR, round(score, 2), "; ".join(bits)))

    out.sort(key=lambda f: -f.score)
    log.info("FA scan (%s mode): %d candidates, %d immediate adds", status, len(out),
             sum(1 for f in out if f.add_priority == "Immediate Add"))
    return out[:20]


def waiver_summary(needs: dict[str, dict], status: str, fas: list[FreeAgent]) -> str:
    ordered = sorted(needs.items(), key=lambda kv: -kv[1]["need"])
    parts = [f"{status} mode: prioritizing {FOCUS.get(status, FOCUS['Bubble'])}."]
    top_pos, top = ordered[0]
    if top["need"] >= NEED_BAR:
        why = []
        if top["start_gap"] > 0:
            why.append("starters trail the league")
        if top["aging"] >= 0.5:
            why.append("starters are aging")
        if top["depth_gap"] > 0:
            why.append("thin depth")
        parts.append(f"Biggest need: {top_pos} ({', '.join(why) or 'depth'}).")
    else:
        parts.append("No real positional holes — adds are depth or upside plays.")
    set_pos = [pos for pos, n in ordered if n["need"] < SET_BAR]
    if set_pos:
        parts.append(f"Set at {', '.join(set_pos)} — adds there are depth only.")
    imm = [f for f in fas if f.add_priority == "Immediate Add"]
    parts.append(f"Top add: {imm[0].player.name} ({imm[0].player.position})." if imm
                 else "No must-add on the wire this week.")
    return " ".join(parts)
