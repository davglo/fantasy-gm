"""Sleeper Dynasty Team Consultant — orchestrator.

fetch (Sleeper + KTC) -> enrich -> analyze -> AI -> render single HTML dashboard.

Flags:
  --dry-run         skip Claude calls, render from cached analysis
  --skip-rankings   force-use cached KTC rankings
  --draft-live      poll the live draft and print best-available (Friday mode)
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
import time
from pathlib import Path

from analysts.opponents import assess_contention, classify_opponents
from analysts.picks import build_pick_portfolio, build_war_room, slot_for_round
from analysts.roster import enrich_all_rosters, enrich_roster, positional_needs
from analysts.trades import reprice_packages, run_trade_discovery
from analysts.waivers import scan_free_agents, waiver_summary
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


CONTEXT_FILE = Path(__file__).resolve().parent / "state" / "context.json"


def _write_context(data, metrics, my_enriched, opponent_enriched, opponent_profiles,
                   contention, needs, sell_candidates, buys, fa_analysis, fc_picks, movable) -> None:
    """Live-data digest the weekly analysis refresh reads (docs/weekly-refresh.md)."""
    from analysts.ai import league_roster_hash

    def slim(p):
        return {"id": p.player_id, "name": p.name, "pos": p.position, "age": p.age,
                "window": p.dynasty_window, "slot": p.slot_type, "value": p.market_value}

    me = contention[data.user_id]
    my_picks = [f"{tp.season} R{tp.round} (from roster {tp.roster_id})"
                for tp in data.my_roster.future_picks if tp.roster_id != data.my_roster.roster_id]
    ctx = {
        "generated_at": datetime.datetime.now().isoformat(timespec="minutes"),
        "roster_hash": league_roster_hash(my_enriched, opponent_enriched),
        "week": (data.league.get("settings") or {}).get("leg"),
        "me": {"team": data.my_roster.team_name,
               "record": f"{data.my_roster.wins}-{data.my_roster.losses}",
               "points_for": data.my_roster.fpts, "total_value": metrics["total_value"],
               "title_odds": vars(me), "needs": needs, "acquired_picks": my_picks},
        "my_roster": [slim(p) for p in sorted(my_enriched, key=lambda p: -p.market_value)],
        "movable_player_ids": movable,
        "opponents": [{
            "team": o.team_name, "record": f"{o.wins}-{o.losses}", "points_for": o.points_for,
            "title_odds": contention[o.owner_id].status, "roster_build": o.dynasty_tier,
            "motivation": o.trade_motivation, "weak": o.weak_positions, "surplus": o.surplus_positions,
            "top_players": [slim(p) for p in sorted(opponent_enriched.get(o.owner_id, []),
                                                    key=lambda p: -p.market_value)[:10]],
        } for o in opponent_profiles],
        "sell_candidates": [{"name": c.player.name, "value": c.player.market_value,
                             "reasons": c.reasons} for c in sell_candidates[:10]],
        "buy_targets": [{"name": b.player.name, "pos": b.player.position, "value": b.player.market_value,
                         "from": b.from_team, "why": b.rationale} for b in buys[:12]],
        "free_agents": [{"name": f.player.name, "pos": f.player.position, "value": f.player.market_value,
                         "priority": f.add_priority, "reason": f.reason} for f in fa_analysis[:12]],
        "pick_values": fc_picks,
    }
    CONTEXT_FILE.write_text(json.dumps(ctx, indent=2, ensure_ascii=False))


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
    contention = assess_contention(data, opponent_profiles, metrics["total_value"])
    me = contention[data.user_id]
    needs = positional_needs(my_enriched, opponent_enriched)
    log.info("Title odds: %s (#%d composite — record #%d, PF #%d, value #%d) | needs: %s",
             me.status, me.rank, me.record_rank, me.pf_rank, me.value_rank,
             {pos: n["need"] for pos, n in needs.items()})

    log.info("Running trade discovery...")
    sell_candidates, buys = run_trade_discovery(my_enriched, metrics, opponent_profiles, opponent_enriched,
                                                needs, contending=me.status == "Contender",
                                                contention=contention)

    log.info("Analyzing picks%s...", "" if draft_done else " + War Room")
    my_slot = _my_ktc_slot(data, rank_by_id, fc_values)
    pick_portfolio, pick_total = build_pick_portfolio(data, my_slot, fc_picks)
    war_room = None if draft_done else build_war_room(data, rankings, metrics, my_slot, fc_values)

    log.info("Scanning free agents...")
    fa_analysis = scan_free_agents(fa_list, needs, me.status)
    fa_summary = waiver_summary(needs, me.status, fa_analysis)

    log.info("Running AI analysis%s...", " (--dry-run, cache only)" if args.dry_run else "")
    from analysts.ai import run_ai_analysis
    ai = run_ai_analysis(my_enriched, metrics, opponent_profiles, sell_candidates, buys,
                         pick_portfolio, war_room, fa_list, opponent_enriched, dry_run=args.dry_run)
    value_by_name = {p.name: p.market_value
                     for p in my_enriched + [q for pl in opponent_enriched.values() for q in pl]}
    value_by_name.update(fc_picks)
    tf = ai.get("trade_finder", {})
    log.info("Repriced %d Trade Finder packages at live values", reprice_packages(tf, value_by_name))

    movable = [p.player_id for p in my_enriched
               if p.market_value >= 800 and p.slot_type in ("starter", "bench", "taxi")]
    missing = [pid for pid in movable if pid not in tf]
    unpriced = sorted({x for e in tf.values() for t in e.get("top_targets", [])
                       for x in (t.get("i_give") or []) + (t.get("i_receive") or [])
                       if isinstance(x, str) and x not in value_by_name})
    log.info("Analysis check: %s", "OK" if not missing and not unpriced else
             f"missing Trade Finder for {missing}; unpriced names {unpriced}")

    _write_context(data, metrics, my_enriched, opponent_enriched, opponent_profiles,
                   contention, needs, sell_candidates, buys, fa_analysis, fc_picks, movable)

    log.info("Rendering dashboard...")
    from renderer.dashboard import render_dashboard
    out = render_dashboard(data, metrics, my_enriched, opponent_profiles, sell_candidates, buys,
                           pick_portfolio, pick_total, war_room, fa_analysis, ai, rankings,
                           contention=contention, fa_summary=fa_summary)
    log.info("Done -> %s", out)


if __name__ == "__main__":
    main()
