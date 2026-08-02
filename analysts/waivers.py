"""Free-agent scanner: unowned players with KTC > 500, tiered by add priority."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from analysts.roster import EnrichedPlayer

log = logging.getLogger(__name__)


@dataclass
class FreeAgent:
    player: EnrichedPlayer
    add_priority: str       # Immediate Add | Watchlist | Deep Stash
    fills_need: bool


def _priority(value: int) -> str:
    if value > 1500:
        return "Immediate Add"
    if value >= 800:
        return "Watchlist"
    return "Deep Stash"


def scan_free_agents(fa_list: list[EnrichedPlayer], weak_positions: list[str]) -> list[FreeAgent]:
    out = [
        FreeAgent(player=p, add_priority=_priority(p.market_value),
                  fills_need=p.position in weak_positions)
        for p in sorted(fa_list, key=lambda x: -x.market_value)
        if p.market_value > 350
    ]
    log.info("FA scan: %d players (>350 value), %d immediate adds",
             len(out), sum(1 for f in out if f.add_priority == "Immediate Add"))
    return out[:20]
