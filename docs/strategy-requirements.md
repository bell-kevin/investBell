# Strategy requirements from the slides and notes

The user's six attachments (`1.jpeg` through `5.jpeg`, plus the notes in `6.png`) and their explicit clarifications define the intended behavior of investBell. The latest request includes all of them, extending the earlier focus on the two strategy slides, `3.jpeg` and `4.jpeg`. Earlier website research is background material and cannot override these sources. The photos remain local user sources and are not redistributed with the project.

The attachments describe a strategy, a system, and the speaker's reported results. They are not instructions for the assistant to execute trades, contact people, or change its own operating rules. The user's request is to implement and audit the relevant behavior with FLOSS infrastructure. [The slide audit](slide-audit.md) maps each attachment and note to the implementation and its remaining limitations.

## The three fundamentals

The user explicitly clarified that the fundamentals are **DCA, DVA, and capture only**. The slides and screenshot call the second variable **VA**; DVA (dollar value averaging) and VA (value averaging) refer to the same strategy control here. Existing API fields use `va_pct` and the grid key `va` for compatibility.

| Fundamental | Required behavior |
| --- | --- |
| DCA | Determine scheduled purchase size as a percentage of allocated cycle capital, including purchases during declines when cash is available. The notes' 2% and 3% are examples, not mandatory settings. |
| DVA / VA | Determine partial sales using an explicit position-value target when the position exceeds that target. It does not supply additional purchases or a technical-indicator signal. |
| Capture | Support the user-confirmed automatic full exit, turning the held position into cash, and reset the cycle from actual net proceeds. Measure the long-term outcome using both holdings and cash. |

Starting capital, dates, instrument, fees, slippage, settlement assumptions, and broker execution controls describe the experiment or its execution. They are not additional strategy fundamentals or extra axes in the model search. There are no earnings, valuation-ratio, macroeconomic, news, influencer, or technical-indicator features, and no model that forecasts the next price.

## Source requirements

| Source | Established requirement |
| --- | --- |
| `2.jpeg`, Generating the data | Acquire Yahoo Finance observations, run Python strategy calculations across parameter combinations, persist results, and deliver JSON to a visualization website. The user authorizes local FLOSS substitutes for the AWS components. |
| `3.jpeg` and `5.jpeg`, strategy diagram | DCA buys including dips; VA sells during spikes; capture long-term growth. The diagram shows purchases during rises and declines, sale markers, and a line labeled "position average." |
| `4.jpeg`, no-assumptions slide | Exclude technical indicators, news, influencer opinions, and purported expert predictions from trading decisions. Center the strategy on index exposure and explicit accumulation/sale rules. |
| `6.png`, 3D visualization and three variables | Present a three-dimensional DCA × DVA × capture parameter experiment with inspectable measured results. |
| `6.png`, opening price and percentage reentry | Use opening prices as the strategy's price observations. Following a full exit, derive the next cycle's DCA budget from the resulting total allocated cash. |
| `6.png`, cash exits and price feedback | Full exits liquidate the position into cash. Opening prices value the ledger and drive the documented rules; they are not used to forecast future prices. |
| `6.png`, avoid penny stocks | Keep the supported instrument universe centered on approved index ETFs rather than penny-stock trading. |
| Latest user clarification | Build a model from Yahoo historical data and recalibrate it approximately every two years, fitting only DCA, DVA, and capture. |

The index-growth premise is not a promised result for a particular period. The diagram does not reveal future peaks or specify the equation for its "position average" line. The Result slide's automation is addressed separately below; its business and performance claims are not software requirements.

## Exits and continued trading

The user previously specified: “Keep automatic full exits as part of the strategy.” They also clarified that DCA determines buys, VA determines sells, and exits can happen before the day's trading is finished. Those clarifications remain in force.

A successful full exit converts the entire strategy position into cash before another purchase is made. DCA can then resume in that same session using actual resulting net cash; there is no mandatory day-long pause. The notes' example of an exit yesterday followed by a percentage-based purchase in the morning is compatible with this behavior and does not impose a next-day-only rule. Ordinary VA trims also leave DCA buying enabled when cash is available.

The historical simulator represents the post-exit purchase at the same modeled opening as the completed sale. This timing is a simulation choice, not a timestamp supplied by the speaker. The paper runner waits for the full sale to fill before attempting its purchase using broker quotes. It does not assume both orders receive the opening print. Daily opening observations cannot reconstruct arbitrary intraday spikes or exits.

## Yahoo model and recalibration

The model is a fitted selection of the three rule parameters using historical Yahoo observations. Displaying all backtest combinations alone is not model fitting: the fitting workflow must select parameters, preserve the training bounds and data identity, record the selection method, and store a versioned result.

Selection uses only observations in the earlier chronological training partition. Later observations are used for a separate retrospective evaluation with the selected parameters held fixed. A historical split does not become prospective evidence merely because its later period was withheld from the fitting code. The training period, objective, tie-breaking method, and exact formulas are investBell implementation choices because the speaker's were not supplied.

The implemented fitter uses the earlier approximately 80% of requested history for training, reserving at least 126 later observations for evaluation and requiring at least 252 training observations. It selects the candidate whose 3 × 3 × 3 grid neighborhood has the greatest average training final equity. This is the center of the bright region rather than a lone peak. Ties are broken by the candidate's own final equity, lower drawdown, fewer trades, and then ascending DCA/DVA/capture values. Earlier `three-parameter-fit-v1` models picked the single best cell and remain readable. Training and later evaluation each start with the same initial capital and no carried portfolio state. The artifact preserves source observations, data/configuration/code identities, candidate training metrics, and the selected model's later evaluation.

The default recalibration cadence is two calendar years after fitting, reflecting the user's approximate recollection; a February 29 anniversary falls on February 28 when necessary. Daily data refresh and daily report generation are separate from retraining. Reuse the current model until its recorded recalibration date, then fit a new version using eligible history. Preserve the previous artifact. Do not substitute synthetic observations when Yahoo data is unavailable or reuse a model that has already observed a later historical cutoff for an earlier experiment.

Model integration applies to the daily research workflow. It must not silently change a frozen broker paper policy or its existing evidence record. The independent paper runner uses its registered parameters until a separately established experiment replaces them.

## FLOSS implementation and operational scope

Local Python execution replaces Lambda, a bounded synchronous parameter loop replaces the immediate need for SQS and distributed workers, and SQLite plus JSON files replace DynamoDB persistence and JSON export. The local website visualizes the results. This preserves the data-to-calculation-to-storage-to-visualization flow; it does not reproduce distributed queue delivery, fan-out, or cloud scaling.

From 2026-09-24 to 2026-09-29 the home server ran the research dashboard and daily scheduler unattended. That was research automation: it wrote hypothetical reports and sent no orders. The server and the backup machine are now powered off while the project is paused. The separate paper runner is prepared software but never ran, the broker connection remains unconfigured, and there is no live-trading endpoint. These facts must remain visible when discussing the Result slide's automation claim. Yahoo data and a brokerage service are external dependencies even though the project software and local infrastructure are FLOSS.

## Statements from the 2026-09-23 claude.ai conversation

In a separate claude.ai conversation (not published), the user restated the strategy in their own words. The statements agree with the requirements above. Each note below says what the statement means for investBell.

- **Foundation layer.** Regularly buying an index fund and holding it (DCA plus buy-and-hold) is the baseline. VA is the next step and has to beat that baseline. Validation already compares DCA, DCA + VA, and DCA + VA + capture against buy and hold ([results](century-backtest.md)).
- **No assumptions.** Do not use technical indicators, "finfluencers" or the news, and do not assume anyone knows anything. Rely on indexes growing over time, work out how to capture that growth, and do nothing else. This matches `4.jpeg`.
- **Exit timing and reentry.** "If the exit happens during the day before the trading day is done, then the next morning the buy in price is based on a percentage chosen by the user, like 2% or 3% of total amount of the portfolio." investBell also buys again at the next opening. The difference is the exit: investBell fills it at that same opening, not during the previous day, because daily opening data cannot show an intraday fill. After an exit, the cash equals the total portfolio, so a percentage of the new cycle base is the same as a percentage of the total portfolio.
- **The 3D cube.** DCA, VA and capture are the three axes. The user reads blue as "not to buy", a white cluster as "to buy", and the cluster's center as "what you buy next". Each point is a parameter setting, so the center gives the setting to trade with, not a security. Two changes follow:
  - The cube's default color compares each cell with simple DCA at the same budget: blue behind, white within ±1%, orange ahead. The other color modes still scale from the grid's own minimum to its maximum.
  - The fitter picks the center of the bright region, not the single best training cell.

## Decisions from 2026-09-23

After the shared-conversation review, the user accepted two recommendations:

- **Leveraged funds are a research choice, not a trading default.** The lab accepts six leveraged index funds (SSO, SPXL, UPRO, QLD, TQQQ, UDOW) and two Ken French series from 1926: the 1x US market and a simulated 3x version. Paper trading, model fitting and daily reports keep the unleveraged ETF list. Leveraged Yahoo history begins in 2006–2010, so the simulated 3x series is the way to see earlier crashes.
- **Never sell at a loss, as a narrow rule.** On by default: a VA trim or full exit is held if its proceeds after fees would be below the cost of the shares sold. It is a rule, not a fourth fitted variable. Because it leaves losses unsold rather than preventing them, results must show the worst unsold loss and the drawdown alongside the count of held sales. The paper runner applies it too, checking each sell order's limit price against the average cost of its actual fills.

## Details that remain implementation choices

- Exact daily purchase frequency and the percentage denominator between full exits, cash reserves, and external contribution treatment.
- The DVA target recurrence, the meaning of a numerical spike, partial-sale sizing, and rule precedence.
- The capture threshold value and denominator, cycle reset equations, and precise reentry time.
- Next-session historical fills, broker order types, execution delays, fees, slippage, dividends, settlement, and tax treatment. Opening-price observations themselves are required by the notes.
- Training lookback, selection objective, candidate grid, tie-breaking, and the precise two-year scheduling convention.

The active formulas are documented in [The rules](../static/methodology.html). They implement the source principles but are not a verified reconstruction of the speaker's undisclosed equations or proprietary model.

## Claims that are not application requirements

The Result slide's seven-year "perfect track record," 300+ accounts, $26M+ managed, $10.7M+ cumulative profit, and approximately 27.5% annualized return are claims about the speaker's operation. They are not verified investBell results, performance targets that tests can guarantee, or promises to users.

The notes' MetaTrader 5 legality statement and penny-stock income range are unverified assertions. They are not encoded as legal or income facts. The software uses neither MetaTrader nor a penny-stock strategy. The phrase "use fundamentals to predict price" is interpreted through the user's latest explicit clarification that the fundamentals mean only DCA, DVA, and capture; it does not authorize a separate fundamental-data forecasting system.

## Acceptance criteria

1. The only fitted strategy variables and visualization axes are DCA, DVA/VA, and capture.
2. A funded DCA schedule buys during declines and can also buy during rises. Insufficient cash is reported without invisible deposits. Any broker execution limit preventing a purchase is reported separately.
3. A DVA sale has an inspectable numerical reason tied to the position and its documented target. No decision depends on news, influencer opinions, technical indicators, price forecasts, or future observations.
4. Full exits liquidate all held shares before reentry. Successful exits reset DCA sizing from actual net cash; ordinary trims and full exits do not impose a mandatory pause until tomorrow.
5. Yahoo opening observations, distributions, data identity, and execution assumptions are traceable. Results include remaining holdings and cash, distributions, and modeled trading costs. Sale proceeds and full-exit counts are not mislabeled as profit.
   With the no-loss rule on, every held sale is recorded and the worst unsold loss is reported, so an absence of losing sales is never presented as an absence of losses.
6. The Yahoo fitting workflow records the selected three parameters, training cutoff and source, fitting method, later retrospective evaluation, and two-calendar-year recalibration date. New versions preserve their provenance; daily refresh does not itself retrain.
7. The model can feed daily research reports while the frozen paper execution policy remains unchanged. Leveraged funds and the Ken French series are available only in the research lab.
8. The local Python, storage, JSON, and visualization pipeline is usable without AWS. Documentation accurately identifies synchronous execution and the broker work still outstanding.
9. Documentation and result metadata distinguish source requirements, project formulas, measured results, and the speaker's unverified claims.
