"""Run daily research reports at startup and weekdays at 18:00 New York time.

No broker orders are sent. Failed or timed-out reports retry after 15 minutes;
the daily command retains responsibility for completed-session data and
idempotent output. When INVESTBELL_NTFY_URL is set, the first failure and the
following recovery each send one alert, and every successful report sends a
quiet summary, so a missing evening message means the server needs a look.
This process is suitable as a container's main command.
"""

import argparse
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time as monotonic_time
from zoneinfo import ZoneInfo

from . import notify
from .daily import add_parameter_arguments
from .data import completed_end, parse_range
from .engine import Parameters, SYMBOLS


NEW_YORK = ZoneInfo("America/New_York")
RETRY_SECONDS = 15 * 60
REPORT_TIMEOUT_SECONDS = 10 * 60


def utc_now():
    return datetime.now(timezone.utc)


def log(event, **fields):
    print(json.dumps({"time": utc_now().isoformat(), "event": event, **fields}), flush=True)


def next_run(now):
    """Return the strictly future weekday 18:00 ET occurrence, in UTC.

    Construct each local wall-clock occurrence separately, so DST transitions
    do not turn an 18:00 schedule into 17:00 or 19:00.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("A timezone-aware datetime is required.")
    local_day = now.astimezone(NEW_YORK).date()
    for offset in range(8):
        day = local_day + timedelta(days=offset)
        due = datetime.combine(day, time(18), tzinfo=NEW_YORK).astimezone(timezone.utc)
        if day.weekday() < 5 and due > now:
            return due
    raise RuntimeError("Could not determine the next weekday report.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=SYMBOLS, default="SPY")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--report-dir", help="defaults to DATA_DIR/reports")
    parser.add_argument("--model-dir", help="defaults to DATA_DIR/models")
    parser.add_argument("--model-mode", choices=("fitted", "fixed"), default="fitted")
    add_parameter_arguments(parser)
    args = parser.parse_args(argv)
    try:
        parse_range(args.start, completed_end().isoformat())
        Parameters(**{name: getattr(args, name) for name in Parameters.__dataclass_fields__})
    except ValueError as error:
        parser.error(str(error))
    if args.report_dir is None:
        args.report_dir = str(Path(args.data_dir) / "reports")
    if args.model_dir is None:
        args.model_dir = str(Path(args.data_dir) / "models")
    return args


def build_command(args):
    # No fixed --end: a fresh daily process determines the current data cutoff.
    command = [sys.executable, "-u", "-m", "investbell.daily",
               "--symbol", args.symbol, "--start", args.start,
               "--data-dir", args.data_dir, "--report-dir", args.report_dir,
               "--model-dir", args.model_dir, "--model-mode", args.model_mode]
    for name in Parameters.__dataclass_fields__:
        value = getattr(args, name)
        command.extend(["--" + name.replace("_", "-"), str(value).lower() if isinstance(value, bool) else str(value)])
    return command


def stop_child(child):
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def run_report(command, stop, *, timeout=REPORT_TIMEOUT_SECONDS, output=None):
    """Return success, keeping shutdown responsive; stdout goes to output if given."""
    if stop.is_set():
        return False
    try:
        child = subprocess.Popen(command, start_new_session=True, stdout=output)
    except OSError as error:
        log("report_start_failed", error=str(error))
        return False
    deadline = monotonic_time.monotonic() + timeout
    try:
        while True:
            returncode = child.poll()
            if returncode is not None:
                log("report_finished", returncode=returncode, orders_sent=0)
                return returncode == 0
            remaining = deadline - monotonic_time.monotonic()
            if remaining <= 0:
                log("report_timed_out", timeout_seconds=timeout)
                return False
            if stop.wait(min(1, remaining)):
                log("report_interrupted")
                return False
    finally:
        stop_child(child)


def wait_until(due, stop, *, clock=utc_now):
    while not stop.is_set():
        remaining = (due - clock()).total_seconds()
        if remaining <= 0:
            return True
        # Recheck wall time after clock corrections; SIGTERM wakes immediately.
        stop.wait(min(60, remaining))
    return False


PLANS = {"capture": "full exit (capture), then DCA buys again",
         "buy_and_trim": "buy about {buy} and VA trim about {sell}",
         "trim": "VA trim about {sell}", "buy": "buy about {buy}", "hold": "hold"}


def report_summary(symbol, printed):
    """Title and message for a finished report, from the line the report command prints."""
    fallback = (f"investBell: {symbol} report finished", "The research report finished. No orders were sent.")
    try:
        line = json.loads(printed.strip().splitlines()[-1])
        report = json.loads(Path(line["report"]).read_text())
        plan, params, metrics = (report["selected"][key] for key in ("next_action", "params", "metrics"))
        initial = params["initial_cash"]
        text = PLANS[plan["action"]].format(buy=f"${plan['estimated_buy_dollars']:,.0f}",
                                            sell=f"${plan['estimated_sell_net']:,.0f}")
        if plan.get("sale_held_at_reference_price"):
            text += " (the no-loss rule would hold a sale)"
        lines = [f"Next-session plan: {text}. Hypothetical, in the ${initial:,.0f} replay; no orders were sent."]
        if not line["created"]:
            lines.append("No new trading session since this report was first written.")
        returns = f"strategy {metrics['total_return_pct']:+.1f}%, buy and hold {metrics['benchmark_return_pct']:+.1f}%"
        if "simple_dca_final_equity" in metrics:
            returns += f", simple DCA {(metrics['simple_dca_final_equity'] / initial - 1) * 100:+.1f}%"
        lines.append(f"Replay since {report['range']['start']}: {returns}.")
        settings = f"DCA {params['dca_pct']:g}%, VA {params['va_pct']:g}%, capture {params['capture_pct']:g}%"
        model = report.get("fitted_model")
        lines.append(f"Fitted model: {settings}; retrains {model['next_retrain_at'][:10]}." if model
                     else f"Fixed parameters: {settings}.")
        return f"investBell: {symbol} report for {line['as_of']}", "\n".join(lines)
    except (IndexError, KeyError, TypeError, ValueError, OSError):
        return fallback


def alerts_status():
    try:
        return "on" if notify.topic_url() else "off"
    except ValueError as error:
        return str(error)


def run_scheduler(args, stop, *, clock=utc_now, runner=run_report, waiter=wait_until, alert=notify.send):
    command = build_command(args)
    log("scheduler_started", schedule="weekdays 18:00 America/New_York",
        startup_report=True, retry_seconds=RETRY_SECONDS, alerts=alerts_status(), orders_sent=0)
    failing = False
    while not stop.is_set():
        log("report_started", symbol=args.symbol)
        with tempfile.TemporaryFile() as output:
            success = runner(command, stop, output=output)
            output.seek(0)
            printed = output.read().decode(errors="replace")
        # The report prints a one-line summary; keep it in the journal.
        print(printed, end="", flush=True)
        if stop.is_set():
            break
        # One alert when reports start failing and one when they recover, not one per retry.
        if not success and not failing:
            alert("investBell: research report failed",
                  f"The {args.symbol} research report failed. The scheduler retries every "
                  f"{RETRY_SECONDS // 60} minutes and sends one message when a report succeeds again."
                  "\n\nInspect: journalctl --user -u investbell-scheduler -n 50")
        elif success:
            title, message = report_summary(args.symbol, printed)
            if failing:
                alert("investBell: research report recovered", "The report succeeded after failing.\n\n" + message,
                      priority="default", tags=("white_check_mark",))
            else:
                # A quiet daily message: if one is missing on a weekday evening, something is wrong.
                alert(title, message, priority="low", tags=("bar_chart",))
        failing = not success
        finished = clock()
        due = next_run(finished)
        if not success:
            due = min(due, finished + timedelta(seconds=RETRY_SECONDS))
        log("report_scheduled", due=due.isoformat(), reason="daily" if success else "retry")
        if not waiter(due, stop):
            break
    log("scheduler_stopped", orders_sent=0)


def main(argv=None):
    args = parse_args(argv)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: stop.set())
    run_scheduler(args, stop)


if __name__ == "__main__":
    main()
