# Does DCA + VA beat DCA? A 100-year test, and what a firm using this approach actually trades

Research date: 2026-09-23. Historical research only; nothing here places orders or predicts returns. Reproduce with:

```sh
.venv/bin/python scripts/century_backtest.py --verify
```

`--verify` first proves the script's fast simulator produces the same final equity as `investbell.research_execution.simulate_execution` (to 1e-9) for all four policies. Data is downloaded to the git-ignored `data/research/`.

A small investment adviser publicly promotes a similar approach with leveraged funds. It is not named in this repository and is called "the firm" below.

These results predate the lab's no-loss rule and were computed without it (`no_loss_sales=False`). The research lab now offers the same Ken French 1x series and the simulated 3x series as data sources, so any 20-year window can be explored on the cube.

## Short answer

- **For ordinary (1x) index funds, adding VA to DCA did not beat DCA over 100 years.** If the VA target grows at least as fast as the market (about 0.04%/day or more), VA almost never sells and the result is identical to DCA. If the target grows slower than the market, VA sells into every rally and falls far behind: 64–92% less money after 100 years.
- **DCA alone is effectively buy-and-hold.** At 2%/day the money is fully invested after 50 trading days; from then on the two are the same. Lump-sum buy-and-hold finished ahead of DCA in 62% of historical 10- and 20-year windows, because markets rise more often than they fall.
- **Adding capture (full exits) usually lowered returns** and never prevented the big crashes: every one of 240 settings still lost 77–84% in 1929–32.
- **The firm's results come from 3x leveraged ETFs plus capture resets, not from VA.** Replaying its published example on real SPXL reproduces its reported numbers (about 29.9%/yr, about −47% drawdown). On SPXL, switching VA off changes the result by only 0.01 to 0.2 points per year.
- **The approach's edge belongs to the post-2009 era.** Across 100 years of synthetic 3x data, the firm's example settings beat a simple constant mix with the same average exposure in only 13–35% of windows. They won in 100% of 10-year windows starting in the 2010s, which is the only period with real leveraged-ETF data to backtest on.

## Data and method

| Item | Choice |
| --- | --- |
| 100-year index | Ken French daily US market return (CRSP value-weighted, dividends included), 1926-07-01 to 2026-07-31, 26,296 sessions. It is the whole US market rather than exactly the S&P 500. |
| Cash | Idle cash earns the daily 1-month T-bill rate from the same file (the firm says its cash earns interest). |
| 3x series | Daily `3 × market excess return + T-bill − 0.95%/yr`, reset daily like SPXL/UPRO. From 2009 to 2026 it has 0.995 daily correlation with real SPXL (31.5% vs 30.3%/yr). |
| Rules | Exactly investBell's `simple_dca`, `dca_va`, `strategy` and `buy_and_hold` policies: DCA budget = % of cycle-start capital per day; VA target grows VA%/day plus purchases, and the excess is trimmed; capture sells everything at +capture% and restarts. Decisions fill at the next observation; 5 bp slippage. |
| Windows | Every month-start, 10- and 20-year horizons (1,133 and 1,013 windows). |
| Exposure-matched benchmark | A daily-rebalanced mix of the same fund and T-bills holding the strategy's average invested percentage. This separates timing skill from simply holding less stock. |
| Firm-rules replay | Real SPXL/TQQQ open/high prices; trades just after the open; an intraday limit sell for the full capture target; never sells below average cost (the firm's regulatory brochure). |

## Results: 1x index, 1926–2026

Buy and hold: **10.26%/yr**. DCA only: 10.21–10.25%/yr (0.5–5% per day).

DCA + VA ending wealth relative to DCA only, single 100-year run:

| VA target growth per day | DCA 0.5% | DCA 1% | DCA 2% | DCA 3% | DCA 5% |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0.01% (≈2.6%/yr) | −91.5% | −88.5% | −85.6% | −82.7% | −77.2% |
| 0.02% (≈5.2%/yr) | −83.0% | −80.6% | −76.1% | −71.6% | −64.2% |
| 0.04% (≈10.6%/yr) | −0.1% | −0.0% | −0.1% | −0.0% | −0.1% |
| 0.06% and higher | ±0.1% | ±0.1% | ±0.1% | ±0.1% | ±0.1% |

Rolling windows, DCA 2%/day:

| | 10-year windows | 20-year windows |
| --- | ---: | ---: |
| DCA + VA richer than DCA only | 16% | 18% |
| Tie (within 0.01%) | 40% | 39% |
| DCA + VA poorer | 44% | 43% |
| Median difference | −0.00% | −0.00% |
| Full strategy beats buy and hold (48 settings) | 25% | 18% |
| Full strategy beats exposure-matched mix | 37% | 29% |
| Firm's example settings (2/1/5): median return vs buy and hold | 9.3% vs 10.7%/yr | 9.1% vs 10.2%/yr |

## Results: 3x leverage, 1926–2026

Buy and hold 3x: 13.56%/yr, but a **−99.9%** fall from 1929 to 1932. It regained its 1929 peak only in 1955. Other 3x drawdowns: −92% (1937–42), −91% (1973–74), −93% (2000–02), −95% (2007–09), −77% (2020), −65% (2022).

| | 10-year windows | 20-year windows |
| --- | ---: | ---: |
| DCA + VA richer / tie / poorer than DCA only | 50% / 11% / 39% | 52% / 11% / 37% |
| Median DCA + VA difference | +0.01% | +0.02% |
| Full strategy beats 3x buy and hold | 34% | 29% |
| Full strategy beats exposure-matched mix | 41% | 30% |
| Firm's example settings: median return vs 3x buy and hold | 14.1% vs 18.0%/yr | 10.9% vs 14.2%/yr |
| Firm's example settings beat exposure-matched mix | 35% | 13% |

The firm's example settings beating the exposure-matched mix, by 10-year window start decade: 1920s 8%, 1930s 35%, 1940s 2%, 1950s 28%, 1960s 19%, 1970s 64%, 1980s 26%, 1990s 31%, 2000s 46%, **2010s 100%**.

## Results: replaying the firm's published example

Published example (a 2026 article by the firm's founder): SPXL, DCA 2%/day, VA 1%/day, capture 5%; the author reported 29.9% CAGR and a −46.9% maximum drawdown.

| SPXL 2009-01 to 2026-02 | CAGR | Max drawdown | Avg invested |
| --- | ---: | ---: | ---: |
| Buy and hold | 30.0% | −74.8% | 100% |
| DCA only | 33.2% | −74.8% | 99% |
| DCA + VA | 33.2% | −74.8% | 99% |
| DCA + capture, VA off | 28.8% | −47.3% | 55% |
| DCA + VA + capture (intraday limit) | 28.8% | −47.3% | 54% |
| Same, investBell next-open timing | 29.9% | −47.3% | 57% |
| Constant 54% SPXL / 46% T-bills | 20.1% | −50.4% | 54% |

Over 2021-01 to 2026-09, SPXL buy and hold returned 28.5%/yr, and the full strategy returned 27.9%/yr with a −47.3% drawdown. Compare the firm's reported ~27.5%/yr net of fees. On TQQQ (2010–2026), the full strategy returned 36.0%/yr versus 43.6% for buy and hold.

In this post-2009 period the capture resets beat an exposure-matched mix by about 8–10 points per year. The strategy is fully invested at the bottom of each crash, after averaging down, and every crash since 2009 rebounded quickly. The century test shows that pattern did not hold in most earlier decades.

On TQQQ, the VA layer happened to matter on this one path (36.0%/yr with VA versus 31.2%/yr without). Across 100 years of 3x windows, however, DCA + VA versus DCA was a coin flip with a median difference of +0.01%.

## What the firm's public filings and posts establish

From the firm's Form ADV Part 1 and Part 2A brochure (July 2026):

- Established in mid-2021 and began business in August 2021. State-registered, not SEC-registered.
- **298 clients and accounts, about $22.6M** in discretionary assets. The slide says "300+ accounts" and "$26M+".
- Trades **only the listed ETFs, all leveraged except SPY**: SOXL, SPXL, TECL, TQQQ, UPRO, UDOW (3x); QLD, ROM, USD (2x); SPY (1x).
- Model portfolios **never sell at a loss**; they hold positions until the market recovers above the average entry price. No stop-losses or other loss mitigation.
- VA sells begin when the share price exceeds the average entry price; an overall profit target sells the entire position and restarts.
- The automated program trades in the first moments of the trading day, then places **limit sell** orders to capture profit during the day. It runs on AWS through the Interactive Brokers API.
- On average only about half of client accounts are invested. In an extended decline they keep buying shares until the account runs out of cash.
- Fee: **2.0% per year** of assets, not negotiable; $25,000 minimum.

From the firm's archived blog (the blog now returns 404; copies from the Internet Archive):

- The strategy returned **−67.8%** in 2022, against −19.4% for the S&P 500.
- Clients averaged a 65% return in 2024. For comparison, SPXL returned 63.6% in 2024.
- About May 2025 the firm announced passing $2,000,000 in "captured profits".
- The firm targets 30–50% annual returns. Its history post gives 30% as an example overall growth target and says it chose leveraged ETFs because their backtested returns were so high.

The firm's current website states that its ~27.5% annualized consolidated net return is net of fees and includes only accounts open before each year began; accounts opened mid-year are excluded.

### Reading the Result slide against this evidence

| Slide claim | Evidence |
| --- | --- |
| ~27.5% annualized return | Plausible for 2021–2026. SPXL buy and hold returned 28.5%/yr over the same period, and the replay returns 27.9%/yr. This is what 3x leverage did in this market, not evidence that DCA + VA beats DCA. |
| 7 years with a perfect track record | The firm has operated since August 2021, about five years. Earlier trading before the firm existed may account for the other years. Its own blog reports −67.8% in 2022. The rule "never sell at a loss" makes every *closed* trade a winner by design, so a perfect record of closed trades says nothing about account losses. |
| $26M+ under management, 300+ accounts | Regulatory filing (July 2026): $22.6M, 298 accounts. Close, possibly updated since. |
| $10.7M+ cumulative profit | Not independently verifiable. "Captured profit" counts realized gains from sales; unrealized losses are never realized under the no-loss-sale rule. The firm's weekly social-media updates report it as a running total (one week added $216K and passed $9M), so it can only rise. |

The website footnote also makes an exception for 2021: that year includes accounts opened during it (checked 2026-09-23).

## Cross-check: a separate claude.ai analysis the same day

A separate claude.ai conversation on 2026-09-23 (not published) tested the same question with its own code. Its scripts (a daily VA sweep and a 3x rules simulation) are not in this repository and were not rerun here. The figures below are as that conversation reported them.

**Different rules, same answer.** It used Ken French daily data from July 1926 to November 2018. Every strategy deposited $1 each week. VA sold the whole traded position once it rose a set percentage above its average cost, and reentry bought a set percentage of the total portfolio each morning. It first modeled capture as a core that is never sold, and later corrected this: capture is a full reset.

- At 2–3% reentry per morning, DCA + VA ended with 16–64% less money than DCA alone after 92 years.
- Neighboring settings differed widely. At 2% per morning, a +10% trigger finished 26% behind, +15% finished 42% behind, and +20% finished 25% behind. That pattern is noise, not a reliable optimum.
- Across 72 twenty-year windows, those settings beat DCA 25–35% of the time.
- Of 288 settings, 6 beat DCA by more than 1%, 83 finished within 1%, and 199 lost.

**Why selling spikes lost.** In the year after a sell signal, the market returned 13.0% on average, versus 11.5% after a random day. A spike did not predict a fall, so the cash mostly sat out rising markets. Selling a spike also assumes it will come back down, which conflicts with the slides' rule against assumptions.

**3x leverage, five-year windows.** It rebuilt the firm's disclosed rules on synthetic 3x data from 1926 to 2018: 64 settings over 350 five-year windows. For settings about half invested, matching the firm's filing:

- 27.5%/yr or better occurred in about 1% of windows before fees, and in none after a 2% fee.
- They lost money in 27% of windows and fell more than 50% at some point in about 45%.
- They beat an exposure-matched mix of the 3x fund and T-bills in about 45%.
- The worst starts were mid-1929 (−98%, versus −54% for the plain index), late 1999 (−67%) and mid-2007 (−58%). Starts just after a crash (late 1974, late 2008) made 21–24%/yr.

Both analyses agree: on 1x index funds, VA does not beat DCA, and the firm's results come from leverage in a favorable period.

**Reported yearly results.** The conversation also reported the firm's figures of +154.4% in 2023 and +65.6% in 2024. Accounts open before 2024 gained +179.1% from January 2024 to May 22, 2026. These figures could not be read as text from the website's chart here. Combined with −67.8% in 2022, 2022–2024 compounds to about +36%. Over those three years, SPY returned +29%, SPXL +20%, TQQQ −1% and SOXL −59%. In that crash-and-rebound period, the cash buffer beat holding the 3x funds outright. Six of the ten traded funds track tech, semiconductor or Nasdaq-100 indexes.

## Limits of this test

- The 100-year series is the whole US market, not the S&P 500 alone. The 3x series assumes borrowing at the T-bill rate; that is optimistic before the 1980s, and leveraged ETFs did not exist.
- Daily closes cannot show intraday spikes before 1993. The intraday-limit replay uses real SPXL/TQQQ highs only.
- Taxes are ignored. Frequent VA and capture sales realize short-term gains; The firm tells clients to set aside about 25% of each profit capture. Buy and hold defers those taxes, so after-tax results would widen the gap.
- A 2% advisory fee is not deducted in these simulations.
- These are reconstructions of publicly described rules. The firm's exact code, per-client allocations across several ETFs, and parameter choices are not public.
