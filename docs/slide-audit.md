# Slide and notes coverage audit

This audit covers all six attachments and the user's latest clarification that the fundamentals are only **DCA, DVA, and capture**, with a Yahoo-based model retrained approximately every two years. DVA and the slides' VA name the same variable; the compatible code field is `va_pct`. [Strategy requirements](strategy-requirements.md) states the controlling requirements and separates them from implementation choices.

The core strategy and local research pipeline are implemented. A fitted parameter model and two-calendar-year recalibration extend the earlier fixed-parameter backtests. From 2026-09-24 to 2026-09-29 the home server ran the research dashboard and daily scheduler; it is now powered off while the project is paused. An operational broker connection remains outstanding, so the complete investing operation shown in the Result slide is not running here.

## Attachment coverage

| Attachment | Requested behavior or source claim | Coverage and limit |
| --- | --- | --- |
| `1.jpeg`: ignore market noise | Rules do not depend on news or opinions. | Implemented: the engine uses the accounting ledger and opening observations, with DCA, DVA, and capture controls. No news, influencer, indicator, or price-forecast inputs. |
| `1.jpeg`: automate investing | Run the data and decision workflow automatically. | Research scheduling ran on the home server from 2026-09-24 to 2026-09-29: a fitted report every weekday after the close, with no orders. The server is now powered off. A separate opt-in paper runner is implemented, but the broker is unconfigured and the runner is not running. There is no live-trading endpoint or claim of fully automated real investing. |
| `1.jpeg`: seven years, account count, assets, profit, annualized return | Speaker's reported business and performance results. | Source claims only. They cannot be reproduced by implementing a feature and are not claimed as investBell results or guaranteed outcomes. |
| `2.jpeg`: Yahoo → Python → queue/workers → database → JSON → website | Historical data acquisition, parameter calculation, persistence, and visualization. | Implemented locally with Yahoo/yfinance, Python, SQLite, JSON, and the website. A synchronous bounded loop processes combinations; there is no distributed worker queue or AWS dependency. |
| `3.jpeg`: DCA buys including dips | Buy on the schedule with available cash through rises and declines. | Implemented. The percentage budget is fixed within each cycle and resets after a full exit. No dip indicator or market-top prediction is used. |
| `3.jpeg`: VA sells during spikes | Partial sales based on the position exceeding its target. | Implemented through the documented DVA/VA target recurrence. The speaker's exact recurrence and "position average" formula were not disclosed. |
| `3.jpeg`: capture long-term growth | Retain both cash and remaining position value in the outcome; support the user-confirmed full exits. | Implemented with a documented portfolio-growth threshold and cycle reset. Capture counts completed exits; it does not mean realized profit or guaranteed growth. |
| `4.jpeg`: no technical indicators, influencers, news, or assumed expert knowledge | Keep these inputs out of investment decisions. | Implemented. Historical calibration selects among the three explicit rule variables without introducing these inputs. |
| `4.jpeg`: indexes grow over time, figure out how to capture it | Keep the strategy focused on index exposure. | Implemented through the supported index-ETF universe. The premise is not coded as guaranteed recovery or positive returns. |
| `5.jpeg`: strategy diagram | Same three-part diagram as `3.jpeg`. | Covered by the same DCA, DVA, and capture implementation; it adds no separately specified equation. |
| `6.png`: notes | Additional behavior remembered by the user. | Audited individually below. |

## Notes and latest clarifications

| Note | Interpretation and implementation |
| --- | --- |
| "Technical indicators are bad" | Exclude technical indicators from this strategy's decisions. The software need not establish a universal claim about their usefulness. |
| "Price feedback loop" | Opening prices revalue held shares, changing the ledger's relation to the DVA target and capture threshold. This is a response to observed state, not a forecast. |
| "Don't do penny stocks" | Only the configured index ETFs are accepted. The accompanying $25,000–$40,000 annual-income assertion is unverified and is not reproduced as a software fact. |
| "3d visualization with 3 variables" | Interactive 3D combinations of DCA, DVA/VA, and capture, with measured return coloring and inspectable scenario results. Funding and execution settings do not become additional model axes. |
| "DCA to buy" and "Dollar cost averaging" | DCA determines purchase sizing, capped by available cash. DVA does not supply a second buy-sizing rule. |
| "3 variables are DCA, VA, and CAPTURE" | Exactly those three variables form the search space. DVA is an alias for VA, not a fourth variable. |
| "Based on opening price" | Yahoo historical opening prices are the observations. Sell decisions from an observed open fill at the following modeled open to avoid assuming a decision can receive an already observed price. Broker paper orders instead use quotes after the open. |
| Percentage-based morning purchase after getting out yesterday | New-cycle DCA uses actual cash left after the exit. The suggested 2% or 3% are configurable examples. This example is compatible with the earlier explicit clarification allowing continued trading in the exit session. |
| "When you get out, it all turns back into cash" | A capture sale closes the entire position. Only after a successful full exit may DCA reinvest a portion of actual proceeds; a later portfolio observation can therefore show a new holding. |
| "Don't use price to predict price" | No price forecast. Historical prices evaluate parameter choices and value holdings; execution rules respond to observed openings and the ledger. |
| "Use fundamentals to predict price" | The user's latest clarification defines those fundamentals as DCA, DVA, and capture only. No earnings, financial-ratio, macro, or other fundamentals predictor is added. |
| "Metatrader 5 is not legal for USA residents" | Unverified legal assertion, not an application requirement or a factual claim made by investBell. The implementation does not use MetaTrader. |
| Model built from Yahoo data | Fit a model by selecting DCA, DVA, and capture parameters on a recorded Yahoo training period, preserving the data identity and method. This goes beyond displaying grid backtests without selecting parameters. |
| Retrained every two years or so | Record and enforce a two-calendar-year recalibration date. Daily Yahoo refresh/report generation is distinct from model fitting. The precise cadence and training window are project conventions because the speaker's details are unavailable. |

## Model fitting and evidence

[The fitting workflow](../investbell/training.py) uses the earlier approximately 80% of requested Yahoo history for training, reserving at least 126 later observations for evaluation and requiring at least 252 training observations. It ranks combinations by training final equity, then lower drawdown, fewer trades, and ascending DCA/DVA/capture values. Only the selected triple is evaluated on the later partition. Each partition begins with the same initial cash and no carried state.

The artifact preserves observations, source/configuration/code identities, all candidate training metrics, selected parameters, later evaluation, and recalibration timing. Versioned JSON artifacts live under the configured model directory. The default next fitting date is two calendar years after fitting, with February 28 used for a February 29 anniversary when necessary. A model that has observed a later historical cutoff cannot be used for an earlier experiment; that earlier experiment needs a separate model directory.

The later chronological evaluation remains retrospective, using previously explored history. It is not actual prospective broker evidence or proof of future profitability. The speaker's exact formulas, selection objective, training window, and trained parameter values were not supplied. investBell implements documented choices rather than claiming an exact reconstruction.

The fitted model feeds the daily **research** workflow. It does not edit an existing paper ledger, change a frozen policy, or restart a paper experiment. Paper execution and its prospective evidence remain separate from historical fitting and research reports.

## Local substitutes for the architecture slide

| Slide component | Local implementation |
| --- | --- |
| Yahoo Finance | `investbell/data.py`: Yahoo observations and validated cached snapshots. Yahoo remains an external data source. |
| Python Lambda fetching data | Local Python application and daily research command. |
| SQS and parallel Lambda workers | Bounded synchronous parameter evaluation in Python; no distributed queue, worker fan-out, or cloud scaling is claimed. |
| DynamoDB | SQLite market snapshots and local versioned JSON research/model artifacts. |
| Python export to minified JSON | Python API responses and JSON exports. |
| Visualization website | Locally served browser application with interactive three-axis results, scenario inspection, equity history, and trade details. |
| Unattended execution | Prepared systemd timer and container scheduler; installation and operation on the confirmed host remain incomplete. |

## Evidence and remaining limits

The reference strategy is in [engine.py](../investbell/engine.py); its bounded three-axis experiments and request validation are in [service.py](../investbell/service.py). [The strategy tests](../tests/test_engine.py) cover funded dip purchases, DVA trims, full liquidation, net-cash reentry, costs, and separation of prior signals from future opening prices. [Research execution](../investbell/research_execution.py) adds cash-availability sensitivities. [The paper runner](../investbell/paper.py) reconciles actual fills before reentry.

Paper risk and execution controls can stop a purchase even when the three strategy rules would schedule one. Position caps, loss limits, stale quotes, and unreconciled cash are separately identified execution constraints; they are not extra fundamentals or technical signals. Historical daily opens also cannot identify all intraday sale opportunities or reproduce arbitrary intraday execution.

See [deployment](deployment.md), [home server deployment](server.md), and [paper execution](paper-trading.md) for the remaining operational setup. Prepared code and successful local tests must not be described as an installed, funded, unattended investing operation.

Local verification on September 23, 2026: all 174 automated tests and the desktop/mobile browser checks passed. The first retained Yahoo SPY fit used 1,436 completed sessions through September 22, 2026: 1,149 for training and 287 for later evaluation. Its model digest begins `c227a805b656`; the next retraining date is September 23, 2028. A repeated daily run reused the same model and report. The running local dashboard displays that saved model. No broker orders were submitted by these research runs.
