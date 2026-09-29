# A real-world track record: broker reports, filings, archives, and social media

Research date: 2026-09-28. This follows [source-research.md](source-research.md) and [century-backtest.md](century-backtest.md) and answers one question: is there something in the record of the firm that promotes this approach that investBell's tests are missing? The firm, a small investment adviser, is not named in this repository and is called "the firm" below.

Everything here comes from public sources: adviser registration records, the firm's own website and site code, its broker-generated performance reports, the Internet Archive, social media, video captions, and published slide decks. No login, private portal, or private data was used, and nobody was contacted. Local copies of the key files are in the git-ignored `data/research/` folder.

## The short answer

**No hidden ingredient turned up.** The firm's real client results look like holding a basket of 3x leveraged tech and stock-market funds. They dropped about 69% in 2022, bounced back in 2023, and have done well since.

- **Its clients' accounts moved almost in lockstep with its six 3x funds.** Month by month, the correlation is 0.97, where 1.0 would be a perfect match. When the funds moved 10%, the accounts moved about 9.4%.
- **$10,000 in its reported results from April 2021 grew to about $36,700 by September 2026** (27%/yr, after its fees). An equal mix of the same six 3x funds grew to about $42,600 (31%/yr, no fee). The S&P 500 fund SPY grew to about $19,900 (13.5%/yr).
- **The cash cushion did not soften the crash.** The accounts fell 68.6% from December 2021 to September 2022, which was worse than holding SPXL alone (−63.8%). At the end of 2022 they had only 3.7% of the money left in cash.
- **The best year (+154% in 2023) is what simply holding the end-of-2022 portfolio would have made (+158%).** The trading rules did not add it.
- **The track record comes from a small early group.** It covers 16–22 accounts and about $2 million until 2024. Three family-sized accounts that paid almost no fee held about half of that money in 2023. Most of today's $22–25 million arrived in 2025–26, after the rebound.
- **The settings are picked from data starting in 2008–2010,** the period our 100-year test found unusually friendly to this approach. The firm's own backtests that reach back to 2006–07 show 72–87% drops and up to 6.7 years with no trades at all.
- **No regulatory trouble was found.** No disciplinary history turned up for the firm or its advisers.

## 1. What the clients actually got (broker reports)

The firm's website links a broker performance report for each year. They are Interactive Brokers "PortfolioAnalyst" reports for the combined client accounts, and each one lists every account included. All five were generated on 2026-03-11. They use money-weighted returns, which account for deposits and withdrawals. The firm chose which accounts go into each report: accounts opened during a year are left out of that year.

| Year | The firm | S&P 500 | Accounts | Money at year end | Cash at year end |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2021 (Apr 19–Dec) | +36.7% | +13.9% | 16 | $1.77M | 66% |
| 2022 | −67.6% | −19.4% | 16 | $0.68M | 3.7% |
| 2023 | +154.5% | +24.2% | 17 | $1.78M | 5% |
| 2024 | +65.6% | +23.3% | 22 | $2.51M | 38% |
| 2025 | +40.6% | +16.4% | 68 | $6.96M | 29% |
| 2026 to Sep 25 | +39.7% | +13.1% | (no report) | | |

S&P 500 figures are the ones the website shows. The 2026 figure is from the website only. The site shows **+44.5% for 2025**, but the broker report it links to says **+40.61%**. The site also shows −67.8% for 2022, while the report says −67.56%.

Same period, same start date (2021-04-19 to 2026-09-25), using real fund prices:

| $10,000 in… | Ends at | Per year |
| --- | ---: | ---: |
| The firm's reported results (after its fees) | $36,700 | 27.0% |
| Equal mix of its six 3x funds, rebalanced each January (no fee) | $42,600 | 30.6% |
| SPXL (3x S&P 500) held | $30,600 | 22.9% |
| TQQQ (3x Nasdaq-100) held | $29,900 | 22.4% |
| SPY (plain S&P 500) held | $19,900 | 13.5% |

How closely the accounts followed the funds, month by month (56 months, May 2021 to December 2025):

| Compared with | Correlation (1.0 = identical) | Accounts moved this much per 1% move |
| --- | ---: | ---: |
| Equal mix of its six 3x funds | 0.97 | 0.94% |
| SPXL | 0.93 | 1.07% |
| SPY | 0.93 | 3.2% |

The firm's regulatory filing says that on average only about half of its clients' money is invested. That doesn't show up in the month-to-month numbers. The rules buy all the way down, so accounts are nearly fully invested during the big drops, and those drops are what these numbers capture.

**The 2022 crash, month by month (broker report).** April −32%, June −31%, July +38%, August −19%, September −31%, December −24%. Peak to bottom at month-ends was −68.6%, from December 2021 to September 2022. Accounts got back to the old high in February 2024, about 2 years later. Over the same stretch, SPXL fell 63.8%, TQQQ 81.7%, TECL 78.0% and SOXL 90.5%.

**2023 was a holding year.** At the end of 2022 the accounts held $657,119 in SOXL, TQQQ, TECL, SPXL and UPRO, and $25,243 in cash. Holding exactly that through 2023 with no trades returns +158.0%. The reported result was +154.5%.

**2024 is where the rules seem to have helped.** Accounts made +65.6%, against +39.6% for the equal fund mix. The chip fund SOXL spiked in the first half of 2024 and then fell (−12% for the year), and the partial sales captured some of the spike. The year-end 2023 position values in the report don't add up exactly, so this comparison is approximate.

**Who is in the record.** The first 16 accounts were funded from 2021-04-19, before the firm's first adviser registration. The 2021 report shows $0 in fees for every account. The largest early accounts paid almost no fee in later years: three of them held 51% of the 2023 report's money and 35% of 2024's. Fees actually charged averaged about 0.6% (2022), 1.0% (2023), 1.2% (2024) and 1.5% (2025) of assets, not the 2% a new client pays. So the "net of fees" record is better than a new client paying 2% would have seen. The FAQ separately says broker commissions add just under 1% a year. In 2024, $857,000 was withdrawn from a $2.04M starting total, mostly by the three largest original accounts.

A promotional video says no client pulled out of the market in 2022. All 16 accounts did stay open, but $177,000 was withdrawn that year.

Growth in money managed: $1.8M (end of 2021) → $0.7M (end of 2022) → about $3M (mid-2024, social-media post) → $4M (October 2024 video) → 183 accounts (2025 FAQ) → **$22.6M and 298 accounts** (regulatory filing, July 2026) → about $25M (social media, August 2026). Most of today's money has never been through a big drop with the firm.

## 2. How the strategy actually runs (from the firm's own portal code and videos)

The client portal's code was public in the archived site bundle from February 2025. It computes the values it shows to clients, and those calculations match the videos:

- **Each account is split into "allocations," one per fund.** Each allocation has its own cash, shares, average price, and aggressiveness level (moderate, aggressive, ludicrous). A newer allocation type stores explicit target and reset prices.
- **Buy:** each trading day, buy `daily_invest_rate × (the allocation's cash + what its shares cost)`. In the January 2025 portal demo, the "buys left" column implies about **2% to 4.5% per day**. So a falling fund uses up its cash in about 20 to 50 trading days, which matches the fully invested accounts at the end of 2022. The more aggressive an allocation is, the faster it spends its cash.
- **Partial sell:** if the price is above the day's target price, sell `floor(shares × (price − target) ÷ (price − average cost))` shares (at least 1, never all). Profit is counted as shares × (price − average cost).
- **Full reset:** when the allocation's cash plus share value reaches `reset_target_balance`, sell everything and start over with the new total. Example from a video: $10,000 with a 30% target resets at $13,000, and the next target is $16,900. In the January 2025 demo, the reset prices were about 10–18% above each fund's average cost.
- **Timing:** one buy or sell per allocation per day, placed about 15 minutes after the open (portal trades at 9:38–9:47 a.m. Eastern; a video says 9:45 a.m. Eastern). A limit sell for the reset is placed afterwards.
- **No-loss rule:** positions are never sold below average cost. The brochure says the algorithm lets an account lose value and takes no action to prevent further loss.
- **Other account settings:** a 2% fee and optional "profit retain %" and "retain taxes" (25%) that set cash aside instead of reinvesting it.
- **Funds used over time:** 2023 also offered CURE and RXL (health care), MIDU (mid-size companies) and XLG. The current list is SOXL, SPXL, TECL, TQQQ, UPRO, UDOW (3x), QLD, ROM, USD (2x) and SPY. Four of these follow a single sector (chips or tech), even though the brochure calls them all broad-market indices.

The founder's February 2025 conference talk says the settings come from searching about 40,000 combinations and picking one in the middle of a profitable region, to avoid choosing a lucky one. He declined to give the actual numbers. His SPXL example, starting November 2008, grew about as fast as the fund itself (25.5% a year) with a beta of only 1.35. Put simply, it matched holding SPXL while holding less of it on average. That matches what [century-backtest.md](century-backtest.md) found for 2009–2026, and also what it found for earlier decades: the edge mostly disappears.

## 3. The firm's own backtests

The firm's public models page loads a data file of 27 backtests (9 funds × 3 levels), updated through 2026-09-25. The 2024 copy embedded in the old site runs through 2024-07-18. The 3x funds start in 2008–2010, after the 2008 crash began. The three 2x funds that existed earlier start in 2006–07 and include the crash:

| Backtest (current file) | Start | Per year | Worst drop | Longest stretch with no trades |
| --- | --- | ---: | ---: | ---: |
| SPXL moderate | 2008-11 | 26.9% | −40.2% | 113 trading days |
| TQQQ moderate | 2010-02 | 36.1% | −49.8% | 137 days |
| UPRO moderate | 2009-06 | 23.8% | −50.2% | 89 days |
| QLD moderate (2x Nasdaq) | 2006-06 | 20.0% | −78.3% | 942 days |
| ROM moderate (2x tech) | 2007-02 | 18.6% | −71.8% | 394 days |
| USD aggressive (2x chips) | 2007-02 | 27.1% | −87.1% | 1,701 days (~6.7 years) |

"Longest stretch with no trades" is labeled "Longest Period of No Trades" in the firm's 2023 site code. It means no cash left to buy and nothing above cost to sell.

- **Real accounts fell further than the moderate backtests.** The SPXL, TQQQ and UPRO moderate backtests show worst drops of 40–50%, and the real accounts fell 68.6% in 2022.
- **Once the 2008 crash is included, the picture matches our 100-year test.** Drops of 72–87% and years of sitting still, as in [century-backtest.md](century-backtest.md).
- **The settings were picked on the same data these results are measured on.** The firm's regulatory filing confirms its advertisements include hypothetical performance.

## 4. Replaying the firm's rules on its funds

Using investBell's `replay()` from `scripts/century_backtest.py`, I built six equal pots: SOXL, TECL, TQQQ, SPXL, UPRO and UDOW. Each pot starts on 2021-04-19 and follows the no-loss rule, and a 2% yearly fee is taken out:

| Settings | 2022 | 2023 | Worst drop | Average invested |
| --- | ---: | ---: | ---: | ---: |
| Buy 3%/day, reset at +10% | −61.2% | +98.9% | −70.3% | 83% |
| Buy 4.5%/day, reset at +10% | −65.7% | +105.7% | −73.8% | 87% |
| Just hold the six funds (no fee) | −66.7% | +109.6% | −74.6% | 100% |
| The firm's broker reports | −67.6% | +154.5% | −68.6% | — |

The public rules reproduce the 2022 crash and the size of the drop. 2023 differs because the firm's real mix leaned toward the funds that rebounded most (SOXL, TECL, TQQQ), and the equal six-fund mix doesn't. The full grid is in `replay_vs_broker.csv` in the git-ignored `data/research/` folder.

## 5. Regulatory records

The firm is a small, state-registered investment adviser. Its registration records show no disciplinary history, customer complaints or broker registrations for the firm or its advisers, and no SEC EDGAR filings mention it. Personal and business details about the people involved were reviewed but are deliberately left out of this repository.

## 6. Marketing claims, checked

| Claim | Where | What the evidence shows |
| --- | --- | --- |
| Targets 30–50% annual returns | Site, videos, 2025–26 | Backtests are in that range only for 2008/10–2026. Real results: 27%/yr since April 2021. |
| Roughly double the S&P 500's return, mid-2021 to mid-2024 (money-weighted) | Site, August 2024 | The July 2024 broker table says 61.5% total vs 30.8% since April 2021. That is double, but it works out to about 16%/yr vs 8.5%/yr. |
| 2024: 65.6%, nearly triple the S&P 500 | Social media, FAQ, talk | Matches the broker report. The equal mix of its funds made 39.6% that year. |
| Every account opened before 2024 has more than doubled (+179.1% vs 55.1%) | Social media, May 2026 | Consistent with 2024 +65.6% and 2025 +40.6% plus 2026. The 3x funds did similar or better (SOXL, TECL). |
| No client pulled out in 2022 | Video, October 2025 | No account closed, but $177K was withdrawn in 2022. |
| The 2022 drop was expected, backtested and planned for | Same video | Its moderate backtests' worst drops are 40–50%. The real drop was 68.6%. |
| "Perfect track record," "never sell at a loss" | Earlier slides, blog | Every closed trade is a winner because losing positions are never sold. Accounts still lost 68.6%. |
| $10M+ "captured profit" | Social media, August 2026 | A running total of realized sales that can only go up. Losses that are never sold don't count against it. |
| No advisory fees until 2027 for new clients | Site, now | A current promotion. New accounts are left out of the yearly return figures anyway. |

## 7. What this means for investBell

- This supports what [century-backtest.md](century-backtest.md) already found. The returns come from 3x funds (mostly tech and chips) in a strong decade, bought all the way down and held through the recovery. The DCA/VA/capture rules change the ride only a little and cost return in most older decades.
- **For investBell:** there is no missing formula to add. We now know the firm's buy speed (about 2–4.5% of each pot per day), its partial-sale formula, and that each fund runs as a separate pot. Adding those would make the lab a closer copy of the firm's rules, but it would not change the answer in [century-backtest.md](century-backtest.md).
- **For the user's own money:** the choice between plain index investing and 3x leverage still comes down to whether you could watch $10,000 fall to about $3,100 and wait two years to get it back. That is what the firm's clients' reported results did in 2022.

## Sources

All sources were public: the firm's regulatory filings (Form ADV Part 1 and the Part 2A/2B brochure, July 2026) and its advisers' registration reports; the broker performance reports for 2021–2025 linked from its website; the public backtest data behind its models page; archived copies of its website, site code and blog from 2023 to 2026 (Internet Archive); its social-media posts and video captions; and a recorded 2025 conference talk. Links are left out so that the firm and the people involved are not identified.

Could not review: a podcast interview that is now private, social platforms that require a login, the futures regulator's search (blocked automated lookups), and blog posts from 2024–25 that were never archived.
