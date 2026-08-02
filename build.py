"""Sleeper Dynasty Team Consultant — orchestrator.

fetch (Sleeper + KTC) -> enrich -> analyze -> AI -> render single HTML dashboard.

Flags:
  --dry-run         skip Claude calls, render from cached analysis
  --skip-rankings   force-use cached KTC rankings
  --draft-live      poll the live draft and print best-available (Friday mode)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from analysts.opponents import classify_opponents
from analysts.picks import build_pick_portfolio, build_war_room, slot_for_round
from analysts.roster import enrich_all_rosters, enrich_roster
from analysts.trades import run_trade_discovery
from analysts.waivers import scan_free_agents
from fetchers.fantasycalc import fetch_fc_values, load_fc_pick_values, load_fc_values
from fetchers.rankings import fetch_ktc_rankings, load_rankings_cache
from fetchers.sleeper import fetch_all_sleeper_data, fetch_draft_picks

SLEEPER_USERNAME = "thejabronibeatr"
SLEEPER_LEAGUE_ID = "1312154605395652608"

log = logging.getLogger("build")


def _my_ktc_slot(data, rank_by_id, fc_values) -> int:
    """Projected future draft slot from roster strength (best value -> latest pick)."""
    totals = sorted(
        ((sum(p.market_value for p in enrich_roster(r, data.players, rank_by_id, fc_values)), r.owner_id)
         for r in data.all_rosters), reverse=True)
    my_rank = next(i for i, (_, oid) in enumerate(totals) if oid == data.user_id)
    return 12 - my_rank


def _draft_recap(data) -> list[dict]:
    """My selections from the completed draft, for the dashboard recap."""
    picks = fetch_draft_picks(data.draft_id)
    my_rid = data.my_roster.roster_id
    recap = []
    for p in picks:
        if p.get("roster_id") != my_rid:
            continue
        player = data.players.get(str(p.get("player_id")))
        recap.append({
            "pick_no": p.get("pick_no"),
            "round": p.get("round"),
            "name": player.name if player else f"player {p.get('player_id')}",
            "position": player.position if player else "?",
        })
    return recap


def draft_live(data, rankings, fc_values) -> None:
    """Friday co-pilot: poll live picks, recompute best-available at my next pick."""
    fc_values = fc_values or {}

    def board_val(r):
        return fc_values.get(r.player_id) or r.ktc_value

    src = "FantasyCalc" if fc_values else "KTC"
    rookies = sorted([r for r in rankings if r.is_rookie and r.ktc_value > 0], key=lambda x: -board_val(x))
    base_slot = data.draft.get("draft_order", {}).get(data.user_id) or 9
    dtype = data.draft.get("type") or "linear"
    log.info("Draft-live polling every 30s (Ctrl-C to stop). My slot=%d, type=%s, board=%s", base_slot, dtype, src)
    while True:
        picks = fetch_draft_picks(data.draft_id)
        drafted = {p.get("player_id") for p in picks if p.get("player_id")}
        available = [r for r in rookies if r.player_id not in drafted]
        n = len(picks)
        rnd = n // 12 + 1
        my_overall = (rnd - 1) * 12 + slot_for_round(base_slot, rnd, dtype)
        log.info("--- %d picks made. Your next pick ~%d overall (R%d). Best available (%s): ---", n, my_overall, rnd, src)
        for r in available[:8]:
            val = board_val(r)
            tag = f"FC={val}" if fc_values.get(r.player_id) else f"KTC={r.ktc_value}"
            print(f"  {r.name:22}{r.position:3} {tag}")
        if picks and all(p.get("player_id") for p in picks) and n >= 48:
            log.info("Draft appears complete.")
            break
        time.sleep(30)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-rankings", action="store_true")
    ap.add_argument("--draft-live", action="store_true")
    args = ap.parse_args()

    log.info("Fetching Sleeper data...")
    data = fetch_all_sleeper_data(SLEEPER_USERNAME, SLEEPER_LEAGUE_ID)
    if not data:
        log.error("Sleeper fetch failed; aborting")
        sys.exit(1)

    log.info("Fetching KTC superflex rankings...")
    rankings = (load_rankings_cache() if args.skip_rankings
                else fetch_ktc_rankings(data.players, skip=args.skip_rankings))
    if not rankings:
        log.warning("No KTC rankings available — dashboard will show Sleeper data only")

    log.info("Fetching FantasyCalc values...")
    fc_values = (load_fc_values() if args.skip_rankings
                 else fetch_fc_values(rankings, skip=args.skip_rankings))
    fc_picks = load_fc_pick_values()

    if args.draft_live:
        draft_live(data, rankings, fc_values)
        return

    rank_by_id = {r.player_id: r for r in rankings if r.player_id}
    draft_done = data.draft.get("status") == "complete"

    log.info("Enriching rosters...")
    my_enriched, opponent_enriched, fa_list, metrics = enrich_all_rosters(data, rankings, fc_values)

    log.info("Classifying opponents...")
    opponent_profiles = classify_opponents(data, opponent_enriched, my_enriched)

    log.info("Running trade discovery...")
    sell_candidates, buys = run_trade_discovery(my_enriched, metrics, opponent_profiles, opponent_enriched)

    log.info("Analyzing picks%s...", " (draft complete — recap mode)" if draft_done else " + War Room")
    my_slot = _my_ktc_slot(data, rank_by_id, fc_values)
    pick_portfolio, pick_total = build_pick_portfolio(data, my_slot, fc_picks)
    war_room = None if draft_done else build_war_room(data, rankings, metrics, my_slot, fc_values)
    draft_recap = _draft_recap(data) if draft_done else []

    log.info("Scanning free agents...")
    fa_analysis = scan_free_agents(fa_list, metrics["weak_positions"])

    log.info("Running AI analysis%s...", " (--dry-run, cache only)" if args.dry_run else "")
    from analysts.ai import run_ai_analysis
    ai = run_ai_analysis(my_enriched, metrics, opponent_profiles, sell_candidates, buys,
                         pick_portfolio, war_room, fa_list, opponent_enriched, dry_run=args.dry_run)

    log.info("Rendering dashboard...")
    from renderer.dashboard import render_dashboard
    out = render_dashboard(data, metrics, my_enriched, opponent_profiles, sell_candidates, buys,
                           pick_portfolio, pick_total, war_room, fa_analysis, ai, rankings,
                           draft_recap=draft_recap)
    log.info("Done -> %s", out)


if __name__ == "__main__":
    main()
