"""Century-scale research: does adding VA (and capture) to DCA beat DCA?

Research only; places no orders. Downloads Ken French's daily US market and
T-bill returns (1926 onward, dividends included) and Yahoo OHLC for SPY, SPXL
and TQQQ into data/research/. Writes nothing else.

The vectorized simulator mirrors investbell.research_execution.simulate_execution
with zero delays and no_loss_sales=False (checked by --verify) and adds T-bill
interest on idle cash. The research lab's simulated 3x source builds the same
3x series (investbell.data.market_series).
The 3x series is synthetic: 3 x market excess return + T-bill - 0.95%/yr, reset
daily. The SPXL/TQQQ replay follows the regulatory brochure of a firm that runs
this approach: trade just after the open, intraday LIMIT SELL for the full
capture target, and never sell below average cost. These are project
reconstructions, not the firm's code.

    .venv/bin/python scripts/century_backtest.py [--verify]
"""

import argparse
import io
from pathlib import Path
import sys
import urllib.request
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "research"
FRENCH_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
              "F-F_Research_Data_Factors_daily_CSV.zip")
VAS = [0.01, 0.02, 0.04, 0.06, 0.1, 0.2, 0.5, 1.0]


def download():
    DATA.mkdir(parents=True, exist_ok=True)
    french = DATA / "ff_daily.csv"
    if not french.exists():
        request = urllib.request.Request(FRENCH_URL, headers={"User-Agent": "Mozilla/5.0"})
        archive = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(request, timeout=120).read()))
        french.write_text(archive.read(archive.namelist()[0]).decode("latin1"))
    import yfinance as yf
    for symbol in ("SPY", "SPXL", "TQQQ"):
        path = DATA / f"{symbol}.csv"
        if not path.exists():
            yf.download(symbol, start="1993-01-01", auto_adjust=False, progress=False,
                        multi_level_index=False).to_csv(path)


def load_french(leverage=1.0, expense=0.0095):
    df = pd.read_csv(DATA / "ff_daily.csv", skiprows=4, index_col=0)
    df = df[pd.to_numeric(df.index, errors="coerce").notna()]
    df.index = pd.to_datetime(df.index.astype(str).str.strip(), format="%Y%m%d")
    df = df.apply(pd.to_numeric) / 100
    rf, excess = df["RF"].to_numpy(), df["Mkt-RF"].to_numpy()
    returns = excess + rf if leverage == 1 else np.maximum(rf + leverage * excess - expense / 252, -0.999)
    return df.index, np.cumprod(1 + returns), rf


def load_ohlc(symbol):
    df = pd.read_csv(DATA / f"{symbol}.csv", index_col=0, parse_dates=True).dropna(subset=["Open", "Adj Close"])
    factor = df["Adj Close"] / df["Close"]
    return pd.DataFrame({"open": df["Open"] * factor, "high": df["High"] * factor})


def simulate(P, rf, starts, H, dca, va, cap, policy, slip=5e-4, interest=True):
    """Many start dates (rows) x parameter settings (columns); starting cash 1."""
    starts = np.asarray(starts)
    shape = (len(starts), len(dca))
    cash, shares, target, cycle = np.ones(shape), np.zeros(shape), np.zeros(shape), np.ones(shape)
    pend_sell, pend_cap = np.zeros(shape), np.zeros(shape, bool)
    peak, mdd, invested = np.ones(shape), np.zeros(shape), np.zeros(shape)
    growth = 1 + np.asarray(va)[None, :] / 100
    budget = np.asarray(dca)[None, :] / 100
    goal = 1 + np.asarray(cap)[None, :] / 100
    trims = policy in ("dca_va", "strategy")
    for k in range(H):
        idx = starts + k
        p = P[idx][:, None]
        if interest and k:
            cash *= 1 + rf[idx][:, None]
        target *= growth
        if trims:
            q = np.where(pend_cap, shares, np.minimum(shares, pend_sell))
            cash += q * p * (1 - slip)
            shares -= q
            done = pend_cap & (q > 1e-12)
            cycle = np.where(done, cash, cycle)
            target = np.where(done, 0.0, target)
        if policy == "buy_and_hold":
            request = np.full(shape, np.inf if k == 0 else 0.0)
        else:
            request = cycle * budget
        n = np.clip(np.minimum(request, cash), 0, None)
        n = np.where(n > 1e-12, n, 0.0)
        shares += n / (p * (1 + slip))
        cash -= n
        target += n
        position = shares * p
        total = cash + position
        peak = np.maximum(peak, total)
        mdd = np.maximum(mdd, (peak - total) / peak)
        invested += position / total
        if policy == "strategy":
            pend_cap = (shares > 1e-12) & (total >= cycle * goal)
        if trims:
            pend_sell = np.where(pend_cap, shares, np.minimum(shares, np.maximum(0.0, position - target) / p))
    return {"final": total, "mdd": mdd, "invested": invested / H}


def constant_mix(r, rf, starts, H, weights):
    """Daily-rebalanced weight in the risky series, remainder in T-bills."""
    w = np.asarray(weights)[None, :]
    wealth = np.ones((len(starts), w.shape[1]))
    for k in range(1, H):
        idx = starts + k
        wealth *= 1 + w * r[idx][:, None] + (1 - w) * rf[idx][:, None]
    return wealth


def replay(o, h, rf, dca=2, va=1.0, cap=5, policy="strategy", intraday=True, slip=5e-4):
    """Firm-style single path: decide after the open, fill at the open."""
    cash, shares, cost, target, cycle = 1.0, 0.0, 0.0, 0.0, 1.0
    equity, invested = np.empty(len(o)), np.empty(len(o))
    for t, p in enumerate(o):
        if t:
            cash *= 1 + rf[t]
        target *= 1 + va / 100
        if policy in ("dca_va", "strategy") and shares > 1e-12:
            capture = policy == "strategy" and cash + shares * p >= cycle * (1 + cap / 100)
            q = shares if capture else min(shares, max(0.0, shares * p - target) / p)
            if q > 1e-12 and p * (1 - slip) > cost / shares:      # never sell at a loss
                cash += q * p * (1 - slip)
                cost -= q * cost / shares
                shares -= q
                if capture:
                    shares, cost, target, cycle = 0.0, 0.0, 0.0, cash
        request = (cash if t == 0 else 0.0) if policy == "buy_and_hold" else cycle * dca / 100
        n = min(request, cash)
        if n > 1e-12:
            shares += n / (p * (1 + slip))
            cost += n
            cash -= n
            target += n
        if policy == "strategy" and intraday and shares > 1e-12:
            limit = (cycle * (1 + cap / 100) - cash) / shares
            if h[t] >= limit > max(p, cost / shares):
                cash += shares * limit
                shares, cost, target, cycle = 0.0, 0.0, 0.0, cash
        equity[t] = cash + shares * p
        invested[t] = shares * p / equity[t]
    return equity, invested


def cagr(multiple, years):
    return (np.maximum(multiple, 1e-12) ** (1 / years) - 1) * 100


def verify():
    sys.path.insert(0, str(ROOT))
    from investbell.engine import Bar, Parameters
    from investbell.research_execution import simulate_execution
    spy = load_ohlc("SPY")["2015":]
    bars = [Bar(d.date().isoformat(), float(o)) for d, o in zip(spy.index, spy["open"])]
    P, zero = spy["open"].to_numpy(), np.zeros(len(spy))
    for dca, va, cap in [(2, 0.1, 10), (4, 0.4, 8), (5, 1.0, 5)]:
        for policy in ("buy_and_hold", "simple_dca", "dca_va", "strategy"):
            # The century grid predates the no-loss rule; its results are without it.
            params = Parameters(initial_cash=10000, dca_pct=dca, va_pct=va, capture_pct=cap, slippage_bps=5, fee=0,
                                no_loss_sales=False)
            reference = simulate_execution(bars, params, policy=policy)["metrics"]["final_equity"] / 10000
            mine = simulate(P, zero, [0], len(P), [dca], [va], [cap], policy, interest=False)["final"][0, 0]
            assert abs(mine / reference - 1) < 1e-9, (dca, va, cap, policy, mine, reference)
    print("verify: vectorized simulator matches research_execution.simulate_execution")


def century(leverage):
    dates, P, rf = load_french(leverage)
    years = (dates[-1] - dates[0]).days / 365.25
    r = np.r_[0, P[1:] / P[:-1] - 1]
    label = f"{leverage:g}x"
    bh = simulate(P, rf, [0], len(P), [1], [0], [100], "buy_and_hold")
    print(f"\n## {label} US total market, {dates[0].date()}..{dates[-1].date()} ({years:.0f} years)")
    print(f"Buy and hold: {cagr(bh['final'][0, 0], years):.2f}%/yr, max drawdown {bh['mdd'][0, 0] * 100:.1f}%")
    dcas = [0.5, 1, 2, 3, 5]
    only = simulate(P, rf, [0], len(P), dcas, [0] * 5, [100] * 5, "simple_dca")["final"][0]
    D, V = (a.ravel() for a in np.meshgrid(dcas, VAS, indexing="ij"))
    both = simulate(P, rf, [0], len(P), D, V, [100] * len(D), "dca_va")["final"][0]
    table = pd.DataFrame({"dca": D, "va": V, "rel": both / np.repeat(only, len(VAS))})
    print("DCA only: " + ", ".join(f"{d}%/day {cagr(m, years):.2f}%/yr" for d, m in zip(dcas, only)))
    print("DCA+VA ending wealth relative to DCA only (rows VA %/day, columns DCA %/day):")
    print(table.pivot(index="va", columns="dca", values="rel").map(lambda x: f"{(x - 1) * 100:+.1f}%").to_string())

    for horizon in (10, 20):
        H = 252 * horizon
        starts = np.arange(0, len(P) - H, 21)
        only = simulate(P, rf, starts, H, [2], [0], [100], "simple_dca")["final"][:, 0]
        hold = simulate(P, rf, starts, H, [2], [0], [100], "buy_and_hold")["final"][:, 0]
        both = simulate(P, rf, starts, H, [2] * len(VAS), VAS, [100] * len(VAS), "dca_va")["final"]
        rel = both / only[:, None]
        Dg, Vg, Kg = (a.ravel() for a in np.meshgrid([1, 2, 5], [0.04, 0.1, 0.5, 1.0], [5, 10, 20, 30], indexing="ij"))
        full = simulate(P, rf, starts, H, Dg, Vg, Kg, "strategy")
        weights = np.linspace(0.2, 1.0, 17)
        mix = constant_mix(r, rf, starts, H, weights)
        column = np.clip(np.searchsorted(weights, full["invested"]), 0, len(weights) - 1)
        matched = mix[np.arange(len(starts))[:, None], column]
        example = int(np.flatnonzero((Dg == 2) & (Vg == 1.0) & (Kg == 5))[0])
        print(f"\n{label}, {horizon}-year windows starting monthly ({len(starts)} windows), DCA 2%/day:")
        print(f"  Buy and hold beats DCA only in {(hold > only).mean() * 100:.0f}% of windows")
        print(f"  DCA+VA vs DCA only: richer {(rel > 1.0001).mean() * 100:.0f}%, tie {(abs(rel - 1) <= 1e-4).mean() * 100:.0f}%,"
              f" poorer {(rel < 0.9999).mean() * 100:.0f}%, median {(np.median(rel) - 1) * 100:+.2f}%")
        print(f"  Full strategy (48 settings) beats buy and hold in {(full['final'] > hold[:, None]).mean() * 100:.0f}%,"
              f" an exposure-matched T-bill mix in {(full['final'] > matched).mean() * 100:.0f}% of window-settings")
        print(f"  Firm example (2/1/5): median {np.median(cagr(full['final'][:, example], horizon)):.1f}%/yr vs buy and hold"
              f" {np.median(cagr(hold, horizon)):.1f}%/yr; beats exposure-matched mix in"
              f" {(full['final'][:, example] > matched[:, example]).mean() * 100:.0f}% of windows")


def modern(symbol, start, end):
    ohlc = load_ohlc(symbol)[start:end]
    dates = ohlc.index
    french_dates, _, french_rf = load_french()
    rf = pd.Series(french_rf, index=french_dates).reindex(dates).ffill().fillna(french_rf[-1]).to_numpy()
    o, h = ohlc["open"].to_numpy(), ohlc["high"].to_numpy()
    years = (dates[-1] - dates[0]).days / 365.25
    print(f"\n## {symbol} {dates[0].date()}..{dates[-1].date()}, DCA 2%/day, VA 1%/day, capture 5%")
    runs = {"Buy and hold": replay(o, h, rf, policy="buy_and_hold"),
            "DCA only": replay(o, h, rf, policy="simple_dca"),
            "DCA + VA": replay(o, h, rf, policy="dca_va"),
            "DCA + capture (VA off)": replay(o, h, rf, va=1e6, cap=5),
            "DCA + VA + capture": replay(o, h, rf)}
    w = runs["DCA + VA + capture"][1].mean()
    daily = np.r_[0, o[1:] / o[:-1] - 1]
    runs[f"Constant {w * 100:.0f}% {symbol} / T-bills"] = (np.cumprod(1 + w * daily + (1 - w) * rf), np.full(len(o), w))
    for name, (equity, invested) in runs.items():
        drawdown = (equity / np.maximum.accumulate(equity)).min() - 1
        print(f"  {name:28s} {cagr(equity[-1], years):6.2f}%/yr  max drawdown {drawdown * 100:6.1f}%  invested {invested.mean() * 100:4.0f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="cross-check the simulator against the engine first")
    args = parser.parse_args()
    np.seterr(over="ignore")
    download()
    if args.verify:
        verify()
    century(1.0)
    century(3.0)
    modern("SPXL", "2009-01-02", "2026-03-01")
    modern("SPXL", "2021-01-04", None)
    modern("TQQQ", "2010-02-11", None)


if __name__ == "__main__":
    main()
