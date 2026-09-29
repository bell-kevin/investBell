"""Validated, bounded application requests shared by HTTP and daily reports."""

from dataclasses import asdict, replace
from itertools import product
import hashlib
import json

from . import MODEL_VERSION, __version__
from .data import LEVERAGED_ETFS, MARKET_SERIES, PriceStore, completed_end, demo_bars, parse_range
from .engine import Parameters, SYMBOLS, number, simulate
from .research_execution import simulate_execution


DEFAULT_GRID = {"dca": [1, 2, 3, 4, 5], "va": [0, 0.1, 0.2, 0.3, 0.4], "capture": [3, 5, 8, 10, 15]}
STRATEGY = {
    "name": "DCA, VA and capture",
    "source": "User-provided slides 1.jpeg–5.jpeg, notes 6.png and explicit clarifications; automatic full exits and same-session continuation retained",
    "principles": ["DCA to buy, including dips", "VA to sell during spikes", "Capture the long-term growth"],
    "strategy_variables": ["DCA", "DVA", "capture"],
    "terminology": "DVA (dollar-value averaging) is the VA label in the slides and va_pct in the API. These are the same variable.",
    "model_training": "The daily Yahoo research workflow fits only DCA/DVA/capture and saves a model for two calendar years before refitting. The interactive grid is manual exploration.",
    "excluded_inputs": ["technical indicators", "influencer opinions", "news", "price forecasts"],
    "capture": "Automatic full exits remain part of the strategy. An exit does not end trading for that day; DCA can resume in the same session.",
    "formula_status": "The slides do not define exact formulas. DCA sizing, the VA target, the full-exit threshold and execution timing are simulation assumptions.",
    "sale_rule": "No-loss rule (on by default): a VA trim or full exit is held if its net proceeds would be below the cost of the shares sold. It is a rule, not a fourth fitted variable.",
}
WARNINGS = [
    "The user-provided slides define DCA buys, VA sells and the long-term growth objective. The user also confirmed automatic full exits and same-session continuation; exact formulas remain simulation assumptions.",
    "Decisions use the latest observed open and fill at the next session open, with slippage and per-order fees. No closing-price signals.",
    "DCA purchases and VA trims may both occur in a session and each incurs its configured fee. The VA target is not reduced by trims.",
    "The full-exit threshold is a project rule, not a formula supplied by the slides. After an exit, DCA can buy in the same session using actual net cash.",
    "Daily opening observations cannot locate intraday spikes or exits. The strategy permits exits before the day ends; this backtest models only the available opens.",
    "Fractional shares, immediate cash reuse, no taxes, no cash interest, and no broker constraints are modeled. There are no live orders.",
    "Dividends and capital-gain distributions are credited on ex-date using prior holdings, not actual payment date; this advances cash availability.",
    "Returns and drawdowns use opening valuations. Intraday losses are not measured. The buy-and-hold benchmark has the same starting cash and initial buy costs; distributions stay in cash. Neither strategy nor benchmark is forced to sell at the endpoint, so there are no endpoint liquidation costs.",
    "Every cube is an in-sample scenario, not an optimized recommendation or forecast. Results can change with the dates and assumptions.",
    "Each cube's 'vs simple DCA' figure compares its final value with simple DCA at the same daily DCA budget, dates and costs: no VA trims and no full exits. It measures what VA and capture added or lost.",
]
NO_LOSS_WARNINGS = {
    True: "The no-loss rule holds any VA trim or full exit whose net proceeds would be below the cost of the shares sold, including fees. Held positions keep their losses on paper: check the worst unsold loss and the drawdown, not just the absence of losing sales.",
    False: "The no-loss rule is off: a sale signaled on one observation can fill below average cost after a gap.",
}
# Ken French series replace the Yahoo daily-open specifics in WARNINGS.
MARKET_WARNINGS = {
    WARNINGS[1]: "Observations are daily closing total-return levels of Ken French's US market series (all CRSP stocks, not a single fund). Decisions use the latest close and fill at the next close, with slippage and per-order fees.",
    WARNINGS[4]: "Daily closes cannot locate intraday spikes or exits.",
    WARNINGS[5]: "Idle cash earns the daily 1-month T-bill rate from the same file. Fractional shares, immediate cash reuse, no taxes, and no broker constraints are modeled. There are no live orders.",
    WARNINGS[6]: "Dividends are included in the market return rather than paid out as cash.",
    WARNINGS[7]: "Returns and drawdowns use daily closing levels. The buy-and-hold benchmark has the same starting cash and initial buy costs. Neither strategy nor benchmark is forced to sell at the endpoint.",
}
SIMULATED_3X_WARNING = ("Simulated 3x: each day earns 3 x the market's return above T-bills plus the T-bill rate, minus 0.95%/yr in expenses, "
                        "reset daily like SPXL or UPRO. No 3x index fund existed before 2006, and borrowing at the T-bill rate is optimistic before the 1980s.")


def warnings_for(kind, symbol, params):
    if kind in MARKET_SERIES:
        warnings = [MARKET_WARNINGS.get(warning, warning) for warning in WARNINGS]
        if MARKET_SERIES[kind]["leverage"] > 1:
            warnings.insert(0, SIMULATED_3X_WARNING)
    else:
        warnings = list(WARNINGS)
    warnings.append(NO_LOSS_WARNINGS[params.no_loss_sales])
    if kind == "demo":
        warnings.insert(0, "Synthetic prices are generated for demonstration; weekends are omitted but exchange holidays are not modeled.")
    elif kind == "yahoo":
        if symbol in LEVERAGED_ETFS:
            fund = LEVERAGED_ETFS[symbol]
            warnings.insert(0, f"{symbol} seeks {fund['leverage']}x the daily {fund['index']} return and resets daily, so over longer periods "
                               f"it does not return {fund['leverage']}x the index. Yahoo history begins {fund['first_session']}, an era of fast "
                               "recoveries from crashes; the simulated 3x US market covers earlier decades. Research only: paper trading "
                               "accepts only unleveraged funds.")
        warnings.append("Yahoo is an unofficial, rate-limited data source. Snapshots expire after 24 hours; data errors fail visibly and never trigger a synthetic fallback.")
    return warnings


COMPARISON = {
    "baseline": "simple_dca",
    "definition": "Simple DCA uses the same daily DCA budget, starting cash, dates, slippage and fees, with no VA trims, full exits or cycle resets.",
    "metric": "vs_simple_dca_pct = (final_equity / simple DCA final_equity - 1) x 100",
}


def simple_dca_equity(bars, params, dca_values, cash_rates=None):
    """Final equity of the no-VA, no-capture baseline for each DCA budget."""
    return {dca: simulate_execution(bars, replace(params, dca_pct=dca), policy="simple_dca",
                                    cash_rates=cash_rates)["metrics"]["final_equity"]
            for dca in dca_values}


def compare_with_simple_dca(metrics, baseline):
    return {**metrics, "simple_dca_final_equity": baseline,
            "vs_simple_dca_pct": (metrics["final_equity"] / baseline - 1) * 100 if baseline > 0 else None}


def config():
    return {"version": __version__, "model_version": MODEL_VERSION, "symbols": list(SYMBOLS),
            "leveraged_symbols": LEVERAGED_ETFS, "market_series": MARKET_SERIES,
            "defaults": {"symbol": "SPY", "source": "yahoo",
            "start": "2020-01-01", "end": completed_end().isoformat(), **asdict(Parameters()), "grid": DEFAULT_GRID},
            "max_grid_cells": 343, "max_years": 20, "paper_only": True, "strategy": STRATEGY,
            "formulas": {
                "dca": "daily dollars = cycle-start total portfolio × DCA% / 100; capped by available cash",
                "va": "target = previous target × (1 + daily VA% / 100) + actual buy notional; sell value above target next session",
                "capture": "if cash + shares × observed open ≥ cycle-start total × (1 + capture% / 100), sell all at the next observed open; reset from net cash and allow DCA buying in that same session",
                "no_loss": "hold a sale if gross proceeds − fee < cost basis × shares sold / shares held; cost basis is cash paid for held shares including buy fees",
            }, "warnings": WARNINGS + [NO_LOSS_WARNINGS[True]]}


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("The request must be a JSON object.")
    permitted = {"symbol", "start", "end", "source", "grid", "refresh", *Parameters.__dataclass_fields__}
    if set(request) - permitted:
        raise ValueError(f"Unknown request fields: {', '.join(sorted(set(request) - permitted))}")
    defaults = config()["defaults"]
    source = request.get("source", defaults["source"])
    if source not in ("demo", "yahoo", *MARKET_SERIES):
        raise ValueError("source must be yahoo, demo, " + " or ".join(MARKET_SERIES) + ".")
    symbol = str(request.get("symbol", defaults["symbol"])).upper()
    if source in MARKET_SERIES:
        symbol = MARKET_SERIES[source]["symbol"]
    elif symbol not in SYMBOLS and symbol not in LEVERAGED_ETFS:
        raise ValueError("Choose a supported index ETF: " + ", ".join((*SYMBOLS, *LEVERAGED_ETFS)))
    start, end = request.get("start", defaults["start"]), request.get("end", defaults["end"])
    parse_range(start, end)
    params = Parameters(**{key: request[key] for key in Parameters.__dataclass_fields__ if key in request})
    grid = request.get("grid", DEFAULT_GRID)
    if not isinstance(grid, dict) or set(grid) != {"dca", "va", "capture"}:
        raise ValueError("grid must contain dca, va, and capture arrays.")
    bounds = {"dca": (0.01, 100), "va": (0, 5), "capture": (0.1, 1000)}
    axes = {}
    for key, limits in bounds.items():
        values = grid[key]
        if not isinstance(values, list) or not 1 <= len(values) <= 7:
            raise ValueError("Each grid axis needs 1 to 7 numeric values (at most 343 cubes).")
        axes[key] = sorted(set(number(value, key, *limits) for value in values))
    if "refresh" in request and not isinstance(request["refresh"], bool):
        raise ValueError("refresh must be true or false.")
    return symbol, source, start, end, params, axes


def run(request, *, store=None):
    symbol, kind, start, end, params, axes = validate_request(request)
    cash_rates = None
    if kind in MARKET_SERIES:
        store = store or PriceStore()
        bars, cash_rates, source = store.load_market(kind, start, end, refresh=request.get("refresh", False))
    elif kind == "demo":
        bars = demo_bars(start, end)
        payload = json.dumps([asdict(bar) for bar in bars], separators=(",", ":"))
        source = {"kind": "demo", "label": "SYNTHETIC DEMO · not market data", "symbol": symbol,
                  "cached": False, "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                  "price_basis": "synthetic weekday fixture; same fixture for every ETF", "end_exclusive": True}
    else:
        store = store or PriceStore()
        bars, source = store.load(symbol, start, end, refresh=request.get("refresh", False))
    baselines = simple_dca_equity(bars, params, sorted({*axes["dca"], params.dca_pct}), cash_rates)
    selected = simulate(bars, params, cash_rates=cash_rates)
    selected["metrics"] = compare_with_simple_dca(selected["metrics"], baselines[params.dca_pct])
    grid = []
    for dca, va, capture in product(axes["dca"], axes["va"], axes["capture"]):
        cell_params = replace(params, dca_pct=dca, va_pct=va, capture_pct=capture)
        metrics = (selected["metrics"] if cell_params == params else
                   compare_with_simple_dca(simulate(bars, cell_params, detailed=False, cash_rates=cash_rates)["metrics"],
                                           baselines[dca]))
        grid.append({"dca_pct": dca, "va_pct": va, "capture_pct": capture, **metrics})
    warnings = warnings_for(kind, symbol, params)
    if selected["metrics"]["skipped_orders"]:
        warnings.append("Some sell orders were skipped because their proceeds could not cover the configured fee.")
    return {"version": __version__, "model_version": MODEL_VERSION, "strategy": STRATEGY, "source": source, "bars_count": len(bars),
            "range": {"start": bars[0].date, "end": bars[-1].date,
                      "requested_start": start, "requested_end": end, "requested_end_exclusive": True},
            "selected": selected, "grid": grid,
            "comparison": {**COMPARISON, "simple_dca": [
                {"dca_pct": dca, "final_equity": equity, "total_return_pct": (equity / params.initial_cash - 1) * 100}
                for dca, equity in baselines.items()]},
            "warnings": warnings}
