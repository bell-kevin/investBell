# Strategy validation and frozen research plans

The validation command measures the current model and its sensitivity to assumptions. It does not approve live trading. The available SPY evidence does not show an advantage over the simpler comparisons, and no prospective broker-paper results have been observed yet.

## Run a reproducible historical evaluation

Install the pinned dependencies using `./scripts/setup.sh`. The command reads one existing cached snapshot through a read-only SQLite connection; it does not download prices, connect to a broker, or place orders.

```sh
.venv/bin/python -m investbell.research \
  --database data/market.sqlite3 --symbol SPY \
  --start 2021-01-04 --end 2026-09-23 \
  --evaluation-start 2024-01-01 \
  --initial-cash 10000 --dca-pct 2 --va-pct 0.1 \
  --capture-pct 10 --slippage-bps 5 --fee 0 \
  --output data/validation/2026-09-23-layer-comparison-default.json
```

`--end` is exclusive and must match an existing complete snapshot's cache key. The output must be a new path: choose another filename when rerunning a saved example. Previous reports and plans are retained, never overwritten. To register a separate prospective plan, add `--freeze-output` with another new path. `--prospective-start YYYY-MM-DD` can defer that new plan's start, but cannot put it on or before the registration day or already observed history. Generating the layer comparison does not replace an existing frozen plan or its review criteria.

The loader verifies the snapshot checksum and compares every observation date with the XNYS exchange calendar. Missing beginning, interior, or ending sessions fail the run, as do extra weekend/holiday rows. A fresh fetch timestamp cannot conceal an incomplete range. Fetch timestamps must be timezone-aware and cannot be in the future; every included session must have closed, including the data publication buffer, before the recorded fetch. A calendar failure stops validation. Old complete historical ranges remain legitimate research artifacts; they are not certified as current trading data.

Parameters stay fixed across the chronological split. Each partition starts from the same initial cash, with no carried positions, targets, or results from the earlier partition. The earlier period is named `training` for organization; the command does not fit parameters or select a winner. Both periods need at least 20 observations.

The dataset was already explored. Therefore the later period is **retrospective evaluation**, not a genuinely unseen holdout. Changing the split date, scanning neighbors, or selecting better parameters after reading the report does not turn it into prospective evidence.

## What the report contains

- Earlier, later, and full-history results for simple DCA, DCA plus VA, the full DCA/VA/capture strategy, and buy and hold. Each comparison uses identical starting cash, dates, prices, and execution assumptions. There are no later external contributions, withdrawals, or loans.
- Incremental return differences in percentage points: `dca_va_minus_simple_dca_return_pp`, `strategy_minus_dca_va_return_pp`, and `strategy_minus_simple_dca_return_pp`. The existing comparison against buy and hold is retained. All four policies and the incremental differences also appear in every sensitivity and stress scenario.
- Opening-value drawdown, worst observed session, annualized return for periods at least a year long, opening-return volatility, exposure, cash exhaustion, order counts, VA sale counts, capture counts, gross traded notional, modeled execution costs, and outstanding receivables.
- One-parameter-at-a-time neighboring settings, cost scenarios, and separate execution sensitivities. These reuse the later historical period and are exploratory.
- Explicit synthetic cash-exhaustion, crash, gap-after-capture-signal, prolonged-decline, and partial-recovery scenarios. Their losses are illustrations, not probability estimates.
- Parameters, model version, source metadata, bar digest, and source-code hashes. The zero-delay execution baseline must match the reference engine or the report fails.

The original strategy engine is unchanged. `research_execution.py` provides a separate sensitivity simulator. Sale and distribution receivables remain part of equity while unavailable to buy. Distribution entitlement is fixed using holdings at the ex-date; hypothetical payment delays affect spendability. A full exit resets its budget from net liquidation equity including receivables. Purchases remain capped by available settled cash, so funded same-session reentry is permitted.

The `simple_dca` policy uses the original fixed daily purchase budget and disables both VA sales and capture exits. The new `dca_va` policy retains that budget, the strategy's VA target and sale rules, and funded purchases after trims, but explicitly disables capture exits and cycle resets. Changing `capture_pct` cannot change this policy's trades or holdings. Setting `va_pct=0` is **not** a way to disable VA: it gives the target zero growth and still allows trims. The full `strategy` policy adds capture exits and their budget resets. `buy_and_hold` invests at the first open and retains distributions as cash.

All policies share the same fixed parameter values; none is separately optimized in this comparison. Thus the VA increment measures adding the existing VA rule to DCA, while the capture increment includes capture's changes to subsequent DCA budgets and VA targets. The saved fitted parameters were selected for the full strategy. Removing a layer tests that fixed choice; it does not establish how an independently fitted VA-only strategy would perform.

`fees_paid` reports order fees. `modeled_slippage_cost` measures filled shares times the adverse difference between the fill and observed opening price, including additional post-sale buy friction. `modeled_execution_cost` adds those two costs. They are already reflected in net equity and are not deducted twice. `gross_traded_notional` sums executed buy and sell amounts. Identical per-order assumptions can produce different total costs because the policies trade differently.

Post-sale purchases have a separately modeled price: opening price × configured buy slippage × additional post-sale friction. This measures a departure from an identical modeled opening print; it does not reconstruct actual intraday prices. Delays are counts of observed sessions, not reconstructed broker settlement or distribution payment schedules. Their effect can improve or worsen a historical result because they alter market exposure; they are not guaranteed pessimistic bounds.

Taxes, cash interest, actual partial fills and rejected orders, broker constraints, and intraday drawdowns remain outside these historical simulations. Fractional shares and split-normalized units remain assumptions. Endpoint valuations do not force liquidation or deduct final liquidation costs.

## Results from the available SPY snapshot

These are total returns, not annualized returns, with $10,000 initial cash, DCA 2% per session, VA 0.1% per session, capture 10%, 5 basis points of slippage, and no order fee. Snapshot: 1,436 openings from January 4, 2021 through September 22, 2026.

| Period | Simple DCA | DCA + VA | DCA + VA + capture | Buy and hold |
| --- | ---: | ---: | ---: | ---: |
| Earlier: Jan 2021–Dec 2023 | 29.67% | 29.64% | 26.52% | 31.87% |
| Later retrospective: Jan 2024–Sep 2026 | 62.27% | 62.14% | 54.77% | 68.07% |
| Full history, descriptive | 117.53% | 117.48% | 104.31% | 116.42% |

In the later default-parameter comparison, adding VA changed return by **−0.1375 percentage points**; adding capture and its resets changed it by another **−7.3644 points**. DCA plus VA made 17 VA sales and no captures, with 80 orders versus simple DCA's 61. Its maximum opening drawdown remained 19.76%. These results do not show an improvement from the added VA layer in this sample.

The later period's maximum opening drawdown was 19.76% for the strategy, 19.50% for buy and hold, and 19.76% for simple DCA. Full-history strategy drawdown was 25.93%, versus 25.87% for buy and hold. The strategy reached 100% exposure and exhausted spendable cash during this sample.

Neighboring parameter settings returned 51.88%–56.27% in the later retrospective period. Increasing costs to 25 basis points plus $1 per order produced 53.38%; 50 basis points plus $1 produced 49.27%. Execution-delay sensitivities produced 55.24% and 54.74%, demonstrating why changed execution cannot simply be assumed always to reduce returns.

Under the default parameters, the synthetic cash-exhaustion/crash scenario lost 50.02%. A capture signal followed by a large downward gap lost 75.03% from initial capital and drew down 83.34% from its modeled peak. The prolonged decline lost 78.62%. A capture threshold does not cap losses or protect a cash reserve.

The original three-policy results were first recorded in `data/validation/2026-09-23-retrospective-v1.json`. The earlier `v2` artifacts added calendar checks and prospective review gates. The new [default layer comparison](../data/validation/2026-09-23-layer-comparison-default.json) uses report schema `retrospective-validation-v2`, includes DCA plus VA, and preserves the original policies' metrics exactly. Earlier reports and frozen plans remain unchanged. Report files are local artifacts excluded from source distribution.

## Comparison using the saved fitted parameters

The saved model with digest beginning `c227a805b656` selected DCA 4%, VA 0.4%, and capture 8% on its older training partition. The [fitted layer comparison](../data/validation/2026-09-23-layer-comparison-fitted.json) reuses that model's exact data, parameters, and later evaluation dates, August 1, 2025 through September 22, 2026. It does not refit or replace the model. All paths start with $10,000 and use 5 basis points of slippage with no order fee.

| Policy | Total return | Maximum opening drawdown | Orders | VA sales | Captures | Modeled execution cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Simple DCA | 22.5807% | 8.0858% | 30 | 0 | 0 | $5.0714 |
| DCA + VA | 22.5795% | 8.0858% | 34 | 3 | 0 | $5.0751 |
| DCA + VA + capture | 21.6874% | 8.0207% | 86 | 3 | 2 | $27.6044 |
| Buy and hold | 25.0283% | 8.0347% | 1 | 0 | 0 | $4.9975 |

Adding VA changed return by **−0.0012 percentage points** (about $0.12 on the starting allocation). Adding capture and its resets changed it by another **−0.8920 points**. VA alone was nearly identical to DCA and provided no measured improvement here. The full strategy's slightly lower opening drawdown came with lower return. These results describe this already explored historical sample.

To reproduce the fitted comparison with a new output filename:

```sh
.venv/bin/python -m investbell.research \
  --database data/market.sqlite3 --symbol SPY \
  --start 2021-01-04 --end 2026-09-23 \
  --evaluation-start 2025-08-01 \
  --initial-cash 10000 --dca-pct 4 --va-pct 0.4 \
  --capture-pct 8 --slippage-bps 5 --fee 0 \
  --output data/validation/2026-09-23-layer-comparison-fitted.json
```

## What the prospective manifest establishes

A manifest fixes the symbol, parameters, model/code hashes, execution assumptions, known historical data digest, and the retrospective report digest. It records its real creation time, a future start date, the first scheduled exchange session, and the date of the 252nd expected session. A September 24, 2026 start currently has a minimum window end of September 24, 2027 under the pinned calendar. Recheck newly announced exchange closures before assessing completion.

The 252-session duration is a project minimum for collecting evidence, approximately one trading year; it is not a claim of statistical power or proof of a profitable edge. Registration does not create those future observations or connect a broker account.

The manifest's research review requires **all** of these conditions:

1. At least 252 completed consecutive expected sessions from the registered first session, with no missing observations.
2. Contemporaneously recorded paper evidence linked to this plan throughout that window, with unchanged strategy, parameters, code, and execution assumptions.
3. Zero duplicate broker orders and zero unresolved cash, position, fill, or distribution reconciliation differences.
4. Strategy net return at least equal to both comparable buy and hold and simple DCA, with opening drawdown no worse than buy and hold.
5. Account exposure, drawdown, and cash limits recorded before broker-paper activation and respected throughout. These personal limits require separate configuration; a research manifest cannot infer them.

Before the window ends, or without actual contemporaneous broker-paper evidence, the appropriate outcome is **insufficient evidence**. A complete valid window that misses a criterion fails research review. Passing only qualifies the evidence for human review; it never automatically authorizes live trades.

The manifest records these conditions. The paper-evidence command below checks the retained local journal when observations and fills exist. Independent broker reconciliation and a fully matched economic comparison remain review requirements; a historical simulation result cannot substitute for them.

Historical caches cannot backfill prospective operational evidence. Preserve original plan and journal creation timestamps, link journal entries to the plan digest, and retain append-only paper records. A later replay must keep its retrospective label even if all its prices occur after the manifest's proposed start. A local SHA-256 digest detects changes relative to the retained digest; it is not an independent timestamp attestation and cannot stop someone with filesystem control rewriting and rehashing files.

Any parameter, strategy-code, or execution change requires a new plan and a new prospective observation window. Keep the superseded files and do not relabel a previously reviewed period as unseen. Version 1 artifacts were superseded while implementing the calendar and review gates, before any prospective window began; the newly frozen version is the applicable research registration.

## Review prospectively recorded broker-paper evidence

Initializing the paper runner creates a separate immutable `evidence_plan` bound to the actual paper policy and execution-code fingerprint. Its first session is the next exchange session after initialization; it cannot use an earlier research manifest to retroactively qualify account activity. The runner appends timestamped quote/equity observations with the plan, policy and execution fingerprints while executing the frozen experiment. See the [paper runner guide](paper-trading.md) for account setup and operation.

```sh
.venv/bin/python -m investbell.paper_evidence \
  --ledger data/paper.sqlite3 \
  --output data/validation/paper-evidence-review-001.json
```

This command opens a consistent read-only SQLite snapshot. It neither creates a ledger nor loads credentials, contacts a broker, reconciles an account remotely, sends an order, or changes execution state. Omit `--output` to print JSON only. Saved report paths must be new.

The review uses the first registered 252-session window; later observations cannot replace a missing early day. Each session needs a valid first observation between one and 15 minutes after the exchange opening, and a later or final observation marked `session_complete`. Recording after the opening is deliberate: the opening reference price is already known. These are **post-open quote observations**, not exact opening portfolio valuations. The strategy's endpoint is its last recorded bid-marked equity in the selected window. The report also measures drawdown over the recorded quote samples; unobserved intraday losses remain unknown.

The evaluator checks contemporaneous session dates, chronological timestamps, quote age/feed, frozen fingerprints, and cash-plus-shares equity reconciliation. It audits duplicate client/broker order IDs, unresolved or unsuccessful orders, cumulative fill consistency, critical events and configured cash, new-purchase, order-notional, daily-turnover, loss and drawdown limits. For each buy intent, the latest same-session mark at or before order creation must be within the permitted quote age; otherwise evidence is insufficient. Held shares valued at that mark's ask plus requested buy quantity times limit price must fit `max_position_value`, allowing a small rounding tolerance. This is a cap on new buying, so appreciation of already-held shares alone is not a breach. Critical events remain visible even after an operator resumes; the current unstructured event history cannot automatically prove every historical exception was resolved. Sales held by the no-loss rule are listed and counted, next to the worst unrealized loss measured from each observation's recorded cost basis and bid mark; a held sale does not fail the review, because holding leaves a loss unsold rather than avoiding it.

Its statuses are:

| Status | Meaning |
| --- | --- |
| `insufficient_evidence` | The account/schema/plan is absent, the full future window has not elapsed, actual filled broker orders are absent, or required contemporaneous observations cannot be verified. |
| `failed_recorded_checks` | The completed observation window has order, policy, fingerprint, critical-event, or halt findings requiring correction/review. |
| `review_required` | The completed local evidence passed the checks that can be automated, while external reconciliation and comparable performance evidence still need review. |

Every output has `live_ready: false`. An exit code of zero means the read-only report was produced, **not** that paper or live execution passed a readiness gate.

The report constructs equal-capital buy-and-hold and simple-DCA comparisons using each session's recorded reference opening price, 5 basis points of modeled slippage and zero modeled fees. Actual strategy fills/costs and bid-valued equity are not identical to those hypothetical open fills and endpoint marks. The observation schema also lacks the complete dividend/corporate-action history needed for matched total-return comparisons. Consequently return differences are explicitly provisional, do not establish an edge, and cannot produce an automatic performance pass. A missing period cannot be filled from Yahoo history and called prospective operational evidence.

An empty or pre-observation journal therefore yields useful missing-evidence diagnostics today. It cannot manufacture the elapsed months, actual broker fills, or independent audit records that future review requires.

## Verify the implementation

```sh
.venv/bin/python -m unittest tests.test_research -v
.venv/bin/python -m unittest tests.test_paper_evidence -v
```

The research tests compare the sensitivity baseline with the reference engine across varied prices, dividends, costs, and allocations; check causal signals, delayed cash entitlement, funded reentry, and cash exhaustion; verify chronological separation and digest integrity; and reject incomplete, future-dated, or calendar-invalid cached observations. Evidence-review tests reject premature windows, historical backfill, changed fingerprints, missing sessions, incomplete phases, duplicate/unresolved orders and policy breaches, and verify that a complete local fixture still never receives live approval.
