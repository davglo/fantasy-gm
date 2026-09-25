# Weekly analysis refresh

The scheduled task runs this every Tuesday morning (after Monday Night Football,
before waivers). It regenerates the written analysis in `state/ai_manual.json`
from live data, then publishes. Everything numeric on the dashboard (values,
title odds, needs, free agents, trade balances) is computed on every build — this
refresh only updates the written takes.

## Steps

1. `cd ~/Claude/fantasy-gm && git pull`
2. `python3 build.py --dry-run` — refreshes all data and writes `state/context.json`.
3. Read `state/context.json` (live digest) and the current `state/ai_manual.json`.
4. Write a new `state/ai_manual.json` following the rules and schema below.
5. `python3 build.py --dry-run` again. The log must show `Analysis check: OK`.
   If it lists missing players or unpriced names, fix the JSON and rebuild.
6. `git add state/ai_manual.json && git commit -m "Weekly analysis refresh (Week N)" && git push`
   (end the commit message with the standard Claude co-author line).

## Rules for the analysis

- **Title odds drive the strategy** (`me.title_odds.status`):
  - **Contender** — win now. Never recommend selling a producing starter
    (e.g. an aging RB who starts). Hold them for the title run. Spend true
    surplus (QB3, benched surplus) and picks on upgrades at the biggest need.
  - **Bubble** — buy only upgrades that don't cost future value; sell vets who
    aren't starting.
  - **Long shot** — build for next year: sell aging producers for youth/picks.
- **Standing rule from Dave: never trade Patrick Mahomes.**
- **Target sellers first** — opponents whose `title_odds` is "Long shot" (or
  `motivation` "Selling"). Don't propose buying a star from a contender.
- **Needs come from `me.needs`** (0-1 per position; ≥0.3 is a real need). Keep
  every recommendation consistent with them.
- **Propose fair packages** using the values in `context.json` (within ~10%).
  The build recomputes every balance live and labels it; don't invent numbers.
- **Names must match exactly** — every name in `i_give` / `i_receive` must be a
  player name from `context.json` or a key in `pick_values` (e.g. `"2027 2nd"`),
  or it can't be priced.
- Reference the current record and week. No draft-era language.

## Schema (`state/ai_manual.json`)

```json
{
  "_generated_at": "YYYY-MM-DD",
  "_roster_hash": "<copy context.roster_hash>",
  "roster_grade": {
    "overall_grade": "A-", "headline": "...", "window_explanation": "...",
    "strengths": [{"label": "...", "detail": "..."}],
    "weaknesses": [{"label": "...", "detail": "..."}],
    "most_urgent_action": "...",
    "positional_grades": {"QB": {"grade": "A", "commentary": "..."}, "RB": {}, "WR": {}, "TE": {}}
  },
  "trade_strategy": {
    "trade_priority_order": ["..."],
    "sell_now": [{"player": "...", "urgency": "...", "reason": "...", "ask": "..."}],
    "buy_now": [{"player": "...", "from_team": "...", "reason": "...", "offer": "..."}],
    "do_not_trade": ["..."],
    "pick_strategy": "..."
  },
  "outlook": {
    "projected_power_rank": "...", "three_year_outlook": "...", "season_narrative": "...",
    "key_risks": ["..."], "key_upside_factors": ["..."],
    "teams_to_watch": [{"team": "...", "why": "..."}]
  },
  "trade_finder": {
    "<player_id>": {
      "sell_reasoning": "...",
      "top_targets": [{
        "rank": 1, "target_owner": "...", "their_player": "...", "why_they_trade": "...",
        "i_give": ["Player Name"], "i_receive": ["Player Name", "2027 2nd"],
        "verdict": "Do it | Ask | Hold | ...", "confidence": "High | Medium | Low"
      }],
      "what_to_avoid": "...", "alternative": "..."
    }
  }
}
```

`trade_finder` needs an entry for **every** id in `context.movable_player_ids`.
Core holds get an entry with a short "hold" rationale and an empty `top_targets`.
`i_give` / `i_receive` must be JSON arrays, never strings.
