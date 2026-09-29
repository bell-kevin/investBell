"""Read-only review of prospectively recorded broker-paper observations.

This command never initializes an account, contacts a broker, or approves live
execution. Local journals are evidence of recording, not independent attestation.
"""

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3

from .calendar import CalendarUnavailable, NEW_YORK, _calendar, expected_sessions, latest_completed_session
from .engine import Bar, Parameters
from .research import canonical_digest, write_new_json
from .research_execution import path_metrics, simulate_execution


TERMINAL = {"filled", "canceled", "expired", "rejected"}


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Evidence timestamps must include a timezone.")
    return parsed.astimezone(timezone.utc)


def numeric(value):
    if isinstance(value, bool):
        raise ValueError("Boolean evidence value is not a number.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Nonfinite evidence value.")
    return value


def read_journal(path):
    """Take a consistent read-only SQLite snapshot, without constructing Ledger."""
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"state", "orders", "events", "observations"}.issubset(tables):
            raise ValueError("Ledger lacks the prospective observation schema; old order history cannot backfill it.")
        state = connection.execute("SELECT payload FROM state WHERE id=1").fetchone()
        if state is None:
            raise ValueError("Paper account has not been initialized.")
        return {"state": json.loads(state[0]),
                "orders": [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY created")],
                "events": [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY id")],
                "observations": [dict(row) for row in connection.execute("SELECT * FROM observations ORDER BY id")]}


def _empty_report(now):
    return {"report_version": "paper-evidence-review-v1", "created_at": now.isoformat(),
            "status": "insufficient_evidence", "live_ready": False,
            "scope": "Local prospective paper journal review; no live approval and no independent broker verification.",
            "issues": [], "observed_sessions": 0,
            "limitations": [
                "Local journal timestamps and hashes are not independently attested and can be rewritten by a filesystem administrator.",
                "Quote observations occur after the opening print; strategy valuations are recorded bid marks, not exact opening valuations or continuous intraday risk measurements.",
                "Benchmarks are synthetic fills at observed reference opens with 5 basis points of slippage and zero fees; strategy fills and costs are actual recorded paper results.",
                "The journal lacks a complete distribution/corporate-action history for matched total-return benchmarks. Benchmark comparisons are provisional and require review.",
                "This read-only review cannot confirm current broker balances, unrecorded external orders, or current broker reconciliation.",
                "A completed local window can require review or fail checks; it never certifies live readiness.",
            ]}


def review_journal(journal, *, now=None, current_execution_sha256=None):
    now = now or datetime.now(timezone.utc)
    now = timestamp(now)
    report = _empty_report(now)
    issues = report["issues"]
    state = journal["state"]
    plan = state.get("evidence_plan")
    if not isinstance(plan, dict):
        issues.append("No frozen prospective paper evidence plan. Historical account data cannot qualify.")
        return report
    required = {"created_at", "first_session", "min_sessions", "policy", "execution_sha256", "plan_sha256"}
    if not required.issubset(plan):
        issues.append("Frozen evidence plan is missing required fields.")
        return report
    plan_digest = canonical_digest({key: value for key, value in plan.items() if key != "plan_sha256"})
    if plan_digest != plan["plan_sha256"]:
        issues.append("Frozen evidence plan digest mismatch.")
        return report
    registered = timestamp(plan["created_at"])
    first = date.fromisoformat(plan["first_session"])
    minimum = plan["min_sessions"]
    if type(minimum) is not int or minimum < 252 or minimum > 2520:
        issues.append("Registered minimum window must be between 252 and 2520 sessions.")
        return report
    if registered > now or first <= registered.astimezone(NEW_YORK).date():
        issues.append("Registration must precede the first evaluated session and cannot be future-dated.")
        return report
    sessions = expected_sessions(first, first + timedelta(days=minimum * 2 + 366))[:minimum]
    if len(sessions) != minimum or sessions[0] != first.isoformat():
        issues.append("Registered first session or minimum exchange window is invalid.")
        return report
    if plan.get("min_end_session", sessions[-1]) != sessions[-1]:
        issues.append("Registered window end differs from the current exchange calendar; explicit review is required.")
        return report
    completed = latest_completed_session(now).isoformat()
    report["window"] = {"first_session": sessions[0], "last_required_session": sessions[-1],
                        "required_sessions": minimum, "latest_completed_session": completed,
                        "elapsed": completed >= sessions[-1],
                        "selection": "First registered minimum window; later sessions cannot replace missing early evidence."}
    report["plan_sha256"] = plan_digest
    policy = plan["policy"]
    from .paper import Policy
    Policy(**policy)  # Verify bounds before using risk limits or allocations.
    policy_digest = canonical_digest(policy)
    fingerprint_ok = (state.get("policy") == policy
                      and state.get("execution_sha256") == plan["execution_sha256"]
                      and (current_execution_sha256 is None or current_execution_sha256 == plan["execution_sha256"]))
    if not fingerprint_ok:
        issues.append("Current policy or execution code differs from the frozen evidence plan.")
    if current_execution_sha256 is None:
        issues.append("Current execution code fingerprint was not independently recomputed for this review.")
    initial = numeric(policy["capital_limit"])
    by_session = defaultdict(list)
    all_session_marks = defaultdict(list)
    malformed, previous_stamp = [], None
    all_points = []
    policy_breaches = []
    peak = initial
    worst_unrealized = None
    for row in journal["observations"]:
        try:
            recorded = timestamp(row["timestamp"])
            observation = json.loads(row["payload"])
            session = observation["session"]
            if recorded < registered or recorded > now or (previous_stamp is not None and recorded < previous_stamp):
                raise ValueError("Observation timestamps are backdated, future-dated or out of append order.")
            previous_stamp = recorded
            if session < sessions[0]:
                raise ValueError("Observation predates the registered first session.")
            if recorded.astimezone(NEW_YORK).date().isoformat() != session:
                raise ValueError("Observation was not recorded during its claimed session.")
            if (observation.get("plan_sha256") != plan_digest or
                    observation.get("execution_sha256") != plan["execution_sha256"] or
                    observation.get("policy_sha256") != policy_digest):
                raise ValueError("Observation code, policy or plan fingerprint changed.")
            if observation.get("feed") != policy["feed"]:
                raise ValueError("Observation feed differs from the frozen policy.")
            quoted = timestamp(observation["quote_timestamp"])
            if not -2 <= (recorded - quoted).total_seconds() <= policy["max_quote_age_seconds"]:
                raise ValueError("Observation quote was stale or future-dated when recorded.")
            values = {key: numeric(observation[key]) for key in ("reference_open", "bid", "ask", "equity", "cash", "shares")}
            if values["reference_open"] <= 0 or not 0 < values["bid"] <= values["ask"] or values["shares"] < -1e-8:
                raise ValueError("Invalid observation price or position.")
            if abs(values["equity"] - values["cash"] - values["shares"] * values["bid"]) > .02:
                raise ValueError("Observation equity does not reconcile to allocated cash and marked shares.")
            cal = _calendar(date.fromisoformat(session).year, date.fromisoformat(session).year)
            opened = cal.session_open(session).to_pydatetime()
            closed = cal.session_close(session).to_pydatetime()
            if not opened + timedelta(minutes=1) <= recorded < closed:
                raise ValueError("Observation timestamp is outside the documented post-open recording window.")
            observation.update(values)
            observation["recorded_at"] = recorded.isoformat()
            observation["minutes_after_open"] = (recorded - opened).total_seconds() / 60
            all_session_marks[session].append(observation)
            if session not in sessions:
                continue
            by_session[session].append(observation)
            point = {"date": session, "equity": values["equity"], "cash": values["cash"],
                     "invested": values["shares"] * values["bid"]}
            all_points.append(point)
            # Older plans did not record cost basis or apply the no-loss rule.
            if "cost_basis" in observation:
                basis = numeric(observation["cost_basis"])
                if basis < 0:
                    raise ValueError("Negative recorded cost basis.")
                if basis > 1e-9:
                    loss = max(0.0, (basis - point["invested"]) / basis * 100)
                    worst_unrealized = max(worst_unrealized or 0.0, loss)
            peak = max(peak, values["equity"])
            if values["cash"] < -.01:
                policy_breaches.append(f"{session}: negative allocated cash")
            if values["equity"] <= peak * (1 - policy["max_drawdown_pct"] / 100):
                policy_breaches.append(f"{session}: drawdown reached policy limit")
        except (ValueError, KeyError, TypeError, CalendarUnavailable) as error:
            malformed.append({"id": row.get("id"), "reason": str(error)})

    missing = [session for session in sessions if session not in by_session]
    late_starts = [session for session, observations in by_session.items() if observations[0]["minutes_after_open"] > 15]
    unfinished = [session for session, observations in by_session.items()
                  if not any(observation.get("phase") == "session_complete" for observation in observations)]
    inconsistent_opens = [session for session, observations in by_session.items()
                          if len({observation["reference_open"] for observation in observations}) != 1]
    day_base = initial
    for session in sessions:
        if session not in by_session:
            continue
        observations = by_session[session]
        if min(o["equity"] for o in observations) <= day_base * (1 - policy["max_daily_loss_pct"] / 100):
            policy_breaches.append(f"{session}: daily loss reached policy limit")
        day_base = observations[-1]["equity"]
    report.update(observed_sessions=len(by_session), missing_sessions=missing,
                  malformed_observations=malformed, late_first_observation_sessions=late_starts,
                  unfinished_sessions=unfinished, inconsistent_open_sessions=inconsistent_opens)

    orders = journal["orders"]
    duplicate_ids = [client for client, count in Counter(row["client_id"] for row in orders).items() if count > 1]
    duplicate_broker_ids = [broker for broker, count in Counter(row.get("broker_id") for row in orders if row.get("broker_id")).items() if count > 1]
    unresolved = [row["client_id"] for row in orders if row["status"] not in TERMINAL]
    unsuccessful = [row["client_id"] for row in orders if row["status"] in TERMINAL - {"filled"}]
    order_errors, daily_turnover, missing_buy_marks = [], defaultdict(float), []
    for row in orders:
        try:
            payload = json.loads(row["payload"])
            created = timestamp(row["created"])
            if created < registered or created > now or created.astimezone(NEW_YORK).date().isoformat() != row["session"]:
                raise ValueError("Order timestamp is outside its registered session.")
            if row["session"] < sessions[0] or payload["client_order_id"] != row["client_id"] or payload["symbol"] != policy["symbol"]:
                raise ValueError("Order identity or registration mismatch.")
            quantity, filled = numeric(payload["qty"]), numeric(row["filled_qty"])
            value = numeric(row["filled_value"])
            if quantity <= 0 or filled < 0 or filled > quantity + 1e-8 or value < 0 or (filled > 0 and value <= 0):
                raise ValueError("Invalid cumulative order fills.")
            if row["status"] == "filled" and (abs(filled - quantity) > 1e-8 or not row.get("broker_id")):
                raise ValueError("Filled order lacks complete quantity or broker identity.")
            if payload["side"] == "buy":
                marks = [mark for mark in all_session_marks[row["session"]]
                         if timestamp(mark["recorded_at"]) <= created]
                latest = max(marks, key=lambda mark: timestamp(mark["recorded_at"])) if marks else None
                if latest is None or (created - timestamp(latest["recorded_at"])).total_seconds() > policy["max_quote_age_seconds"]:
                    missing_buy_marks.append(row["client_id"])
                else:
                    limit = numeric(payload["limit_price"])
                    if limit <= 0:
                        raise ValueError("Buy limit price must be positive.")
                    # The runner limits NEW BUY spending. Market appreciation
                    # after a fill may exceed this cap without a rule breach.
                    requested_position = latest["shares"] * latest["ask"] + quantity * limit
                    if requested_position > policy["max_position_value"] + .02:
                        policy_breaches.append(f"{row['session']}: new buy would exceed position purchase cap")
            risk = numeric(payload["risk_notional"])
            if risk > policy["max_order_notional"] + .01:
                policy_breaches.append(f"{row['session']}: order notional exceeded policy")
            daily_turnover[row["session"]] += risk
        except (ValueError, KeyError, TypeError) as error:
            order_errors.append({"client_id": row.get("client_id"), "reason": str(error)})
    policy_breaches += [f"{session}: daily notional exceeded policy" for session, value in daily_turnover.items()
                        if value > policy["max_daily_notional"] + .01]
    critical = [event for event in journal["events"] if event["level"].lower() == "critical"]
    cash_events = [event for event in journal["events"] if "cash adjustment" in event["message"].lower()]
    held_sales = [event for event in journal["events"] if event["message"].startswith("No-loss rule held")]
    filled_count = sum(row["status"] == "filled" and bool(row.get("broker_id"))
                       and row["session"] in sessions for row in orders)
    report["operational_audit"] = {"duplicate_client_ids": duplicate_ids, "duplicate_broker_ids": duplicate_broker_ids,
                                   "unresolved_orders": unresolved, "terminal_unfilled_or_partial_orders": unsuccessful,
                                   "order_errors": order_errors, "critical_events": critical,
                                   "buy_intents_without_contemporaneous_marks": missing_buy_marks,
                                   "reviewed_cash_events": cash_events,
                                   "no_loss_held_sales": held_sales,
                                   "policy_breaches": sorted(set(policy_breaches)), "currently_halted": state.get("halted"),
                                   "filled_broker_order_count": filled_count,
                                   "fingerprints_match": fingerprint_ok,
                                   "broker_reconciliation": "Only local recorded evidence; current broker comparison not performed."}
    if all_points:
        first_points = [{"date": session, "equity": by_session[session][0]["equity"],
                         "cash": by_session[session][0]["cash"],
                         "invested": by_session[session][0]["shares"] * by_session[session][0]["bid"]}
                        for session in sessions if session in by_session]
        final = all_points[-1]
        sampled_metrics = path_metrics(first_points, initial)
        sampled_metrics["max_sampled_drawdown_pct"] = sampled_metrics.pop("max_opening_drawdown_pct")
        sampled_metrics["annualized_sampled_return_volatility_pct"] = sampled_metrics.pop("annualized_open_return_volatility_pct")
        report["strategy"] = {"final_recorded_equity": final["equity"],
                              "total_return_pct": (final["equity"] / initial - 1) * 100,
                              "observed_quote_drawdown_pct": path_metrics(all_points, initial)["max_opening_drawdown_pct"],
                              "first_post_open_observation_metrics": sampled_metrics,
                              # Held sales leave losses unsold; report them beside each other.
                              "no_loss_sales": policy.get("no_loss_sales") is True,
                              "held_sales": len(held_sales),
                              "worst_unrealized_loss_pct": worst_unrealized,
                              "final_valuation": "Last recorded bid-marked observation in the selected window; not a closing or opening print."}
        bars = [Bar(session, by_session[session][0]["reference_open"]) for session in sessions if session in by_session]
        parameters = Parameters(initial_cash=initial, dca_pct=policy["dca_pct"], va_pct=policy["va_pct"],
                                capture_pct=policy["capture_pct"], slippage_bps=5, fee=0)
        benchmarks = {name: simulate_execution(bars, parameters, policy=name)["metrics"]
                      for name in ("buy_and_hold", "simple_dca")}
        report["modeled_reference_open_benchmarks"] = benchmarks
        report["provisional_return_comparison_pp"] = {name: report["strategy"]["total_return_pct"] - metrics["total_return_pct"]
                                                     for name, metrics in benchmarks.items()}
        report["comparison_qualification"] = "Different valuation timestamps and modeled costs; distributions are absent from benchmark inputs. These differences prevent an automatic performance pass/fail verdict."
    complete = report["window"]["elapsed"] and filled_count > 0 and not (missing or malformed or late_starts or unfinished or inconsistent_opens or missing_buy_marks)
    if not complete:
        issues.append("The elapsed, contiguous, contemporaneous observation window is incomplete or unverifiable.")
    else:
        failures = (not fingerprint_ok or duplicate_ids or duplicate_broker_ids or unresolved or unsuccessful
                    or order_errors or critical or policy_breaches or state.get("halted"))
        report["status"] = "failed_recorded_checks" if failures else "review_required"
        issues.append("Independent broker reconciliation and matched distribution/cost/valuation comparisons remain necessary; no automatic live approval.")
    return report


def build_report(path, *, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        from .paper import execution_digest
        return review_journal(read_journal(path), now=now, current_execution_sha256=execution_digest())
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, CalendarUnavailable) as error:
        report = _empty_report(timestamp(now))
        report["issues"].append(f"Evidence cannot be verified: {error}")
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default="data/paper.sqlite3")
    parser.add_argument("--output", help="Optional new JSON report path; never overwrites an existing file")
    args = parser.parse_args(argv)
    report = build_report(args.ledger)
    if args.output:
        try:
            write_new_json(args.output, report)
        except (ValueError, OSError) as error:
            parser.exit(2, f"Cannot write evidence report: {error}\n")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
