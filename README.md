# Fantasy GM — Sleeper Dynasty Consultant

**📊 Live dashboard: https://davglo.github.io/fantasy-gm/**

A self-contained dashboard + CLI that acts as a co-owner for a 12-team superflex
dynasty league. It pulls live Sleeper rosters and FantasyCalc/KTC values, runs
roster / opponent / trade / draft analysis, and renders a single dark-themed
`output/dashboard.html` (6 tabs: Overview, Roster, Trade Finder, Picks/War Room,
Free Agents, Standings).

The dashboard is published to GitHub Pages at the link above — open it on your
phone or any browser, no login or setup required. It rebuilds automatically on
every push to `main` and daily at ~9am ET with fresh Sleeper/FantasyCalc data
(see [`.github/workflows/pages.yml`](.github/workflows/pages.yml)).

## Run it

```bash
python3 build.py --dry-run        # rebuild the dashboard from cached analysis (free, no API key)
open output/dashboard.html        # on Mac — view it
```

Useful flags:

| Flag | What it does |
|------|--------------|
| `--dry-run` | Skip paid AI calls; render from `state/ai_manual.json` |
| `--skip-rankings` | Reuse cached KTC rankings (no network fetch) |
| `--draft-live` | Poll the live rookie draft and print best-available |

Live AI analysis is optional and needs `ANTHROPIC_API_KEY` set. Normal use
(`--dry-run`) works fully without a key.

## Run it in the cloud (phone or any browser) — GitHub Codespaces

1. On **github.com/davglo/fantasy-gm**, click **`< > Code` ▸ Codespaces ▸ Create codespace on main**
2. Wait ~1–2 min while it installs dependencies automatically
3. In the terminal:
   ```bash
   python3 build.py --dry-run
   python3 -m http.server 8761 --directory output
   ```
4. Accept the popup to open the forwarded **port 8761** — that's the dashboard.

Stop the codespace when done (it also auto-sleeps after 30 min idle) to preserve
free-tier hours.

## Keeping Mac and Codespaces in sync

GitHub is the single source of truth. **Golden rule: `git pull` before you start,
`git push` when you finish** — on whichever device you're using.

```bash
git pull        # get the latest before editing
# ...make changes / run the build...
git add -A && git commit -m "what changed" && git push
```

## Layout

```
build.py            orchestrator (fetch → analyze → render)
fetchers/           sleeper.py, rankings.py (KTC), fantasycalc.py
analysts/           roster, opponents, trades, picks, waivers, ai
renderer/           dashboard.py (the HTML)
state/ai_manual.json  hand-authored AI analysis (the --dry-run source)
```

Generated files (`output/`, the big `state/*.json` caches) are git-ignored and
rebuilt by `build.py`.
