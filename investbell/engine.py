"""Recurring DCA buys, VA trims, and automatic full exits without forecasts.

Prices and dividend amounts must share a split-adjusted (not dividend-adjusted)
basis. All decisions made using an open are filled at a *later* session's open.
The first purchase depends only on known cash. Daily frequency, cycle-start
DCA sizing, the VA target recurrence, and the full-exit threshold are explicit
simulation assumptions; the source slides do not supply these formulas. Full
exits are user-confirmed and may be followed by DCA during the same session.
Daily-open data determines modeled fill times, not an end-of-day restriction.

With no_loss_sales (the default), a VA trim or full exit whose net proceeds
would fall below the cost of the shares sold is held, not sold. Held positions
keep their losses on paper; worst_unrealized_loss_pct reports them.
"""

from dataclasses import asdict, dataclass
from datetime import date
import math
from typing import Iterable


SYMBOLS = ("SPY", "VTI", "VOO", "QQQ", "DIA", "IWM")


def number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class Parameters:
    initial_cash: float = 10000.0
    dca_pct: float = 2.0
    va_pct: float = 0.1
    capture_pct: float = 10.0
    slippage_bps: float = 5.0
    fee: float = 0.0
    no_loss_sales: bool = True

    def __post_init__(self):
        if type(self.no_loss_sales) is not bool:
            raise ValueError("no_loss_sales must be true or false.")
        limits = {
            "initial_cash": (100, 1_000_000_000),
            "dca_pct": (0.01, 100),
            "va_pct": (0, 5),
            "capture_pct": (0.1, 1000),
            "slippage_bps": (0, 500),
            "fee": (0, 100),
        }
        for field, bounds in limits.items():
            object.__setattr__(self, field, number(getattr(self, field), field, *bounds))


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    dividend: float = 0.0
    split: float = 0.0


def validate_bars(bars: Iterable[Bar]) -> list[Bar]:
    bars = list(bars)
    if not bars:
        raise ValueError("No usable daily opening prices in this date range.")
    previous = ""
    for bar in bars:
        if not isinstance(bar, Bar):
            raise ValueError("Every daily observation must be a Bar.")
        try:
            parsed = date.fromisoformat(bar.date)
        except (ValueError, TypeError):
            raise ValueError("Invalid ISO date in daily observations.") from None
        if parsed.isoformat() != bar.date or bar.date <= previous:
            raise ValueError("Daily observations must have unique, ascending ISO dates.")
        number(bar.open, "open", 0.000001, 1_000_000)
        number(bar.dividend, "dividend", 0, 1_000_000)
        number(bar.split, "split", 0, 10000)
        previous = bar.date
    return bars


def validate_cash_rates(cash_rates, bars):
    """Optional daily interest on idle cash, one rate per observation."""
    if cash_rates is None:
        return None
    cash_rates = list(cash_rates)
    if len(cash_rates) != len(bars):
        raise ValueError("cash_rates needs one daily rate per observation.")
    return [number(rate, "cash rate", -0.01, 0.01) for rate in cash_rates]


def simulate(bars: Iterable[Bar], params: Parameters, *, detailed=True, cash_rates=None) -> dict:
    bars = validate_bars(bars)
    cash_rates = validate_cash_rates(cash_rates, bars)
    cash = params.initial_cash
    shares = 0.0
    # Cash paid for the shares still held, including buy fees.
    basis = 0.0
    cycle_start = cash
    target = 0.0
    pending = {"buy_dollars": cycle_start * params.dca_pct / 100, "sell_shares": 0.0,
               "capture": False, "signal_date": None, "reason": "initial DCA"}
    slippage = params.slippage_bps / 10000
    trades, equity = [], []
    trade_count = capture_count = skipped_orders = held_sales = losing_sales = 0
    peak = cash
    drawdown = worst_unrealized = 0.0
    dividend_total = interest_total = realized_gain = 0.0
    benchmark_notional = max(0.0, cash - params.fee)
    benchmark_shares = benchmark_notional / (bars[0].open * (1 + slippage))
    benchmark_cash = 0.0 if benchmark_notional > 0 else cash

    for index, bar in enumerate(bars):
        if cash_rates and index:
            interest = cash * cash_rates[index]
            cash += interest
            interest_total += interest
            benchmark_cash *= 1 + cash_rates[index]
        # Yahoo's raw Open already uses split-normalized units. Applying the
        # split event to shares here would double-count it.
        distribution = shares * bar.dividend
        cash += distribution
        dividend_total += distribution
        if index:
            benchmark_cash += benchmark_shares * bar.dividend
        if detailed and distribution:
            trades.append({"date": bar.date, "side": "DIVIDEND", "reason": "ex-date cash approximation",
                           "shares": shares, "price": bar.dividend, "amount": distribution,
                           "fee": 0.0, "cash": cash, "signal_date": None})
        target *= 1 + params.va_pct / 100
        just_captured = False

        sell_quantity = shares if pending["capture"] else min(shares, pending["sell_shares"])
        if sell_quantity > 1e-12:
            sell_price = bar.open * (1 - slippage)
            gross = sell_quantity * sell_price
            sold_basis = basis * sell_quantity / shares
            reason = "capture: full exit" if pending["capture"] else "VA: excess above target"
            if gross <= params.fee:
                skipped_orders += 1
            elif params.no_loss_sales and gross - params.fee + 1e-9 < sold_basis:
                held_sales += 1
                if detailed:
                    trades.append({"date": bar.date, "side": "HOLD",
                                   "reason": f"no-loss rule: {reason} would sell below average cost",
                                   "shares": sell_quantity, "price": sell_price, "amount": gross,
                                   "fee": 0.0, "cash": cash, "signal_date": pending["signal_date"]})
            else:
                cash += gross - params.fee
                realized_gain += gross - params.fee - sold_basis
                losing_sales += gross - params.fee + 1e-9 < sold_basis
                shares = max(0.0, shares - sell_quantity)
                basis = basis - sold_basis if shares > 1e-12 else 0.0
                trade_count += 1
                if detailed:
                    trades.append({"date": bar.date, "side": "SELL", "reason": reason,
                                   "shares": sell_quantity, "price": sell_price, "amount": gross,
                                   "fee": params.fee, "cash": cash, "signal_date": pending["signal_date"]})
                if pending["capture"]:
                    capture_count += 1
                    just_captured = True
                    cycle_start = cash
                    target = 0.0

        # Full exits do not stop trading for the day. Size the same-session
        # DCA buy from actual net exit cash, including costs and distributions.
        # Fractional shares and immediate cash reuse are simulation assumptions.
        requested_buy = cycle_start * params.dca_pct / 100 if just_captured else pending["buy_dollars"]
        notional = min(requested_buy, max(0.0, cash - params.fee))
        if notional > 1e-9:
            buy_price = bar.open * (1 + slippage)
            quantity = notional / buy_price
            shares += quantity
            basis += notional + params.fee
            cash = max(0.0, cash - notional - params.fee)
            target += notional
            trade_count += 1
            if detailed:
                trades.append({"date": bar.date, "side": "BUY",
                               "reason": "post-exit DCA" if just_captured else pending["reason"],
                               "shares": quantity, "price": buy_price, "amount": notional,
                               "fee": params.fee, "cash": cash, "signal_date": pending["signal_date"]})

        invested = shares * bar.open
        total = cash + invested
        peak = max(peak, total)
        drawdown = max(drawdown, (peak - total) / peak * 100)
        if basis > 1e-9:
            worst_unrealized = max(worst_unrealized, (basis - invested) / basis * 100)
        if detailed:
            equity.append({"date": bar.date, "equity": total, "cash": cash, "invested": invested,
                           "cost_basis": basis,
                           "benchmark": benchmark_cash + benchmark_shares * bar.open,
                           "va_target": target, "cycle_start": cycle_start})

        # Only today's OPEN and the accounting ledger enter tomorrow's plan.
        capture = shares > 1e-12 and total >= cycle_start * (1 + params.capture_pct / 100)
        excess = max(0.0, invested - target)
        sell_shares = shares if capture else min(shares, excess / bar.open)
        pending = {"buy_dollars": cycle_start * params.dca_pct / 100,
                   "sell_shares": sell_shares, "capture": capture,
                   "signal_date": bar.date, "reason": "daily DCA"}

    planned_gross = pending["sell_shares"] * bars[-1].open * (1 - slippage)
    planned_basis = basis * pending["sell_shares"] / shares if shares > 1e-12 else 0.0
    # Estimated at the latest open; the actual fill price decides at execution.
    sale_held = (params.no_loss_sales and pending["sell_shares"] > 1e-12 and planned_gross > params.fee
                 and planned_gross - params.fee + 1e-9 < planned_basis)
    expected_sell_net = (max(0.0, planned_gross - params.fee)
                         if pending["sell_shares"] > 1e-12 and not sale_held else 0.0)
    estimated_post_exit_cash = cash + expected_sell_net
    capture_fill_expected = pending["capture"] and expected_sell_net > 0
    requested_buy = (estimated_post_exit_cash * params.dca_pct / 100
                     if capture_fill_expected else pending["buy_dollars"])
    buy_basis = ("estimated post-exit cash; recomputed from actual net proceeds at fill"
                 if capture_fill_expected else
                 "cycle-start equity; the no-loss rule would hold the exit at the reference price"
                 if pending["capture"] and sale_held else
                 "cycle-start equity; estimated exit proceeds cannot cover the sell fee"
                 if pending["capture"] else "cycle-start equity")
    estimated_buy = min(requested_buy, max(0.0, estimated_post_exit_cash - params.fee))
    can_buy = estimated_buy > 1e-9
    action = ("capture" if pending["capture"] and not sale_held else
              "buy_and_trim" if expected_sell_net and can_buy else "trim" if expected_sell_net
              else "buy" if can_buy else "hold")
    next_action = {**pending, "action": action, "as_of": bars[-1].date,
                   "reference_open": bars[-1].open,
                   "execution": "next available session open; hypothetical, not a broker order",
                   "available_cash": cash, "cycle_start_equity": cycle_start,
                   "buy_dollars": requested_buy,
                   "buy_dollars_basis": buy_basis,
                   "buy_dollars_contingent_on_exit": capture_fill_expected,
                   "buy_dollars_cash_capped": min(requested_buy, max(0.0, cash - params.fee)),
                   "estimated_sell_net": expected_sell_net,
                   "estimated_buy_dollars": estimated_buy,
                   "average_cost": basis / shares if shares > 1e-12 else None,
                   "no_loss_sales": params.no_loss_sales,
                   "sale_held_at_reference_price": sale_held,
                   "estimate_basis": "latest observed open, with configured slippage and fees; next fill price is unknown",
                   "note": "Requested DCA is cash-capped at fill; sale proceeds may fund it. After a full exit, DCA may buy in the same session using actual net exit cash. "
                           + ("The no-loss rule holds any sale whose net proceeds would be below the cost of the shares sold. "
                              if params.no_loss_sales else "Opening gaps can produce a losing sale. ")
                           + "Opening gaps, costs and settlement can change execution."}
    return {"params": asdict(params),
            "metrics": {"final_equity": total, "total_return_pct": (total / params.initial_cash - 1) * 100,
                        "max_drawdown_pct": drawdown, "trade_count": trade_count, "capture_count": capture_count,
                        "dividend_cash": dividend_total, "cash_interest": interest_total,
                        "skipped_orders": skipped_orders,
                        "held_loss_sales": held_sales, "losing_sales": losing_sales,
                        "realized_gain": realized_gain, "unrealized_gain": invested - basis,
                        "worst_unrealized_loss_pct": worst_unrealized,
                        "benchmark_return_pct": ((benchmark_cash + benchmark_shares * bars[-1].open) / params.initial_cash - 1) * 100},
            "equity": equity, "trades": trades, "next_action": next_action}
