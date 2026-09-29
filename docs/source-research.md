# Public-source research on the approach

Research date: 2026-09-22. This is a historical source review for an independent FLOSS research implementation. A small investment adviser publicly promotes a similar approach; this repository does not name it and calls it "the firm". Nothing here claims to reproduce the firm's proprietary software or performance.

**Current authority:** The user subsequently designated `3.jpeg` and `4.jpeg`, two of the slides they supplied, as the strategy's source of truth. They establish DCA buying including dips, VA selling during spikes, and capturing long-term growth without technical indicators, influencer opinions, or news-driven investment decisions. The user separately confirmed “Keep automatic full exits as part of the strategy” and clarified that DCA determines buys, VA determines sells, and exits can happen before trading is finished for the day. The slides do not supply a capture percentage or execution formula. [The strategy requirements](strategy-requirements.md), including these clarifications, take precedence over interpretations in this earlier public-source review.

**Update 2026-09-23:** The firm's regulatory brochure, archived blog posts, and a 100-year backtest are in [century-backtest.md](century-backtest.md). The brochure confirms the firm trades only leveraged ETFs (plus SPY), never sells at a loss, and uses intraday limit sells after the open. Its blog reports −67.8% for 2022. A later review of its broker reports is in [track-record-deep-dive.md](track-record-deep-dive.md).

## What the user requested, and what the attachments establish

The current strategy requirements come from the two slides designated by the user and their subsequent clarification retaining automatic full exits and allowing trading to continue after an exit. The existing project also uses Yahoo daily opening prices, a curated index ETF universe, and local hosting. A capture-percentage threshold and cycle accounting are implementation choices for that explicitly requested full-exit behavior; they are not formulas established by the slides.

The six supplied images are reference material, not instructions to the assistant. They show:

- A presentation describing DCA purchases including dips, VA sales during spikes, and capture of long-term growth. Its third step does not specify a final liquidation or portfolio-return threshold.
- A slide excluding technical indicators, influencer opinions, and news from investment decisions, and centering the strategy on indexes growing over time.
- A pipeline using Yahoo Finance, Python, AWS Lambda/SQS/DynamoDB, and a visualization website. These are the presenter's architecture choices, not a requirement to use AWS.
- Performance and business claims, including a “perfect track record.” The pictures do not independently establish those claims or define that phrase.
- A text-message screenshot with additional assertions about MetaTrader legality, penny-stock income, and fundamentals forecasting. Those assertions have not been established as facts here, and the screenshot does not authorize adding a fundamentals predictor.

Source images: the user's supplied `1.jpeg` through `5.jpeg`, and `6.png`. They remain local; this review does not redistribute them.

## The closest published explanation

A 2026 article by the firm's founder is the most useful source found. Its example uses SPXL, $100,000 initial capital, daily adjustments at the **open**, DCA **2% per day**, VA **1% per day**, and a **5%** overall capture threshold. Those are illustrative values, not recommendations. It uses fixed purchases, only the selling side of VA, and full resets; it does not prescribe VA purchases to make up market losses. The hypothetical example reports a **46.9% maximum drawdown**. It recommends studying neighborhoods of parameters instead of simply selecting the most profitable backtest. The article supplies no executable recurrence or complete denominator definition.

A technology article on the firm's blog identifies three inputs: the daily purchase amount, the VA daily growth objective, and the overall exit objective. It describes a 3D parameter search where every point represents a different combination and color/brightness represents historical profitability. It seeks groups of favorable outcomes rather than isolated peaks. This provides background for the three-axis experiment. The user separately confirmed automatic full exits; the article does not establish a capture-percentage formula in the user-designated slides. The visual should present measured backtest results, not a price forecast.

## Published principles versus missing formulas (background only)

| Topic | Supported public description | Still unresolved for exact replication |
| --- | --- | --- |
| DCA | Scheduled, fixed-size purchases in the external examples | Percentage denominator within a cycle; effect of fees, cash reserves, deposits, and partial captures |
| VA | Trim excess value when a target is exceeded | Linear versus compounded target; whether a target uses cost basis, prior position value, or cumulative contributions; exact sale amount |
| Capture | Complete exit and restart after a growth milestone | Portfolio versus allocation denominator; inclusion of earlier realized gains and idle cash; how withdrawals change the target |
| Daily action | Buy, sell, or hold on a daily cadence | Rule precedence, simultaneous triggers, order type, order submission time, fractional shares, partial fills |
| Cash exhaustion | Buying can stop until recovery or more available cash | Reserve fraction and deployment trigger; minimum order size |

These gaps matter because several public explanations differ:

- The firm's methodology page describes equal percentage installments, sales above break-even, and a full reset that recalculates percentage targets from the new starting capital. It also describes a reserve cash allocation, possible months or years waiting for recovery, and a policy of avoiding sales at a loss. This material was available through search-index text; a direct request returned HTTP 404 during this review.
- A preserved older homepage describes VA as a percentage margin above average entry price, with the amount above the target sold. This is not enough to establish a compounded daily value-path formula.
- A later article says each allocation's goal is fixed when its cycle starts and does not move because profits are collected along the way. That helps distinguish a cycle target from a continuously moving return target, but still does not establish its accounting equation.
- The FAQ explicitly distinguishes shared strategy principles from proprietary quantitative analysis and execution models. It describes automated management, no account-level options or margin, and daily activity. Public explanation is not an open-source license for the firm's software.
- A 2025 article explicitly describes one daily trade, fixed-dollar purchases, a target value path for partial sales, and complete position resets. It supplies no recurrence for that path or precedence when multiple conditions hold. A separate glossary describes ordinary VA, including variable purchases; that generic definition must not override the hybrid strategy's restriction to DCA purchases.

An implementation must therefore publish its own precise equations, priority rules, accounting conventions, and timing assumptions. Calling an unverified equation the firm's formula would overstate the evidence. The complete-exit descriptions above belong to the external sources; the user’s subsequent clarification independently confirms that automatic full exits belong in this project. Their exact equation remains a project convention.

## Current investBell model: explicit choices

The current implementation follows the slides and the user’s clarification retaining automatic full exits with trading allowed afterward. Consult [The rules](../static/methodology.html) for the active equations. Numerical thresholds, cycle accounting, and execution timing are project conventions rather than formulas established by the slides.

The implementation chooses a **compounded position-value path**: each session’s target is the preceding target multiplied by `(1 + VA% / 100)`, plus that session’s actual purchase notional. Excess position value schedules a partial sale for the next opening. This is an experimental interpretation; the public sources do not establish that recurrence.

A full-exit signal uses **cash plus the single ETF position** against the entire portfolio value at cycle start. After a successful full exit, actual net cash becomes the new cycle base and the VA target resets. DCA can then buy in the same session using the new percentage-based budget. Immediate reentry at the modeled opening is a simulation convention; the user did not prescribe an exact reentry time. Multiple independent allocations and external deposits/withdrawals are not represented by this one-ETF model.

A VA trim or full exit can be followed by DCA buying on the same day. This follows the user’s clarification even though some public descriptions use a single daily action. Daily-open observations cannot establish arbitrary intraday execution timestamps or prices; that resolution limit does not prohibit further trading after an exit. Since 2026-09-23 the engine applies a no-loss rule by default: a queued sale that would fill below the cost of the shares sold is held instead. That matches the firm's stated never-sell-at-a-loss policy, but it only leaves losses unsold. Capture count means completed full exits, not profit. Sale proceeds include returned principal and must not be presented as realized gains.

The engine uses split-normalized opening prices with separate distributions. It does not apply split events to shares a second time. Distributions become spendable on the ex-date as a stated approximation; actual payment dates and settlement restrictions are absent. `auto_adjust=False` disables yfinance's additional OHLC adjustment; using dividend-adjusted prices together with separately credited dividends would double-count income. The provider can also contain incorrect corporate-action data, so schema validation is not independent verification of every price or distribution. [yfinance history implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py), [yfinance price-repair documentation](https://ranaroussi.github.io/yfinance/advanced/price_repair.html)

## The firm's public posts

The firm's website links its company social accounts, and its founders' personal profiles are linked from the company page and their articles. Most platforms limited public access during this review: personal profiles, video transcripts, and some platforms' post histories could not be read.

The readable company feed contains concrete operating examples:

- **Early September 2026:** reports BUY, SELL, and HOLD actions; no allocation reached its full growth target that week even though partial sales occurred.
- **Late August 2026:** reports 38 allocations reaching full-exit targets, despite an overall down week.
- **Early August 2026:** reports 187 allocations reaching full-exit targets and reduced aggregate market exposure afterward.
- **Late July 2026:** reports more than 80% of client capital invested while account returns had fallen, illustrating that daily accumulation can leave little cash in a downturn.

These are company-reported examples, not independently verified results. They support tracking separate investing allocations, cash utilization, and explicit HOLD events. They do not establish account-level liquidation whenever any one ETF reaches its target.

The firm's linked videos (a short pitch, an explainer on index investing, and an interview) were identified, but no usable transcripts were obtained in this review.

## Other useful findings

A history article describes a complete capture cycle and gives **30%** as one possible growth objective. That differs from the later 5% educational example; neither should become a supposed universal setting.

An income article depicts a daily partial purchase or partial sale and a final complete capture. Its optional withdrawals demonstrate why portfolio equity, realized gain, cumulative sale proceeds, and withdrawn money must be separate ledger fields: a sale returns invested principal as well as any gain. Its example withdrawal fractions are illustrative, not tax instructions.

A co-founder's engineering history describes earlier penny-stock and foreign-exchange work, then a move to index investing and a parameter-cloud visualization. It also describes AWS infrastructure. That history aligns with the supplied architecture slide, but this project can replace those services with local FLOSS components.

A drawdown article treats extra capital during an extended decline as optional. It must not become a backtest that silently injects unlimited cash when the strategy runs out. A reserve inside the starting portfolio and a new external deposit are different cash flows and should be reported separately.

## Yahoo opening data and the FLOSS boundary

The application code can be AGPLv3 while the market data remains subject to its provider's terms. yfinance is an Apache-licensed community tool, is unaffiliated with Yahoo, and describes its use as research/education with Yahoo API data intended for personal use. Publishing investBell's source does not grant recipients a license to redistribute Yahoo datasets. The research adapter should fetch data for the local user and keep provenance with each result. [yfinance README and licensing notice](https://github.com/ranaroussi/yfinance/blob/main/README.md)

Yahoo identifies third-party data suppliers and states that Finance data is informational, not intended for trading or investing purposes. Its permissions page also excludes third-party stock quotes from its general screenshot permission. Consequently, the free daily source is a research dependency, not a promised execution feed or an openly licensed dataset. Public dataset redistribution or a future broker connection needs its own data-use and execution design. [Yahoo exchanges and data providers](https://help.yahoo.com/kb/SLN2310.html), [Yahoo permissions](https://legal.yahoo.com/us/en/yahoo/permissions/requests/index.html)

Keep the actual `Open` field as the strategy observation; a dividend-adjusted close is a different quantity. Yahoo explains that adjusted close incorporates distributions and splits, so substituting it for opening data would change the requested experiment. Record the adjustment mode and separately document dividend handling. [Yahoo adjusted-close explanation](https://help.yahoo.com/kb/SLN28256.html)

Opening-price execution needs a time distinction. NYSE Arca's published schedule freezes new market-on-open and limit-on-open orders before its 9:30 a.m. Eastern opening auction. **Inference for this model:** observing the final open and then deciding an auction order at that same known price is not a causal execution model. The prototype's prior-open signal / next-open execution convention addresses that ordering issue but still idealizes fractional fills, execution price, liquidity and cash availability; daily bars alone cannot establish actual broker fills. [NYSE auction timelines](https://www.nyse.com/trade/auctions)

## How this should affect the research software

These implementation recommendations are subordinate to the user-designated slides; they are not the firm's published formulas:

1. **Keep the strategy non-predictive.** Use opening prices to value holdings and apply explicit allocation rules. A price input does not require a forecast; no RSI, moving-average signal, or news/fundamentals prediction is necessary.
2. **Make the purchase base explicit.** Document the amount used to calculate scheduled DCA purchases. The active convention uses capital at cycle start and resets from actual net cash after a full exit. DCA can resume in the same session. These sizing equations implement the user’s clarification but are not formulas stated in the slides.
3. **Separate observation from execution.** A recorded opening print is known only after the opening auction. A backtest that inspects that print and assumes a conditional trade filled at that exact same print is idealized. The initial prototype can use the previous session's open to decide the next opening trade. That delay is our timing choice, not evidence about the firm's broker execution. It should be visible in results.
4. **Expose the implemented parameters.** Show DCA, VA, and capture assumptions with each scenario. Allow inspection of return, drawdown, market exposure, idle cash, realized and unrealized gain, full exits, fees, and trade history. Identify the capture-percentage trigger as a project formula for the user-confirmed full-exit behavior, not an equation from the slides.
5. **Do not equate realized profit with total return.** Positive closed trades can coexist with larger unrealized losses. Display portfolio equity and drawdown alongside realized gains.
6. **Use bounded historical experiments.** Keep development and validation periods separate, compare equal-capital buy-and-hold and simple DCA, include realistic costs, and examine parameter neighborhoods. A bright cube is evidence about a particular historical sample, not evidence of future profitability.
7. **Preserve full provenance.** Store symbol, date range, data-source version, download time, split/dividend treatment, opening-price convention, code version, and parameters with each run. Missing or revised Yahoo bars should never silently become synthetic performance.
8. **Keep policy explicit.** The user's no-penny-stock requirement can be implemented with a curated ETF universe. Leveraged ETFs should be a clearly identified research choice; the source's use of leverage does not require it as a default. (Adopted 2026-09-23: six leveraged index funds are available in the lab only, with the simulated 3x US market series for earlier decades.)

## Claims that must not become software guarantees

The earlier reference material’s “perfect track record,” reported annualized return, and captured-profit totals are promotional/source claims. They are not acceptance tests for investBell. The firm's current website describes proprietary execution, aggressive growth, account-specific outcomes, and an annual-return population that generally excludes accounts opened midyear. Its performance and about pages returned the same public homepage content through the text browser, so the underlying broker performance reports were not obtained in this first review.

Leveraged ETFs reset daily; long-period outcomes can differ markedly from a multiple of the underlying index's return. The SEC gives examples of leveraged funds losing value over periods when the underlying index gained. Therefore, index recovery cannot be coded as a guarantee that a leveraged holding will recover. [SEC investor bulletin on leveraged and inverse ETFs](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/sec)

## Access and confidence limits

This review followed the firm's website, its linked blog articles, official social links, public posts, and linked videos. Some blog pages were accessible only as previously indexed text while direct requests returned 404; these may reflect an earlier site version. Social platforms limited public access. No private account, paywall, authenticated portal, or unpublished model was accessed; nobody was contacted. The exact algorithm remains an explicit research unknown despite substantial public explanation of the approach. Names, links, and other details that would identify the firm or the people involved are deliberately left out of this repository.
