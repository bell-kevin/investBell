# investBell

A local, AGPL-licensed investing **research lab** based on strategy slides the user supplied: DCA buys including dips, VA sells during spikes, and capture of long-term index growth. Automatic full exits remain part of the strategy; the simulator implements the user’s clarification about continued trading with same-session DCA reentry. Interactive 3D cubes, daily Yahoo opening prices, an auditable simulation ledger, and scheduled research reports. Trading decisions use no technical indicators, price forecasts, news, or influencer opinions; the research dashboard submits no orders. A separate, opt-in Alpaca paper runner is now available; it cannot connect to a live trading endpoint.

## Status: paused (2026-09-29)

The project is on hold. The home server and the backup machine are powered off, so no daily reports, alerts or backups are running. Everything needed to run the lab again is in this repository.

**Try it without installing:** a saved snapshot of the lab runs on GitHub Pages at <https://bell-kevin.github.io/investBell/>. It holds seven experiments with all 125 settings each, computed by the same engine. GitHub Pages cannot run the Python backtest server, so trying other settings, dates or fresh data needs the local lab below.

**What the research found:** in every test so far the strategy trailed plain buy-and-hold. The home server's own fitted model returned 19.8% in its test period, against 25.0% for buy-and-hold. Over 100 years of ordinary index-fund data, adding VA to DCA did not help ([century backtest](docs/century-backtest.md)). The big returns reported for this style of investing come from 3x leveraged funds during the unusually friendly years since 2009, and real client accounts using it fell 68.6% in 2022 ([track record deep dive](docs/track-record-deep-dive.md)).

## Run

Requires Python 3.11+ with venv support. No Node, JavaScript build, cloud service, or GPU is needed.

```sh
./scripts/setup.sh
./scripts/start.sh
```

Open **http://127.0.0.1:8765**. The interface requests Yahoo data by default. If Yahoo is unavailable, the failure is visible; choose **Synthetic demo** explicitly to try the interface without market data. Install the pinned dependencies for the exchange calendar and data adapter before starting the server.

Drag the cubes to rotate, scroll to zoom, select a cube to inspect its equity path and trades, and read its color against simple DCA at the same budget (blue behind, white within ±1%, orange ahead), or use the accessible scenario table. The Capture slice reveals inner layers. Export the full experiment as JSON or the selected table as CSV.

## Precise scope

All supplied slides, the user’s remembered notes, and explicit clarifications are the strategy’s source of truth; see [Strategy requirements](docs/strategy-requirements.md) and the [slide-by-slide audit](docs/slide-audit.md). The only strategy variables are **DCA, DVA (VA in the slides), and capture**. DCA determines buys, DVA determines partial sells, and capture determines automatic full exits. An exit may occur before trading is finished for the day. Capital and execution costs are accounting assumptions, not additional strategy variables.

The equations are an **experimental interpretation** of those principles, not formulas specified by the slides or a verified recreation of any firm’s proprietary software. DCA uses a fixed daily budget calculated as a percentage of capital when a cycle starts, capped by available cash. VA grows a contribution-adjusted target daily; partial trims leave that target intact. Capture compares portfolio equity with its cycle-start value and schedules a full exit. After that sale executes, its actual net cash becomes the new cycle base, and DCA buys again in the same session when funded. Immediate reentry at the modeled opening is a simulation choice; the user did not prescribe an exact reentry time.

VA and capture signals use an observed opening price and fill at the **following session’s open**, with configurable costs; scheduled DCA does not depend on a price prediction. A **no-loss rule**, on by default, holds any VA trim or full exit whose proceeds after fees would be below the cost of the shares sold; the position stays open and its worst unsold loss is reported. This follows the stated never-sell-at-a-loss policy of a firm that promotes this approach. It leaves losses unsold rather than preventing them. The strategy’s premise of long-term index growth is not a guarantee of returns. Daily-open data limits timing resolution; it does not impose a rule against trading after an exit or define a complete intraday execution system.

Results identify this model as `dca-va-capture-v2`; reports written before 2026-09-29 carry an earlier name for the same rules. Earlier `open-ledger-v1` reports used next-session reentry and need rerunning for comparison under the current rule.

- [Detailed formulas and limitations](static/methodology.html) (also available from **The rules** in the app).
- [Earlier public-source investigation](docs/source-research.md), retained as background subordinate to the slides.
- [100-year DCA vs DCA + VA test and a firm's regulatory filing](docs/century-backtest.md): VA did not beat DCA on 1x index funds; the firm's results come from 3x leveraged ETFs plus capture resets.
- [Real-world track record deep dive](docs/track-record-deep-dive.md): broker reports, regulatory filings, archived site code and social media; client results track a basket of 3x funds, including a 68.6% drop in 2022.
- LAN discovery findings, kept in the private, git-ignored `docs/network-discovery.md`.
- [Spare-computer deployment and daily scheduling](docs/deployment.md).
- [Home server deployment with Podman](docs/server.md).

The lab accepts the unleveraged index ETFs plus six leveraged index funds (SSO, SPXL, UPRO, QLD, TQQQ, UDOW) for research only; paper trading, fitting and daily reports accept only the unleveraged list. It also offers Ken French's daily US market series from 1926 and a simulated 3x version of it, with T-bill interest on idle cash, so leveraged behavior before 2008 can be studied. Yahoo snapshots expire after 24 hours and include provenance/checksums. Each fresh or cached range must contain exactly the expected exchange sessions; missing beginning, middle, or ending observations are rejected. Completed-session cutoffs follow holidays and early closes. Open prices use split-normalized units; dividend distributions use an explicitly documented ex-date cash approximation. Fractional shares and immediate cash reuse are assumed. Taxes, cash interest, actual settlement, and broker constraints are omitted. Results and drawdowns use opening valuations, not intraday extremes.

## Validation and Alpaca paper trading

**Real-money readiness remains unproven.** The later retrospective SPY comparison returned 54.77% for the default strategy versus 68.07% for buy and hold and 62.27% for simple DCA. Historical splits are labeled retrospective because these prices were already explored. See [validation results and frozen future plans](docs/validation.md).

Validation now isolates the layers: **DCA → DCA + VA → DCA + VA + capture**, alongside buy and hold, with identical funding and execution assumptions. Reports include incremental returns, drawdown, exposure, trade counts, and modeled costs. In the saved fitted model's later historical period, these three layers returned 22.5807%, 22.5795%, and 21.6874%, respectively; the VA layer did not improve that sample. See [the fitted comparison](docs/validation.md#comparison-using-the-saved-fitted-parameters).

The [Alpaca paper guide](docs/paper-trading.md) covers the separate persistent runner: actual account reconciliation, durable client order IDs, partial fills, price-bounded orders, the no-loss rule, exposure/loss limits, alerts, and an emergency stop. The dashboard shows its read-only health. Credentials are not configured by the code, and paper execution is not enabled automatically. `.env.example` is a blank template; local `.env.paper` credentials must be private and are excluded from Git/source downloads.

```sh
.venv/bin/python -m investbell.paper init --config deploy/paper-policy.json
.venv/bin/python -m investbell.paper run --interval 15
```

Initialization checks a dedicated empty **paper** account and freezes its policy/code without sending orders. The runner begins on its registered next exchange session. It never connects to a live endpoint. It keeps actual paper fills separate from historical simulation results and pauses on mismatches rather than overwriting them. The default UI and research scheduler do not start this runner.

## Daily research automation

```sh
.venv/bin/python -m investbell.daily --symbol SPY --start 2021-01-04 \
  --model-mode fitted
```

This downloads Yahoo observations, fits or reuses a saved research model, and writes an idempotent JSON report in `data/reports/`. The model fits only DCA/DVA/capture using a bounded grid. Selection uses the older history; a later chronological block is evaluated without selecting on its results. At least 252 training and 126 evaluation sessions are required. Starting cash, slippage, and fees stay fixed across candidates. Selection picks the center of the brightest region rather than a lone peak: the highest average training final equity over each candidate's 3 × 3 × 3 grid neighborhood. Its own final equity, lower drawdown, fewer trades, and then numeric parameter order resolve ties. The model notes when its pick sits on the edge of the grid, and it reports the single best training cell for comparison. These fitting choices are our implementation; the speaker's exact training method is unknown.

Models in `data/models/` retain their parameters, original Yahoo observations, candidate scores, evaluation, timestamps, and data/code hashes. The scheduled workflow reuses the saved model until **two calendar years after fitting**, then fits again on the first successful run at or after that date. A different configuration or strategy/training-code version starts a separate model series. This fits rule parameters; it does not forecast prices. Training and evaluation remain retrospective research, and the daily full-history replay includes training data. It must not be described as prospective performance or as a continuous account carried across model changes.

Use `--model-mode fixed --dca-pct 2 --va-pct 0.1 --capture-pct 10` to reproduce a manual fixed-parameter report. The dashboard's cubes remain manual exploratory backtests; its model panel separately reports saved fitting status. Neither research mode changes the separate paper runner's frozen policy.

The output includes a next-session hypothetical plan. Optional systemd user units are prepared in `deploy/`; they are **not enabled automatically**. The timer runs after the close on weekdays, using New York time. The container scheduler also uses fitted mode by default and checks the retraining due date on each run. Market data determines actual trading sessions.

The deployment target is the former ZimaOS computer, now running Ubuntu Server 26.04.1 LTS without disk encryption so it can reboot unattended. From 2026-09-24 to 2026-09-29 it ran the dashboard and container scheduler as rootless Podman Quadlet units; Docker is not used. It is now powered off while the project is paused. The Dell running Nextcloud is the excluded `bell-server`. See the home server guide and local network-discovery notes.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover paper endpoint isolation, ambiguous submissions, partial fills, restart recovery, cash/position reconciliation, emergency stops, quote deadlines, exchange-session completeness, frozen research plans, cash and position accounting, no-lookahead execution, scheduled accumulation, VA trims, full exits and same-session reentry, distributions and split-normalized data, bounds and malformed requests, cache provenance, HTTP errors, and idempotent report output. A browser smoke script is in `scripts/browser_check.py`; running it additionally requires Playwright and its Chromium browser. Playwright is a development dependency, not required to run the app.

## GitHub Pages snapshot

The Pages site is the same dashboard reading results saved in `site/snapshot/` instead of calling the API. To recompute them from the local data cache, delete that folder and run:

```sh
.venv/bin/python scripts/build_site.py snapshot
```

The experiments and date ranges are listed at the top of `scripts/build_site.py`. To keep the files small, each saved cell keeps 300 points of its equity chart and its latest 200 trades; the metrics and grid are complete. `python3 scripts/build_site.py assemble --out _site` builds the folder that `.github/workflows/pages.yml` publishes on every push to `main`; serve that folder with `python3 -m http.server` to preview it.

## Project layout

| Path | Purpose |
| --- | --- |
| `investbell/engine.py` | Pure opening-price simulation and explicit accounting |
| `investbell/data.py` | Yahoo adapter, deterministic demo, SQLite snapshots |
| `investbell/service.py` | Validated parameter sweeps and result provenance |
| `investbell/server.py` | Local HTTP API and static interface |
| `investbell/daily.py` | Atomic, idempotent daily research reports |
| `investbell/calendar.py` | Exchange sessions, holidays, early closes and completion checks |
| `investbell/training.py` | Yahoo-based fitting of the three variables, immutable models and two-year retraining |
| `investbell/research.py` | Chronological evaluation, execution sensitivities and frozen future plans |
| `investbell/paper.py` | Durable paper account state, risk controls, evidence journal and operator CLI |
| `investbell/broker.py` | Fixed-endpoint Alpaca paper API and explicit market-data feed |
| `investbell/paper_evidence.py` | Read-only prospective paper evidence review |
| `static/` | Dependency-free 3D cube page, ledger, and documentation |
| `scripts/build_site.py`, `site/snapshot/` | GitHub Pages build and the saved results it publishes |
| `deploy/` | Podman Quadlet units for the home server; optional systemd user services and timer for a non-container install |

The server binds to loopback by default. Use an SSH tunnel from another device; this small research server has no user authentication and is not intended for public Internet exposure. No external JS, fonts, tracking, or runtime CDN calls are used. Yahoo supplies historical research data; the optional paper runner uses Alpaca account and market-data endpoints.

## FLOSS and data rights

Project code is licensed under **AGPL-3.0-or-later**, including the custom Canvas 3D renderer. Python, SQLite, yfinance and exchange-calendars are FLOSS components. [yfinance](https://ranaroussi.github.io/yfinance/) is an unofficial Yahoo client intended for research; its software license does not grant new rights to Yahoo data. Downloaded market data, any firm's proprietary software, and the user's presentation photos are not relicensed or redistributed by this project. The GitHub Pages snapshot publishes computed backtest results, including simulated fill prices, but not the downloaded price files.

The source is available in this directory and through **AGPLv3 · Download source** in the app footer. That ZIP includes the application, license, setup scripts, service examples, and tests; it excludes local market data, reports, environment files, and the private network inventory. The repository is published at <https://github.com/bell-kevin/investBell>.
