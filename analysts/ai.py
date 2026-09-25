"""Claude AI analysis layer.

Five build-time calls plus a per-rostered-player Trade Finder pre-computation.
Every response is cached in state/last_analysis.json keyed by section + input
hash, so --dry-run renders from cache with no API calls.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from analysts.roster import EnrichedPlayer

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
CACHE_FILE = STATE_DIR / "last_analysis.json"
MAX_TOKENS = 4000

SYSTEM = ("You are a dynasty fantasy football expert. Direct, opinionated, specific — always "
          "reference actual player names. No hedging. Respond with valid JSON only, no preamble, "
          "no markdown fences.")


def _hash(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)   # strip fences if present
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _slim(p: EnrichedPlayer) -> dict:
    return {
        "name": p.name, "pos": p.position, "age": p.age, "value": p.market_value,
        "tier": p.value_tier, "window": p.dynasty_window, "trend": p.trend_7day,
        "slot": p.slot_type,
    }


def league_roster_hash(my_enriched, opponent_enriched) -> str:
    """Hash of league-wide OWNERSHIP (owner_id, player_id pairs): any trade or
    waiver move — including one between two other teams — changes it."""
    everyone = list(my_enriched) + [p for plist in opponent_enriched.values() for p in plist]
    return _hash(sorted((p.owner_id or "", p.player_id) for p in everyone))


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
def run_ai_analysis(my_enriched, metrics, opponent_profiles, sell_candidates, buys,
                    pick_portfolio, war_room, fa_list, opponent_enriched, dry_run: bool):
    """Run (or load from cache) all AI sections. Returns dict of section -> result."""
    roster_hash = league_roster_hash(my_enriched, opponent_enriched)
    manual_file = STATE_DIR / "ai_manual.json"
    if manual_file.exists():
        log.info("Loading manual AI analysis from %s", manual_file)
        result = json.loads(manual_file.read_text())
        stored = result.get("_roster_hash")
        if stored and stored != roster_hash:
            log.warning("Manual AI analysis is STALE — a league roster changed since it was "
                        "written (stored=%s, current=%s)", stored, roster_hash)
            result["_stale"] = True
        return result
    cache = _load_cache()
    client = None
    if not dry_run:
        import anthropic
        client = anthropic.Anthropic()

    def call(section: str, payload: dict, instruction: str) -> dict:
        key = f"{section}_{_hash(payload)}"
        if key in cache:
            log.info("AI[%s]: cache hit", section)
            return cache[key]
        if dry_run or client is None:
            log.warning("AI[%s]: no cache and --dry-run; returning empty", section)
            return {}
        log.info("AI[%s]: calling Claude...", section)
        user = f"{instruction}\n\nDATA:\n{json.dumps(payload, default=str)}"
        resp = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            result = _parse_json(text)
        except Exception as exc:  # noqa: BLE001
            log.error("AI[%s]: JSON parse failed: %s", section, exc)
            result = {"_error": "parse failed", "_raw": text[:500]}
        cache[key] = result
        _save_cache(cache)
        return result

    my_slim = [_slim(p) for p in my_enriched]
    opp_slim = {op.team_name: {"tier": op.dynasty_tier, "motivation": op.trade_motivation,
                               "value": op.total_value, "surplus": op.surplus_positions,
                               "weak": op.weak_positions, "picks": op.future_pick_count,
                               "tradeable": op.top_tradeable_players} for op in opponent_profiles}

    results = {}

    results["roster_grade"] = call("roster_grade", {
        "roster": my_slim, "metrics": {k: metrics[k] for k in
            ("total_value", "weak_positions", "surplus_positions", "dynasty_window", "positional_breakdown")},
    }, ("Grade this superflex half-PPR dynasty roster (12-team, no DEF/K). Return JSON: "
        '{"overall_grade","headline","strengths":[{"label","detail"}],"weaknesses":[{"label","detail"}],'
        '"dynasty_window","window_explanation","positional_grades":{"QB":{"grade","commentary"},'
        '"RB":{...},"WR":{...},"TE":{...}},"most_urgent_action"}'))

    results["trade_strategy"] = call("trade_strategy", {
        "sell_candidates": [{"player": c.player.name, "value": c.player.market_value,
                             "reasons": c.reasons, "urgency": c.urgency} for c in sell_candidates[:8]],
        "buy_targets": [{"player": b.player.name, "from": b.from_team, "why": b.rationale} for b in buys[:8]],
        "opponents": opp_slim, "my_surplus": metrics["surplus_positions"],
    }, ("Give a trade strategy. Context: QB room is deep (superflex needs 2). Return JSON: "
        '{"trade_priority_order":[...],"sell_now":[{"player","urgency","reason","ask"}],'
        '"buy_now":[{"player","from_team","reason","offer"}],"do_not_trade":[...],"pick_strategy"}'))

    if war_room is not None:
        results["pick_strategy"] = call("pick_strategy", {
            "picks": [{"label": p.label, "value": p.est_value, "flag": p.flag} for p in pick_portfolio],
            "weak_positions": metrics["weak_positions"],
            "draft_type": war_room.draft_type_actual, "my_picks": war_room.my_picks,
            "targets_at_first_pick": [{"name": t.name, "pos": t.position, "value": t.board_value}
                                      for t in war_room.targets_by_pick.get(war_room.my_picks[0], [])],
            "round_targets": {lbl: [{"name": t.name, "pos": t.position} for t in tg]
                              for lbl, tg in war_room.targets_by_pick.items()},
        }, ("Evaluate my pick assets and rookie draft strategy at my first pick. Return JSON: "
            '{"pick_portfolio_grade","portfolio_summary","draft_day_strategy",'
            '"top_pick_at_109":{"name","position","reason"},"fallback_options":[{"name","reason"}],'
            '"round_2_4_targets":[{"round","name","reason"}],"picks_to_protect":[...],"picks_ok_to_package":[...]}'))
    else:
        results["pick_strategy"] = {}

    results["outlook"] = call("outlook", {
        "my_roster": {"value": metrics["total_value"], "window": metrics["dynasty_window"]},
        "league": opp_slim,
        "note": "Season is pre-draft: no games played yet, so this is a preseason power read, not a record projection.",
    }, ("Give a preseason outlook for this dynasty team (NO games played yet — do not invent a W-L "
        "record; rank by roster strength). Return JSON: "
        '{"projected_power_rank","three_year_outlook","season_narrative","key_risks":[...],'
        '"key_upside_factors":[...],"teams_to_watch":[{"team","why"}]}'))

    results["fa_targets"] = call("fa_targets", {
        "weak_positions": metrics["weak_positions"], "dynasty_window": metrics["dynasty_window"],
        "free_agents": [{"name": f.name, "pos": f.position, "value": f.market_value,
                         "window": f.dynasty_window} for f in fa_list[:20]],
    }, ("Which free agents should I prioritize? Return JSON: "
        '{"add_immediately":[{"name","position","reason"}],"add_if_can":[{"name","position","reason"}],'
        '"deep_stash":[{"name","position","reason"}],"waiver_strategy"}'))

    # Per-rostered-player Trade Finder (pre-computed, baked into HTML).
    trade_finder: dict[str, dict] = {}
    movable = [p for p in my_enriched if p.market_value >= 800 and p.slot_type in ("starter", "bench", "taxi")]
    opp_relevant = {tn: [_slim(x) for x in sorted(plist, key=lambda z: -z.market_value) if x.market_value > 700][:8]
                    for tn, plist in
                    {op.team_name: opponent_enriched.get(op.owner_id, []) for op in opponent_profiles}.items()}
    for p in movable:
        trade_finder[p.player_id] = call(f"tf_{p.player_id}", {
            "moving": _slim(p), "my_surplus": metrics["surplus_positions"],
            "my_weak": metrics["weak_positions"], "opponents": opp_relevant,
        }, (f"I want to trade away {p.name} ({p.position}, age {p.age}, value {p.market_value}). "
            "Return JSON: {\"sell_reasoning\",\"top_targets\":[{\"rank\",\"target_owner\",\"their_player\","
            "\"why_they_trade\",\"i_give\":[...],\"i_receive\":[...],\"ktc_balance\",\"verdict\",\"confidence\"}],"
            "\"what_to_avoid\",\"alternative\"}"))
    results["trade_finder"] = trade_finder

    return results
