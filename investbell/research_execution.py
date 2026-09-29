"""Execution sensitivities for research, never broker orders.

Zero delays/friction reproduces the strategy in engine.py. Delayed receipts
remain assets for valuation but cannot fund purchases. Session counts are
explicit hypothetical delays, not reconstructed settlement/payment calendars.
"""

from dataclasses import asdict, dataclass
from datetime import date
import math
from statistics import mean, stdev

from .engine import Parameters, number, validate_bars, validate_cash_rates


@dataclass(frozen=True)
class ExecutionAssumptions:
    settlement_sessions: int = 0
    dividend_delay_sessions: int = 0
    post_sale_buy_bps: float = 0.0

    def __post_init__(self):
        for name, maximum in (("settlement_sessions", 10), ("dividend_delay_sessions", 90)):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 0 and {maximum}.")
        object.__setattr__(self, "post_sale_buy_bps",
                           number(self.post_sale_buy_bps, "post_sale_buy_bps", 0, 500))


def path_metrics(points, initial_cash):
    """Opening-value risk measures; not intraday loss estimates."""
    peak, drawdown = initial_cash, 0.0
    changes, exposures = [], []
    previous = initial_cash
    for point in points:
        value = point["equity"]
        peak = max(peak, value)
        drawdown = max(drawdown, 100 * (peak - value) / peak)
        changes.append(value / previous - 1 if previous else 0.0)
        exposures.append(point["invested"] / value if value else 0.0)
        previous = value
    elapsed = (date.fromisoformat(points[-1]["date"]) - date.fromisoformat(points[0]["date"])).days
    final = points[-1]["equity"]
    # Short windows have no meaningful annualized estimate. Avoid numeric
    # overflow from annualizing a one-session shock.
    annualized = ((final / initial_cash) ** (365.25 / elapsed) - 1) * 100 if elapsed >= 365 else None
    return {"initial_cash": initial_cash, "final_equity": final,
            "total_return_pct": (final / initial_cash - 1) * 100,
            "annualized_return_pct": annualized,
            "max_opening_drawdown_pct": drawdown,
            "worst_observed_session_return_pct": min(changes) * 100,
            "annualized_open_return_volatility_pct": stdev(changes[1:]) * math.sqrt(252) * 100 if len(changes) > 2 else None,
            "average_invested_pct": mean(exposures) * 100,
            "max_invested_pct": max(exposures) * 100,
            "minimum_spendable_cash": min(point["cash"] for point in points),
            "sessions_without_spendable_cash": sum(point["cash"] <= 1e-9 for point in points),
            "final_receivables": points[-1].get("receivables", 0.0)}


def simulate_execution(bars, params: Parameters, assumptions=None, *, policy="strategy", cash_rates=None):
    """Run fixed rules with explicit cash availability and fill sensitivities.

    Policies are strategy, dca_va, simple_dca, and buy_and_hold. The dca_va
    policy keeps VA trims but explicitly disables capture exits and cycle
    resets. Both DCA comparisons retain the original fixed purchase budget.
    A full strategy exit resets its budget from net liquidation equity
    (including owned cash receivables).
    Purchases are capped by settled cash; same-session reentry remains allowed
    whenever cash can fund it. Extra post-sale friction models a separate buy
    price, not a claim that the true intraday price is known. The no-loss rule
    and optional cash interest match engine.simulate.
    """
    if policy not in {"strategy", "dca_va", "simple_dca", "buy_and_hold"}:
        raise ValueError("Unknown research policy.")
    assumptions = assumptions or ExecutionAssumptions()
    bars = validate_bars(bars)
    cash_rates = validate_cash_rates(cash_rates, bars)
    cash, shares, target, basis = params.initial_cash, 0.0, 0.0, 0.0
    cycle_start = cash
    due, equity, trades = [], [], []
    pending_sell, pending_capture = 0.0, False
    captures = skipped = held = losing = 0
    dividends = fees = slippage_cost = interest = 0.0
    slippage = params.slippage_bps / 10000

    for index, bar in enumerate(bars):
        if cash_rates and index:
            interest += cash * cash_rates[index]
            cash *= 1 + cash_rates[index]
        cash += sum(amount for session, amount in due if session <= index)
        due = [(session, amount) for session, amount in due if session > index]
        distribution = shares * bar.dividend
        dividends += distribution
        if distribution:
            if assumptions.dividend_delay_sessions:
                due.append((index + assumptions.dividend_delay_sessions, distribution))
            else:
                cash += distribution
        target *= 1 + params.va_pct / 100
        just_sold = just_captured = False
        sell_quantity = shares if pending_capture else min(shares, pending_sell)
        if sell_quantity > 1e-12:
            sell_price = bar.open * (1 - slippage)
            gross = sell_quantity * sell_price
            sold_basis = basis * sell_quantity / shares
            if gross > params.fee and params.no_loss_sales and gross - params.fee + 1e-9 < sold_basis:
                held += 1
            elif gross > params.fee:
                proceeds = gross - params.fee
                losing += proceeds + 1e-9 < sold_basis
                if assumptions.settlement_sessions:
                    due.append((index + assumptions.settlement_sessions, proceeds))
                else:
                    cash += proceeds
                shares = max(0.0, shares - sell_quantity)
                basis = basis - sold_basis if shares > 1e-12 else 0.0
                fees += params.fee
                slippage_cost += sell_quantity * (bar.open - sell_price)
                just_sold = True
                trades.append({"date": bar.date, "side": "SELL", "shares": sell_quantity,
                               "price": sell_price, "amount": gross, "fee": params.fee,
                               "capture": pending_capture})
                if pending_capture:
                    captures += 1
                    just_captured = True
                    cycle_start = cash + sum(amount for _, amount in due)
                    target = 0.0
            else:
                skipped += 1

        requested = (params.initial_cash if index == 0 else 0.0) if policy == "buy_and_hold" else cycle_start * params.dca_pct / 100
        notional = min(requested, max(0.0, cash - params.fee))
        if notional > 1e-9:
            buy_price = bar.open * (1 + slippage)
            if just_sold:
                buy_price *= 1 + assumptions.post_sale_buy_bps / 10000
            quantity = notional / buy_price
            shares += quantity
            basis += notional + params.fee
            cash = max(0.0, cash - notional - params.fee)
            target += notional
            fees += params.fee
            slippage_cost += quantity * (buy_price - bar.open)
            trades.append({"date": bar.date, "side": "BUY", "shares": quantity,
                           "price": buy_price, "amount": notional, "fee": params.fee,
                           "post_exit": just_captured, "post_sale": just_sold})

        invested = shares * bar.open
        receivables = sum(amount for _, amount in due)
        total = cash + receivables + invested
        equity.append({"date": bar.date, "equity": total, "cash": cash,
                       "receivables": receivables, "invested": invested,
                       "va_target": target, "cycle_start": cycle_start})
        pending_capture = (policy == "strategy" and shares > 1e-12
                           and total >= cycle_start * (1 + params.capture_pct / 100))
        pending_sell = (shares if pending_capture else min(shares, max(0.0, invested - target) / bar.open)) if policy in {"strategy", "dca_va"} else 0.0

    metrics = path_metrics(equity, params.initial_cash)
    metrics.update(trade_count=len(trades), capture_count=captures,
                   va_sell_count=sum(trade["side"] == "SELL" and not trade["capture"] for trade in trades),
                   distribution_entitlement=dividends, fees_paid=fees,
                   gross_traded_notional=sum(trade["amount"] for trade in trades),
                   modeled_slippage_cost=slippage_cost,
                   modeled_execution_cost=fees + slippage_cost,
                   skipped_sell_orders=skipped, held_loss_sales=held, losing_sales=losing,
                   cash_interest=interest)
    return {"policy": policy, "parameters": asdict(params), "execution": asdict(assumptions),
            "metrics": metrics, "equity": equity, "trades": trades}
