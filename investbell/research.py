"""Reproducible retrospective validation and prospective plan registration.

Reads an existing SQLite snapshot without refresh or network access. Historical
partitions are deliberately never called an unseen holdout: this dataset has
already been explored. Nothing here authorizes or places a broker order.
"""

import argparse
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from . import MODEL_VERSION
from .calendar import CalendarUnavailable, expected_sessions, latest_completed_session
from .engine import Bar, Parameters, SYMBOLS, simulate, validate_bars
from .research_execution import ExecutionAssumptions, simulate_execution


REPORT_VERSION = "retrospective-validation-v2"
PLAN_VERSION = "prospective-research-plan-v1"


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def code_identity():
    root = Path(__file__).parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("engine.py", "research.py", "research_execution.py", "calendar.py")}


def load_cached_snapshot(database, symbol, start, end, *, now=None):
    """Require one complete exact cached range; do not stitch adjustment eras."""
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported ETF.")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Cache validation needs a timezone-aware current time.")
    sessions = expected_sessions(start, end)
    if not sessions:
        raise ValueError("Cached research range contains no exchange sessions.")
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute(
            "SELECT fetched_at,digest,payload,adapter_version FROM snapshots WHERE symbol=? AND start=? AND end=?",
            (symbol, start, end)).fetchone()
    if row is None:
        raise ValueError("No exact cached snapshot for this symbol/start/exclusive end; no download attempted.")
    fetched_at, digest, payload, adapter = row
    downloaded_at = datetime.fromisoformat(fetched_at)
    if downloaded_at.tzinfo is None or downloaded_at.utcoffset() is None or downloaded_at > now:
        raise ValueError("Cached snapshot fetch timestamp must be timezone-aware and not in the future.")
    if hashlib.sha256(payload.encode()).hexdigest() != digest:
        raise ValueError("Cached snapshot checksum mismatch.")
    bars = validate_bars(Bar(**item) for item in json.loads(payload))
    if any(not start <= bar.date < end for bar in bars):
        raise ValueError("Cached bars fall outside the requested range.")
    actual = tuple(bar.date for bar in bars)
    if actual != sessions:
        missing = sorted(set(sessions) - set(actual))
        extra = sorted(set(actual) - set(sessions))
        raise ValueError(f"Cached research observations are incomplete or contain non-session dates: "
                         f"missing {len(missing)} exchange sessions; extra {len(extra)} dates.")
    if bars[-1].date > latest_completed_session(downloaded_at).isoformat():
        raise ValueError("Cached research observations include a session not completed when the snapshot was fetched.")
    return bars, {"kind": "cached_yahoo_snapshot", "symbol": symbol,
                  "requested_start": start, "requested_end_exclusive": end,
                  "observed_start": bars[0].date, "observed_end": bars[-1].date,
                  "snapshot_fetched_at": fetched_at, "snapshot_sha256": digest,
                  "adapter_version": adapter,
                  "calendar": "XNYS", "expected_session_count": len(sessions),
                  "exchange_completeness": "Every expected session present; no holiday or weekend rows.",
                  "freshness": "Historical artifact only; no current-session freshness certification."}


def summarize(bars, params, execution=None):
    outcomes = {policy: simulate_execution(bars, params, execution, policy=policy)["metrics"]
                for policy in ("simple_dca", "dca_va", "strategy", "buy_and_hold")}
    for added, baseline in (("dca_va", "simple_dca"), ("strategy", "dca_va"),
                            ("strategy", "simple_dca"), ("strategy", "buy_and_hold")):
        outcomes[f"{added}_minus_{baseline}_return_pp"] = outcomes[added]["total_return_pct"] - outcomes[baseline]["total_return_pct"]
    return {"start": bars[0].date, "end_inclusive": bars[-1].date,
            "observations": len(bars), **outcomes}


def synthetic_bars(prices):
    observations, day = [], date(2000, 1, 3)
    for price in prices:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        observations.append(Bar(day.isoformat(), price))
        day += timedelta(days=1)
    return observations


def stress_cases(params):
    cases = {
        "cash_exhaustion_then_50_percent_crash": [100] * 60 + [50] * 10,
        "capture_signal_then_downward_gap": [100] * 60 + [150, 25] + [25] * 8,
        "prolonged_decline": [100 * (0.99 ** i) for i in range(180)],
        "crash_and_partial_recovery": [100] * 60 + [40] * 20 + [70] * 40,
    }
    return [{"name": name, "source": "synthetic adverse scenario, not a probability estimate",
             "construction": {"prices": prices, "calendar": "synthetic weekdays; exchange holidays omitted"},
             "result": summarize(synthetic_bars(prices), params)} for name, prices in cases.items()]


def validate_history(bars, params, evaluation_start, provenance, *, now=None):
    bars = validate_bars(bars)
    if date.fromisoformat(evaluation_start).isoformat() != evaluation_start:
        raise ValueError("evaluation_start must be YYYY-MM-DD.")
    training = [bar for bar in bars if bar.date < evaluation_start]
    evaluation = [bar for bar in bars if bar.date >= evaluation_start]
    if len(training) < 20 or len(evaluation) < 20:
        raise ValueError("Training and later evaluation each need at least 20 observations.")
    # Guard against divergence between the reference and sensitivity engines.
    reference = simulate(bars, params, detailed=False)["metrics"]
    baseline = simulate_execution(bars, params)["metrics"]
    for ours, theirs in (("final_equity", "final_equity"),
                        ("max_opening_drawdown_pct", "max_drawdown_pct"),
                        ("trade_count", "trade_count"), ("capture_count", "capture_count")):
        if abs(baseline[ours] - reference[theirs]) > 1e-8 * max(1.0, abs(reference[theirs])):
            raise ValueError("Research baseline diverged from the reference strategy; validation stopped.")

    parameters = asdict(params)
    sensitivity = []
    bounds = {"dca_pct": (0.01, 100), "va_pct": (0, 5), "capture_pct": (0.1, 1000)}
    for name, (minimum, maximum) in bounds.items():
        original = getattr(params, name)
        values = [max(minimum, original * 0.75), min(maximum, original * 1.25)] if original else [0.05]
        for value in sorted(set(values) - {original}):
            candidate = replace(params, **{name: value})
            sensitivity.append({"changed": name, "value": value,
                                "result": summarize(evaluation, candidate)})

    costs = []
    for slip, fee in sorted({(params.slippage_bps, params.fee),
                             (min(500, max(10, params.slippage_bps * 2)), params.fee),
                             (min(500, max(25, params.slippage_bps * 5)), max(1, params.fee)),
                             (min(500, max(50, params.slippage_bps * 10)), max(1, params.fee))}):
        costs.append({"slippage_bps": slip, "fee": fee,
                      "result": summarize(evaluation, replace(params, slippage_bps=slip, fee=fee))})

    execution_cases = []
    for name, assumptions in (
        ("reference_immediate_cash", ExecutionAssumptions()),
        ("one_session_sale_delay_twenty_session_distribution_delay", ExecutionAssumptions(1, 20, 10)),
        ("two_session_sale_delay_forty_session_distribution_delay", ExecutionAssumptions(2, 40, 25)),
    ):
        execution_cases.append({"name": name, "assumptions": asdict(assumptions),
                                "result": summarize(evaluation, params, assumptions)})

    return {"report_version": REPORT_VERSION,
            "created_at": (now or datetime.now(timezone.utc)).isoformat(),
            "model_version": MODEL_VERSION, "code_sha256": code_identity(),
            "parameters": parameters, "data": provenance,
            "bars_sha256": canonical_digest([asdict(bar) for bar in bars]),
            "status": "research_only_not_live_approval",
            "validation_design": {
                "classification": "retrospective chronological split; previously explored history",
                "evaluation_start_requested": evaluation_start,
                "selection": "Parameters fixed by request. No optimization or winner selection.",
                "partition_state": "Each partition starts afresh with the same initial cash; no carried positions or training state.",
                "sensitivity_scope": "Later partition; exploratory results are not an untouched holdout.",
                "prospective_status": "No prospective observations or broker fills included.",
            },
            "comparison_design": {
                "policies": {
                    "simple_dca": "Fixed original DCA budget; no VA trims, capture exits or cycle resets.",
                    "dca_va": "Same DCA budget and VA target/sale rule as the strategy; capture exits and cycle resets explicitly disabled.",
                    "strategy": "DCA plus VA trims plus capture exits; actual net exit equity resets the cycle and DCA budget.",
                    "buy_and_hold": "Invest starting cash at the first open; retain holdings and accumulate distributions as cash.",
                },
                "funding": "Identical initial cash, dates and prices; no later external deposits, withdrawals or borrowing.",
                "costs": "Identical fee, slippage and receipt-delay assumptions within each comparison; different trades incur different total costs.",
                "parameters": "Shared fixed parameters; no separate fitting for the added policies. va_pct=0 is a zero-growth target, not a switch to disable VA.",
                "incremental_returns": "dca_va minus simple_dca measures adding VA; strategy minus dca_va measures adding capture and its cycle resets. Differences are percentage points of total return.",
                "execution_cost": "Modeled slippage cost is filled shares times the adverse difference from the observed open, including post-sale buy friction; execution cost adds order fees. Already reflected in net equity, not deducted again.",
            },
            "training": summarize(training, params),
            "later_retrospective_evaluation": summarize(evaluation, params),
            "full_history_descriptive": summarize(bars, params),
            "parameter_sensitivity": sensitivity, "cost_sensitivity": costs,
            "execution_sensitivity": execution_cases, "synthetic_stress": stress_cases(params),
            "limitations": [
                "No evidence here certifies live readiness or future profitability.",
                "Maximum drawdown uses daily opening valuations and misses intraday losses.",
                "One ETF and one historical sample; parameter and cost variants reuse this sample.",
                "No taxes, cash interest, order rejections, partial fills or broker constraints.",
                "Fractional shares and split-normalized share units remain assumptions.",
                "Distribution entitlement is valued on ex-date. Fixed session payment delays are sensitivities, not actual payable dates.",
                "Sale settlement delays are hypothetical session counts. Existing settled cash may still fund same-session reentry.",
                "Post-sale purchases use open times slippage times additional friction, a separate modeled fill rather than observed intraday prices.",
                "Cash receivables remain in equity while unavailable to buy; full exits reset from net liquidation equity including receivables.",
                "Buy-and-hold accumulates distributions as cash. Simple DCA buys the original fixed daily budget and never trims or captures.",
                "DCA plus VA retains immediate funded repurchases after trims; capture is disabled independently of its numeric threshold.",
                "Layer comparisons use the same fixed parameters, not independently optimized strategies; this retrospective sample cannot establish future improvement.",
                "No forced final liquidation, liquidation costs, or withdrawals. Synthetic stresses have no assigned probability.",
            ]}


def freeze_plan(report, *, prospective_start=None, now=None):
    """Create a tamper-evident research plan; never pretend old data were unseen."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Registration timestamp must include a timezone.")
    known_through = report["data"]["observed_end"]
    earliest = max(date.fromisoformat(known_through), now.astimezone(ZoneInfo("America/New_York")).date()) + timedelta(days=1)
    first = date.fromisoformat(prospective_start) if prospective_start else earliest
    if first < earliest:
        raise ValueError("Prospective start must be after registration day and all already observed history.")
    scheduled = expected_sessions(first, first + timedelta(days=400))
    if len(scheduled) < 252:
        raise ValueError("Cannot establish the minimum prospective session window.")
    plan = {"plan_version": PLAN_VERSION, "created_at": now.isoformat(),
            "prospective_start_not_before": first.isoformat(),
            "earliest_observation": "First actual completed session on or after the start date.",
            "status": "frozen_research_parameters_no_live_authorization",
            "parameters": report["parameters"], "symbol": report["data"]["symbol"],
            "model_version": report["model_version"], "code_sha256": report["code_sha256"],
            "known_data_through": known_through, "known_bars_sha256": report["bars_sha256"],
            "retrospective_report_sha256": canonical_digest(report),
            "evaluation": {"minimum_completed_sessions": 252,
                           "first_expected_session": scheduled[0],
                           "minimum_window_end_inclusive": scheduled[251],
                           "calendar": "XNYS schedule at registration; recheck announced closures before assessing the window.",
                           "comparators": ["buy_and_hold", "simple_dca"],
                           "measures": ["total_return_pct", "max_opening_drawdown_pct", "max_invested_pct", "fees_paid"],
                           "criteria_origin": "Project research-review gates; not user-specific financial risk limits or a statistical significance test.",
                           "pass_requires_all": [
                               "At least 252 completed exchange sessions beginning with the registered first session, with no missing observations.",
                               "Contemporaneously recorded prospective paper evidence for the full window, linked to this plan digest; a later historical replay does not qualify.",
                               "Strategy/model/parameter/execution hashes unchanged throughout the window.",
                               "Zero duplicate broker orders and zero unresolved cash, position, fill or distribution reconciliation differences.",
                               "Strategy net total return at least equal to both buy-and-hold and simple DCA on identical funding, dates and comparable costs.",
                               "Strategy maximum opening drawdown no greater than buy-and-hold on the same sessions.",
                               "Separately agreed account exposure, drawdown and cash limits were recorded before broker paper trading and never breached.",
                           ],
                           "incomplete": "Before the window ends, or without contemporaneous broker-paper evidence, status is insufficient_evidence; never pass.",
                           "fail": "A completed valid window missing any pass criterion fails research review. Do not select replacement parameters using that window and relabel it prospective.",
                           "acceptance": "Even passing these gates only qualifies for human review; no automatic live promotion and no proof of an edge.",
                           "anti_backfill": "Registration must predate the first evaluated session. Retain original creation timestamps, immutable plan digest and append-only paper records. Historical cache replays remain retrospective regardless of their evaluation-start date. This local hash is integrity evidence, not independent timestamp attestation.",
                           "changes": "Any parameter, code or execution change creates a new plan and restarts prospective observation; keep superseded plans."},
            "execution_assumptions": asdict(ExecutionAssumptions()),
            "scope": "Prospective research intent only; not a brokerage account, actual paper fills, or a claim that prior history was unseen."}
    plan["plan_sha256"] = canonical_digest(plan)
    return plan


def verify_plan(plan):
    if not isinstance(plan, dict) or plan.get("plan_version") != PLAN_VERSION:
        raise ValueError("Unrecognized prospective research plan.")
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if canonical_digest(payload) != plan.get("plan_sha256"):
        raise ValueError("Prospective research plan digest mismatch.")
    return True


def write_new_json(path, value):
    """Never silently replace a historical validation or frozen plan."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/market.sqlite3")
    parser.add_argument("--symbol", default="SPY", choices=SYMBOLS)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="Exclusive cached snapshot bound")
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--freeze-output")
    parser.add_argument("--prospective-start")
    for name, field in Parameters.__dataclass_fields__.items():
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=field.default)
    args = parser.parse_args(argv)
    try:
        if args.prospective_start and not args.freeze_output:
            raise ValueError("--prospective-start requires --freeze-output.")
        outputs = [Path(p).resolve() for p in (args.output, args.freeze_output) if p]
        if len(set(outputs)) != len(outputs) or any(p.exists() for p in outputs):
            raise ValueError("Choose distinct new output paths; existing artifacts cannot be overwritten.")
        bars, provenance = load_cached_snapshot(args.database, args.symbol, args.start, args.end)
        params = Parameters(**{name: getattr(args, name) for name in Parameters.__dataclass_fields__})
        report = validate_history(bars, params, args.evaluation_start, provenance)
        plan = freeze_plan(report, prospective_start=args.prospective_start) if args.freeze_output else None
        write_new_json(args.output, report)
        if plan:
            write_new_json(args.freeze_output, plan)
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError, CalendarUnavailable) as error:
        parser.exit(2, f"Research validation failed: {error}\n")
    print(json.dumps({"report": args.output, "frozen_plan": args.freeze_output,
                      "status": report["status"],
                      "later_simple_dca_return_pct": report["later_retrospective_evaluation"]["simple_dca"]["total_return_pct"],
                      "later_dca_va_return_pct": report["later_retrospective_evaluation"]["dca_va"]["total_return_pct"],
                      "later_strategy_return_pct": report["later_retrospective_evaluation"]["strategy"]["total_return_pct"],
                      "later_buy_and_hold_return_pct": report["later_retrospective_evaluation"]["buy_and_hold"]["total_return_pct"],
                      "later_va_increment_return_pp": report["later_retrospective_evaluation"]["dca_va_minus_simple_dca_return_pp"],
                      "later_capture_increment_return_pp": report["later_retrospective_evaluation"]["strategy_minus_dca_va_return_pp"]}, indent=2))


if __name__ == "__main__":
    main()
