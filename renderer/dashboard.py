"""Single self-contained HTML dashboard renderer."""
from __future__ import annotations

import datetime
import html
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

OUT = Path(__file__).resolve().parent.parent / "output" / "dashboard.html"

CSS = """
:root{--bg:#080C14;--card:#0F1520;--elev:#141E30;--bd:#1A2535;--grn:#39D98A;
--gold:#F5A623;--red:#EF4444;--blue:#3B82F6;--pur:#8B5CF6;--tx:#F1F5F9;--mut:#475569}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:0 0 60px}
a{color:var(--blue)}
header{background:linear-gradient(180deg,#0F1520,#080C14);border-bottom:1px solid var(--bd);padding:18px 24px;position:sticky;top:0;z-index:10}
.htop{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.htop h1{font-size:20px}.htop .sub{color:var(--mut);font-size:12px}
.pills{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.pill{background:var(--elev);border:1px solid var(--bd);border-radius:20px;padding:5px 12px;font-size:12px}
.pill b{color:var(--grn)}
.banner{background:rgba(245,166,35,.15);border:1px solid var(--gold);color:var(--gold);
border-radius:8px;padding:10px 14px;margin-top:12px;font-weight:600}
nav{display:flex;gap:4px;padding:0 24px;border-bottom:1px solid var(--bd);background:var(--card);overflow-x:auto;position:sticky;top:0;z-index:9}
nav button{background:none;border:none;color:var(--mut);padding:14px 16px;cursor:pointer;font-size:13px;font-weight:600;border-bottom:2px solid transparent;white-space:nowrap}
nav button.active{color:var(--tx);border-bottom-color:var(--grn)}
.tab{display:none;padding:24px;max-width:1200px;margin:0 auto}.tab.active{display:block}
.grid{display:grid;gap:14px}.g2{grid-template-columns:1fr 1fr}.g4{grid-template-columns:repeat(4,1fr)}
@media(max-width:800px){.g2,.g4{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px}
.card h3{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);margin-bottom:12px}
.bigGrade{font-size:64px;font-weight:800;line-height:1}
.headline{font-size:18px;font-weight:600;margin:8px 0}
.callout{background:var(--elev);border-left:3px solid var(--gold);padding:12px 14px;border-radius:6px;margin:8px 0}
.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
.b-grn{background:rgba(57,217,138,.15);color:var(--grn)}.b-red{background:rgba(239,68,68,.15);color:var(--red)}
.b-blue{background:rgba(59,130,246,.15);color:var(--blue)}.b-gold{background:rgba(245,166,35,.15);color:var(--gold)}
.b-pur{background:rgba(139,92,246,.15);color:var(--pur)}.b-mut{background:var(--elev);color:var(--mut)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--bd);font-size:13px}
th{color:var(--mut);font-size:11px;text-transform:uppercase}
tr.me{background:rgba(57,217,138,.08)}
.pcard{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px;display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px}
.pcard .nm{font-weight:600}.pcard .meta{color:var(--mut);font-size:12px}
.ktc{font-size:20px;font-weight:800}
.up{color:var(--grn)}.dn{color:var(--red)}.flat{color:var(--mut)}
.muted{color:var(--mut)}.warn{color:var(--gold)}
select,button.act{background:var(--elev);color:var(--tx);border:1px solid var(--bd);border-radius:8px;padding:10px;font-size:14px}
button.act{cursor:pointer;font-weight:600;background:var(--grn);color:#04140c;border:none}
.bar{height:8px;border-radius:4px;background:var(--elev);overflow:hidden;display:flex}
.bar i{display:block;height:100%}
.sec{margin-bottom:22px}
.col2{columns:2;gap:20px}@media(max-width:800px){.col2{columns:1}}
ul.clean{list-style:none}ul.clean li{padding:4px 0;border-bottom:1px solid var(--bd)}
"""


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _grade_cls(g: str) -> str:
    g = (g or "").upper()[:1]
    return {"A": "b-grn", "B": "b-blue", "C": "b-gold", "D": "b-red", "F": "b-red"}.get(g, "b-mut")


def _status_cls(status: str) -> str:
    return {"Contender": "b-grn", "Bubble": "b-gold", "Long shot": "b-mut"}.get(status, "b-mut")


def _tier_cls(tier: str) -> str:
    return {"Elite": "b-pur", "Starter": "b-grn", "Depth": "b-blue", "Stash": "b-gold", "Cut": "b-red"}.get(tier, "b-mut")


def _trend(t: int) -> str:
    if t > 5:
        return f'<span class="up">▲ {t}</span>'
    if t < -5:
        return f'<span class="dn">▼ {abs(t)}</span>'
    return '<span class="flat">— </span>'


def _bsc(window: str) -> tuple[str, str]:
    return {"Rising": ("BUY", "b-blue"), "Rookie": ("BUY", "b-blue"),
            "Declining": ("SELL", "b-red"), "Veteran": ("HOLD", "b-mut")}.get(window, ("HOLD", "b-mut"))


# ---------------------------------------------------------------------------
def render_dashboard(data, metrics, my_enriched, opponent_profiles, sell_candidates, buys,
                     pick_portfolio, pick_total, war_room, fa_analysis, ai, rankings,
                     contention=None, fa_summary="") -> str:
    # Draft state comes from Sleeper, not a hardcoded date.
    draft = data.draft
    draft_done = draft.get("status") == "complete"
    start_ms = draft.get("start_time") or 0
    days_to_draft = int((start_ms / 1000 - datetime.datetime.now().timestamp()) // 86400) if start_ms else -1
    draft_soon = (not draft_done) and 0 <= days_to_draft <= 7
    r = data.my_roster
    league_name = data.league.get("name", "Dynasty League")
    # Season state: labels flip from draft/preseason framing once the season is live.
    season_live = data.league.get("status") == "in_season"
    has_results = any(x.fpts for x in data.all_rosters)
    week = (data.league.get("settings") or {}).get("leg") or 1
    rank_by_total = sorted(opponent_profiles + [_me_profile(metrics)], key=lambda p: -p.total_value)
    my_rank = next((i + 1 for i, p in enumerate(rank_by_total) if getattr(p, "is_me", False)), "?")

    parts = ["<!doctype html><html><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             f"<title>{_esc(r.team_name)} — Dynasty War Room</title><style>{CSS}</style></head><body>"]

    # Header
    parts.append("<header><div class='htop'>")
    parts.append(f"<h1>{_esc(r.team_name)}</h1>")
    parts.append(f"<span class='sub'>{_esc(league_name)} · {_esc(r.display_name)}</span></div>")
    parts.append("<div class='pills'>")
    _rec_note = f"Week {week}" if season_live else "preseason"
    parts.append(f"<span class='pill'>Record <b>{r.wins}-{r.losses}</b> <span class='muted'>({_rec_note})</span></span>")
    contention = contention or {}
    me = contention.get(data.user_id)
    parts.append(f"<span class='pill'>Roster Value (FC) <b>{metrics['total_value']:,}</b></span>")
    parts.append(f"<span class='pill'>Value Rank <b>#{my_rank}/12</b></span>")
    if me:
        parts.append(f"<span class='pill'>Title Odds <span class='badge {_status_cls(me.status)}'>{me.status}</span></span>")
    parts.append(f"<span class='pill'>Picks <b>{len(pick_portfolio)}</b> (~{pick_total:,} est.)</span>")
    parts.append(f"<span class='pill'>Data {datetime.datetime.now():%b %d %H:%M}</span>")
    if ai.get("_generated_at"):
        parts.append(f"<span class='pill muted'>Analysis {_esc(ai['_generated_at'])}</span>")
    parts.append("</div>")
    if draft_soon:
        parts.append(f"<div class='banner'>⚠ Rookie Draft in {days_to_draft} day(s)</div>")
    if war_room is not None and war_room.draft_type_conflict:
        parts.append(f"<div class='banner'>⚠ Sleeper reports draft type = '{war_room.draft_type_actual}', "
                     "but you expect SNAKE. Round 2/4 slots differ — fix the league setting before the draft.</div>")
    parts.append("</header>")

    # Nav — War Room only exists in the run-up to a draft
    tabs = ["Overview", "Roster", "Trade Finder"] + ([] if draft_done else ["War Room"]) + ["Free Agents", "Standings"]
    parts.append("<nav>")
    for i, t in enumerate(tabs):
        parts.append(f"<button class='{'active' if i == 0 else ''}' onclick=\"showTab({i})\">{t}</button>")
    parts.append("</nav>")

    parts.append(_tab_overview(ai, metrics, opponent_profiles, contention, data.user_id))
    parts.append(_tab_roster(my_enriched))
    parts.append(_tab_trades(ai, sell_candidates, buys, my_enriched))
    if not draft_done:
        parts.append(_tab_warroom(ai, war_room, pick_portfolio, pick_total, days_to_draft, draft_soon))
    parts.append(_tab_fa(fa_analysis, fa_summary))
    parts.append(_tab_standings(ai, opponent_profiles, metrics, r, has_results, week, contention, data.user_id))

    tf_json = json.dumps(ai.get("trade_finder", {}))
    parts.append("<script>")
    parts.append("function showTab(i){document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('active',i==j));"
                 "document.querySelectorAll('nav button').forEach((b,j)=>b.classList.toggle('active',i==j));}")
    parts.append(f"const TF={tf_json};")
    parts.append(_TF_JS)
    parts.append("</script></body></html>")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("".join(parts))
    return str(OUT)


class _me_profile:
    is_me = True
    def __init__(self, metrics):
        self.total_value = metrics["total_value"]


class _me_row:
    """My team as a standings row (record + value), sortable alongside opponents."""
    is_me = True
    team_name = "You"
    def __init__(self, metrics, roster):
        self.total_value = metrics["total_value"]
        self.wins = roster.wins
        self.losses = roster.losses
        self.ties = roster.ties
        self.points_for = roster.fpts


def _computed_strengths(metrics) -> list:
    items = []
    pb = metrics.get("positional_breakdown", {})
    for pos in metrics.get("surplus_positions", []):
        b = pb.get(pos, {})
        items.append({"label": f"{pos} Depth", "detail": f"{b.get('count', '?')} players, avg value {b.get('avg_value', 0):,}"})
    dw = metrics.get("dynasty_window", "")
    if "ontend" in dw:
        items.append({"label": "Contending Window", "detail": "Roster KTC supports competing now — capitalize before the window closes"})
    return items or [{"label": "No clear surplus", "detail": "Balanced roster without a standout position group"}]


def _computed_weaknesses(metrics) -> list:
    items = []
    pb = metrics.get("positional_breakdown", {})
    for pos in metrics.get("weak_positions", []):
        b = pb.get(pos, {})
        items.append({"label": f"{pos} Thin", "detail": f"{b.get('count', '?')} players, avg value {b.get('avg_value', 0):,} — below starter threshold"})
    return items or [{"label": "No major holes", "detail": "Consider trading surplus for picks or younger assets"}]


# ---------------------------------------------------------------------------
STATUS_PLAYBOOK = {
    "Contender": "Win now: hold producing starters, even aging ones, and spend surplus + picks on upgrades.",
    "Bubble": "Push selectively: buy only upgrades that don't cost future value; sell vets that aren't starting.",
    "Long shot": "Build for next year: sell aging producers for youth and picks while they still have value.",
}


def _tab_overview(ai, metrics, opponents, contention, my_owner_id) -> str:
    g = ai.get("roster_grade", {})
    me = contention.get(my_owner_id)
    p = ['<div class="tab active" id="t0">']
    grade = g.get("overall_grade", "—")
    p.append('<div class="grid g2"><div class="card">')
    p.append(f'<div class="bigGrade {_grade_cls(grade)}" style="color:inherit"><span class="badge {_grade_cls(grade)}" '
             f'style="font-size:48px;padding:8px 18px">{_esc(grade)}</span></div>')
    p.append(f'<div class="headline">{_esc(g.get("headline", "Analysis not generated yet."))}</div>')
    if g.get("window_explanation"):
        p.append(f'<p class="muted" style="margin-top:8px">{_esc(g["window_explanation"])}</p>')
    p.append("</div>")
    p.append('<div class="card"><h3>Most Urgent Action</h3>')
    p.append(f'<div class="callout">{_esc(g.get("most_urgent_action", "—"))}</div></div></div>')

    # This season: computed live every build, same yardstick as every other team
    if me:
        basis = (f"#{me.record_rank} by record · #{me.pf_rank} in points for · #{me.value_rank} in roster value"
                 if me.games >= 3 else f"#{me.value_rank} in roster value (records count after Week 3)")
        p.append('<div class="card sec" style="margin-top:14px"><h3>Title Odds — This Season</h3>'
                 f'<span class="badge {_status_cls(me.status)}" style="font-size:18px;padding:4px 12px">{me.status}</span>'
                 f' <span class="muted">#{me.rank} of 12 · {basis}</span>'
                 f'<div class="callout" style="margin-top:10px">{STATUS_PLAYBOOK[me.status]}</div></div>')

    # positional grades
    pg = g.get("positional_grades", {})
    p.append('<div class="card sec" style="margin-top:14px"><h3>Positional Grades</h3><div class="grid g4">')
    for pos in ("QB", "RB", "WR", "TE"):
        b = metrics["positional_breakdown"].get(pos, {})
        gr = pg.get(pos, {}).get("grade", b.get("depth_grade", "—"))
        p.append(f'<div class="card"><div style="font-size:12px;color:var(--mut)">{pos}</div>'
                 f'<span class="badge {_grade_cls(gr)}" style="font-size:22px;padding:4px 12px">{_esc(gr)}</span>'
                 f'<div class="muted" style="margin-top:6px">{b.get("count",0)} players · avg {b.get("avg_value",0):,}</div>'
                 f'<div class="muted" style="font-size:11px;margin-top:4px">{_esc(pg.get(pos,{}).get("commentary",""))}</div></div>')
    p.append("</div></div>")

    # strengths / weaknesses — AI if available, else computed from metrics
    strengths = g.get("strengths") or _computed_strengths(metrics)
    weaknesses = g.get("weaknesses") or _computed_weaknesses(metrics)
    p.append('<div class="grid g2 sec">')
    for items, title in ((strengths, "Strengths"), (weaknesses, "Weaknesses")):
        p.append(f'<div class="card"><h3>{title}</h3>')
        if items:
            for it in items:
                p.append(f'<div style="margin-bottom:8px"><b>{_esc(it.get("label",""))}</b>'
                         f'<div class="muted">{_esc(it.get("detail",""))}</div></div>')
        else:
            p.append('<p class="muted">—</p>')
        p.append("</div>")
    p.append("</div>")

    # league landscape — every team (you included) on the same title-odds yardstick
    p.append('<div class="card sec"><h3>League Landscape</h3><table><tr><th>#</th><th>Team</th>'
             '<th>Title Odds</th><th>Trade Motivation</th><th>Value (FC)</th></tr>')
    rows = [(my_owner_id, "<b>You</b>", "—", metrics["total_value"], True)]
    rows += [(o.owner_id, _esc(o.team_name), o.trade_motivation, o.total_value, False) for o in opponents]
    rows.sort(key=lambda x: contention[x[0]].rank if x[0] in contention else 99)
    for i, (oid, name, motiv, val, is_me) in enumerate(rows, 1):
        st = contention[oid].status if oid in contention else "—"
        p.append(f'<tr class="{"me" if is_me else ""}"><td>{i}</td><td>{name}</td>'
                 f'<td><span class="badge {_status_cls(st)}">{st}</span></td>'
                 f'<td class="muted">{motiv}</td><td>{val:,}</td></tr>')
    p.append("</table></div></div>")
    return "".join(p)


def _tab_roster(my_enriched) -> str:
    p = ['<div class="tab" id="t1">']
    groups = [("Starters", "starter"), ("Bench", "bench"), ("Taxi", "taxi"), ("IR", "ir")]
    for title, slot in groups:
        players = sorted([x for x in my_enriched if x.slot_type == slot], key=lambda z: -z.market_value)
        if not players:
            continue
        p.append(f'<div class="card sec"><h3>{title} ({len(players)})')
        if slot == "taxi":
            p.append(' <span class="muted" style="text-transform:none">· Rookie year only · Cannot return</span>')
        p.append("</h3>")
        for x in players:
            chip, ccls = _bsc(x.dynasty_window)
            inj = ' <span class="badge b-red">⚠</span>' if x.injury_flag else ""
            src = "FC" if x.fc_value else "KTC"
            p.append('<div class="pcard"><div>'
                     f'<div class="nm">{_esc(x.name)}{inj}</div>'
                     f'<div class="meta">{x.position} · {x.team} · age {x.age} · '
                     f'<span class="badge {ccls}">{chip}</span> '
                     f'<span class="badge {_tier_cls(x.value_tier)}">{x.value_tier}</span> {x.dynasty_window}</div></div>'
                     f'<div style="text-align:right"><div class="ktc {_grade_cls("A" if x.market_value>3000 else "C")}" '
                     f'style="color:inherit">{x.market_value:,} <span class="muted" style="font-size:11px">{src}</span></div>'
                     f'<div class="meta">{_trend(x.trend_7day)} '
                     f'{x.position}{x.position_rank or ""} · KTC {x.ktc_value:,}</div></div></div>')
            if x.injury_history:
                p.append(f'<div class="muted" style="margin:-4px 0 8px 4px;font-size:12px">⚠ {_esc(x.injury_history)}</div>')
        p.append("</div>")
    return "".join(p) + "</div>"


def _tab_trades(ai, sell_candidates, buys, my_enriched) -> str:
    ts = ai.get("trade_strategy", {})
    p = ['<div class="tab" id="t2">']

    # Interactive (pre-computed) finder
    p.append('<div class="card sec"><h3>Trade Finder — pick a player to move</h3>')
    p.append('<select id="tfSel" onchange="renderTF()"><option value="">— select —</option>')
    for x in sorted(my_enriched, key=lambda z: -z.market_value):
        if x.market_value >= 800 and x.slot_type in ("starter", "bench", "taxi"):
            p.append(f'<option value="{x.player_id}">{_esc(x.name)} ({x.position}, {x.market_value:,})</option>')
    p.append('</select><div id="tfOut" style="margin-top:14px"></div></div>')

    # AI strategy
    if ts:
        p.append('<div class="card sec"><h3>Trade Strategy</h3>')
        if ts.get("trade_priority_order"):
            p.append("<ol>" + "".join(f"<li>{_esc(s)}</li>" for s in ts["trade_priority_order"]) + "</ol>")
        p.append("</div>")
        p.append('<div class="grid g2 sec">')
        p.append('<div class="card"><h3>Sell Now</h3>')
        for s in ts.get("sell_now", []):
            p.append(f'<div style="margin-bottom:10px"><b>{_esc(s.get("player"))}</b> '
                     f'<span class="badge b-gold">{_esc(s.get("urgency"))}</span>'
                     f'<div class="muted">{_esc(s.get("reason"))}</div>'
                     f'<div style="font-size:12px">Ask: {_esc(s.get("ask"))}</div></div>')
        p.append("</div><div class='card'><h3>Buy Now</h3>")
        for b in ts.get("buy_now", []):
            p.append(f'<div style="margin-bottom:10px"><b>{_esc(b.get("player"))}</b> '
                     f'<span class="muted">from {_esc(b.get("from_team"))}</span>'
                     f'<div class="muted">{_esc(b.get("reason"))}</div>'
                     f'<div style="font-size:12px">Offer: {_esc(b.get("offer"))}</div></div>')
        p.append("</div></div>")
        if ts.get("do_not_trade"):
            p.append('<div class="card sec"><h3>Do Not Trade</h3><ul class="clean">'
                     + "".join(f"<li>{_esc(x)}</li>" for x in ts["do_not_trade"]) + "</ul></div>")
        if ts.get("pick_strategy"):
            p.append(f'<div class="card sec"><h3>Pick Strategy</h3><p class="muted">{_esc(ts["pick_strategy"])}</p></div>')
    else:
        p.append('<div class="card"><p class="muted">AI trade strategy unavailable — run without --dry-run.</p></div>')

    # computed sell candidates fallback table
    p.append('<div class="card sec"><h3>Computed Sell Candidates</h3>')
    for c in sell_candidates[:8]:
        tgts = " · ".join(f"{t.team_name}→{t.their_player} ({t.ktc_balance:+d})" for t in c.targets[:2]) or "no balanced match"
        p.append(f'<div style="margin-bottom:8px"><b>{_esc(c.player.name)}</b> ({c.player.market_value:,}) '
                 f'<span class="badge b-gold">{_esc(c.urgency)}</span>'
                 f'<div class="muted">{_esc(c.reasons[0])}</div>'
                 f'<div style="font-size:12px" class="muted">Targets: {_esc(tgts)}</div></div>')
    p.append("</div>")
    return "".join(p) + "</div>"


def _tab_warroom(ai, war_room, pick_portfolio, pick_total, days_to_draft, draft_soon) -> str:
    ps = ai.get("pick_strategy", {})
    p = ['<div class="tab" id="t3">']
    p.append(f'<div class="card sec"><h3>2026 Rookie Draft · Pick 1.09 · {_esc(war_room.draft_type_actual.title())}</h3>')
    if draft_soon:
        p.append(f'<div class="banner">⚠ Draft in {days_to_draft} day(s)</div>')
    if ps.get("draft_day_strategy"):
        p.append(f'<p style="margin-top:8px">{_esc(ps["draft_day_strategy"])}</p>')
    p.append("</div>")

    # top pick recommendation
    tp = ps.get("top_pick_at_109")
    if tp:
        p.append('<div class="card sec" style="border-color:var(--pur)"><h3>Top Recommendation at 1.09</h3>'
                 f'<div class="headline">{_esc(tp.get("name"))} <span class="badge b-pur">{_esc(tp.get("position"))}</span></div>'
                 f'<p class="muted">{_esc(tp.get("reason"))}</p></div>')

    # big board — ranked by FantasyCalc (fallback KTC)
    has_fc = any(rk.fc_value for rk in war_room.big_board)
    valhdr = "FC" if has_fc else "KTC"
    p.append(f'<div class="card sec"><h3>Big Board (top 15) · ranked by {valhdr}</h3><table>'
             f'<tr><th>#</th><th>Player</th><th>Pos</th><th>{valhdr}</th><th>KTC</th><th>@1.09</th><th>Fit</th></tr>')
    first = war_room.my_picks[0] if war_room.my_picks else "1.09"
    for rk in war_room.big_board[:15]:
        avail = rk.available_at.get(first, "—")
        acls = "b-red" if avail == "Likely Gone" else "b-grn" if avail == "Target" else "b-gold"
        fit = {"fills": ("Fills Need", "b-grn"), "depth": ("Depth", "b-gold")}.get(rk.need_flag, ("BPA", "b-mut"))
        valcell = f"{rk.fc_value:,}" if rk.fc_value else f'<span class="muted">~{rk.ktc_value:,}</span>'
        p.append(f'<tr><td>{rk.rookie_rank}</td><td>{_esc(rk.name)}</td><td>{rk.position}</td>'
                 f'<td>{valcell}</td><td class="muted">{rk.ktc_value:,}</td>'
                 f'<td><span class="badge {acls}">{avail}</span></td>'
                 f'<td><span class="badge {fit[1]}">{fit[0]}</span></td></tr>')
    p.append("</table></div>")

    # round targets
    p.append('<div class="card sec"><h3>Targets by Pick</h3>')
    for lbl, tgts in war_room.targets_by_pick.items():
        names = ", ".join(f"{t.name} ({t.position})" for t in tgts) or "—"
        p.append(f'<div style="margin-bottom:6px"><b>{lbl}</b>: <span class="muted">{_esc(names)}</span></div>')
    p.append("</div>")

    # pick portfolio
    p.append(_portfolio_grid(pick_portfolio, pick_total))
    return "".join(p) + "</div>"


def _portfolio_grid(pick_portfolio, pick_total) -> str:
    p = [f'<div class="card sec"><h3>Pick Portfolio (~{pick_total:,} FC est.)</h3><div class="grid g4">']
    for pk in pick_portfolio:
        flagcls = ("b-red" if "DO NOT" in pk.flag else "b-blue" if "Acquired" in pk.flag
                   else "b-pur" if "Protected" in pk.flag else "b-grn" if pk.flag else "b-mut")
        p.append(f'<div class="card"><div style="font-weight:600">{_esc(pk.label)}</div>'
                 f'<div class="muted">~{pk.est_value:,}</div>'
                 + (f'<span class="badge {flagcls}" style="margin-top:6px;font-size:10px">{_esc(pk.flag)}</span>' if pk.flag else "")
                 + "</div>")
    p.append("</div></div>")
    return "".join(p)


def _tab_fa(fa_analysis, fa_summary) -> str:
    p = ['<div class="tab" id="t4">']
    if fa_summary:
        p.append(f'<div class="card sec"><h3>Waiver Strategy</h3><p>{_esc(fa_summary)}</p>'
                 '<p class="muted" style="font-size:12px;margin-top:6px">Scored on roster need (starters and '
                 'depth vs the league, plus aging), current value, and future upside — weighted by your title odds.</p></div>')
    groups = [("Add Immediately", "Immediate Add"), ("Watchlist", "Watchlist"), ("Deep Stash", "Deep Stash")]
    for title, pri in groups:
        players = [f for f in fa_analysis if f.add_priority == pri]
        if not players:
            continue
        p.append(f'<div class="card sec"><h3>{title} ({len(players)})</h3>')
        for f in players:
            need = ' <span class="badge b-grn">Fills Need ✦</span>' if f.fills_need else ""
            rk = ' <span class="badge b-blue">Rookie</span>' if f.player.is_rookie else ""
            p.append('<div class="pcard"><div>'
                     f'<div class="nm">{_esc(f.player.name)}{need}{rk}</div>'
                     f'<div class="meta">{f.player.position} · {f.player.team} · age {f.player.age} · {f.player.dynasty_window}</div>'
                     f'<div class="meta">{_esc(f.reason)}</div></div>'
                     f'<div class="ktc">{f.player.market_value:,}</div></div>')
        p.append("</div>")
    if not fa_analysis:
        p.append('<div class="card"><p class="muted">No free agents would improve your roster right now.</p></div>')
    return "".join(p) + "</div>"


def _tab_standings(ai, opponents, metrics, my_roster, has_results, week, contention, my_owner_id) -> str:
    ol = ai.get("outlook", {})
    p = ['<div class="tab" id="t5">']
    if has_results:
        title = "Standings"
        note = f"Through Week {week}. Ranked by record, then points for."
    else:
        title = "Power Rankings"
        note = "Season just underway — no results yet. Ranked by roster value (FantasyCalc)."
    p.append(f'<div class="card sec"><h3>{title}</h3>'
             f'<p class="muted" style="margin-bottom:10px">{note}</p>'
             '<table><tr><th>#</th><th>Team</th><th>Record</th><th>PF</th>'
             '<th>Roster Value (FC)</th><th>Title Odds</th></tr>')
    # In-season: rank by (wins, points for). Early: by roster value.
    key = (lambda o: (-o.wins, -o.points_for)) if has_results else (lambda o: -o.total_value)
    me = _me_row(metrics, my_roster)
    rows = sorted(list(opponents) + [me], key=key)
    for i, o in enumerate(rows, 1):
        is_me = getattr(o, "is_me", False)
        c = contention.get(my_owner_id if is_me else o.owner_id)
        st = c.status if c else "—"
        name = "<b>You</b>" if is_me else _esc(o.team_name)
        rec = f"{o.wins}-{o.losses}" + (f"-{o.ties}" if getattr(o, "ties", 0) else "")
        p.append(f'<tr class="{"me" if is_me else ""}"><td>{i}</td><td>{name}</td>'
                 f'<td>{rec}</td><td>{o.points_for:.1f}</td><td>{o.total_value:,}</td>'
                 f'<td><span class="badge {_status_cls(st)}">{st}</span></td></tr>')
    p.append("</table></div>")

    if ol:
        p.append('<div class="grid g2 sec"><div class="card"><h3>Outlook</h3>')
        p.append(f'<div class="headline">{_esc(ol.get("projected_power_rank",""))}</div>')
        p.append(f'<span class="badge b-blue">{_esc(ol.get("three_year_outlook",""))}</span>')
        p.append(f'<p class="muted" style="margin-top:8px">{_esc(ol.get("season_narrative",""))}</p></div>')
        p.append('<div class="card"><h3>Teams to Watch</h3>')
        for t in ol.get("teams_to_watch", []):
            p.append(f'<div style="margin-bottom:6px"><b>{_esc(t.get("team"))}</b> '
                     f'<span class="muted">{_esc(t.get("why"))}</span></div>')
        p.append("</div></div>")
        p.append('<div class="grid g2 sec">')
        for key, title in (("key_risks", "Key Risks"), ("key_upside_factors", "Key Upside")):
            p.append(f'<div class="card"><h3>{title}</h3><ul class="clean">'
                     + "".join(f"<li>{_esc(x)}</li>" for x in ol.get(key, [])) + "</ul></div>")
        p.append("</div>")
    return "".join(p) + "</div>"


_TF_JS = """
function renderTF(){
  const id=document.getElementById('tfSel').value;
  const out=document.getElementById('tfOut');
  if(!id){out.innerHTML='';return;}
  const d=TF[id];
  if(!d||Object.keys(d).length===0){out.innerHTML='<p class="muted">No pre-computed analysis (run without --dry-run).</p>';return;}
  let h='<div class="card"><h3>Sell Reasoning</h3><p>'+(d.sell_reasoning||'')+'</p></div>';
  (d.top_targets||[]).forEach(t=>{
    const L=x=>Array.isArray(x)?x.join(', '):(x||'');
    const give=L(t.i_give),get=L(t.i_receive);
    const bal=t.ktc_balance||0, fair=t.fairness||'';
    const bcls=fair==='You win'||fair==='Fair'?'b-grn':fair==='Slight overpay'?'b-gold':fair?'b-red':'b-mut';
    h+='<div class="card" style="margin-top:10px"><div style="display:flex;justify-content:space-between">'
      +'<b>#'+(t.rank||'')+' '+(t.target_owner||'')+'</b>'
      +'<span class="badge '+bcls+'">'+(fair||'est.')+' ('+(bal>0?'+':'')+bal+')</span></div>'
      +'<div class="muted" style="margin:6px 0">'+(t.why_they_trade||'')+'</div>'
      +'<div style="font-size:13px">Give: <b>'+give+'</b> &nbsp;→&nbsp; Get: <b>'+get+'</b></div>'
      +'<div class="muted" style="font-size:12px">'+(t.verdict?'Take: '+t.verdict+' · ':'')+'Confidence: '+(t.confidence||'')+'</div></div>';
  });
  if(d.what_to_avoid)h+='<div class="card" style="margin-top:10px"><h3>Avoid</h3><p class="muted">'+d.what_to_avoid+'</p></div>';
  if(d.alternative)h+='<div class="card" style="margin-top:10px"><h3>Hold Case</h3><p class="muted">'+d.alternative+'</p></div>';
  out.innerHTML=h;
}
"""
