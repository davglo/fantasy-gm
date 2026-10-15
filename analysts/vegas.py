"""Betting-line trade signals: teams beating or missing their Vegas totals.

A team scoring well above its implied totals has been running hot and tends to
regress, so its players are sell-high candidates; a team far below has been
unlucky, so its players are buy-low candidates. Computed every build from
fetchers/vegas.py.
"""
from __future__ import annotations

LUCK_PPG = 3.0           # points/game vs Vegas before we call it a signal
MIN_VALUE = 1500
NEVER_TRADE = {"Patrick Mahomes"}


def _line(t: dict) -> str:
    return (f"team scoring {t['actual'] / max(t['games'], 1):.1f}/g vs Vegas "
            f"{t['avg_implied']:.1f}/g ({t['luck']:+.1f})")


def vegas_signals(my_enriched, opponent_profiles, opponent_enriched, contention,
                  needs, vegas: dict) -> dict:
    teams = (vegas or {}).get("teams", {})
    if not teams:
        return {"sell_high": [], "buy_low": []}
    need_pos = {pos for pos, n in (needs or {}).items() if n["need"] >= 0.3}

    sell = []
    for p in my_enriched:
        t = teams.get(p.team)
        if t and t["luck"] >= LUCK_PPG and p.market_value >= MIN_VALUE and p.name not in NEVER_TRADE:
            sell.append({"name": p.name, "pos": p.position, "team": p.team, "value": p.market_value,
                         "starter": p.slot_type == "starter", "luck": t["luck"], "why": _line(t)})
    sell.sort(key=lambda s: -s["luck"])

    order = {"Long shot": 0, "Bubble": 1, "Contender": 2}
    buy = []
    for o in opponent_profiles:
        status = contention[o.owner_id].status
        for p in opponent_enriched.get(o.owner_id, []):
            t = teams.get(p.team)
            if t and t["luck"] <= -LUCK_PPG and p.market_value >= MIN_VALUE:
                buy.append({"name": p.name, "pos": p.position, "team": p.team, "value": p.market_value,
                            "from": o.team_name, "owner_status": status, "luck": t["luck"],
                            "need": p.position in need_pos, "why": _line(t)})
    buy.sort(key=lambda b: (not b["need"], order.get(b["owner_status"], 1), -b["value"]))
    return {"sell_high": sell[:8], "buy_low": buy[:10]}
