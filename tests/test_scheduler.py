from datetime import datetime, timezone
import io
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from investbell import daily, notify
from investbell.engine import Parameters
from investbell.scheduler import (build_command, next_run, parse_args, report_summary,
                                 run_report, run_scheduler, wait_until)


def write_sample_report(directory, *, action="hold", fitted=True, simple_dca=True, held=False):
    metrics = {"total_return_pct": 108.27, "benchmark_return_pct": 113.76}
    if simple_dca:
        metrics["simple_dca_final_equity"] = 21807.14
    report = {"range": {"start": "2021-01-04", "end": "2026-09-24"},
              "selected": {"params": {"initial_cash": 10000.0, "dca_pct": 5.0, "va_pct": 0.4, "capture_pct": 10.0},
                           "metrics": metrics,
                           "next_action": {"action": action, "estimated_buy_dollars": 1041.4,
                                           "estimated_sell_net": 312.9, "sale_held_at_reference_price": held}}}
    if fitted:
        report["fitted_model"] = {"next_retrain_at": "2028-09-24T18:30:46.455954+00:00"}
    path = Path(directory) / "2026-09-24-SPY-abc.json"
    path.write_text(json.dumps(report))
    return path


class SchedulerTests(unittest.TestCase):
    def test_next_run_honors_new_york_wall_clock_across_dst_and_weekends(self):
        cases = [
            ("2024-03-08T22:59:00+00:00", "2024-03-08T23:00:00+00:00"),
            ("2024-03-08T23:00:00+00:00", "2024-03-11T22:00:00+00:00"),
            ("2024-03-09T12:00:00+00:00", "2024-03-11T22:00:00+00:00"),
            ("2024-11-01T22:00:00+00:00", "2024-11-04T23:00:00+00:00"),
            ("2024-11-03T12:00:00+00:00", "2024-11-04T23:00:00+00:00"),
            ("2024-11-04T23:00:00+00:00", "2024-11-05T23:00:00+00:00"),
        ]
        for observed, expected in cases:
            with self.subTest(observed=observed):
                self.assertEqual(next_run(datetime.fromisoformat(observed)), datetime.fromisoformat(expected))
        with self.assertRaises(ValueError):
            next_run(datetime(2024, 1, 1))

    def test_command_preserves_configuration_and_leaves_end_dynamic(self):
        args = parse_args(["--symbol", "VTI", "--start", "2024-01-01",
                           "--data-dir", "/some path/data", "--dca-pct", "3",
                           "--va-pct", "0.2", "--capture-pct", "15", "--fee", "1"])
        command = build_command(args)
        self.assertEqual(command[:4], [sys.executable, "-u", "-m", "investbell.daily"])
        values = dict(zip(command[4::2], command[5::2]))
        self.assertEqual(values["--symbol"], "VTI")
        self.assertEqual(values["--data-dir"], "/some path/data")
        self.assertEqual(values["--report-dir"], "/some path/data/reports")
        self.assertEqual(values["--dca-pct"], "3.0")
        self.assertEqual(values["--va-pct"], "0.2")
        self.assertEqual(values["--capture-pct"], "15.0")
        self.assertEqual(values["--fee"], "1.0")
        self.assertEqual(values["--model-mode"], "fitted")
        self.assertEqual(values["--model-dir"], "/some path/data/models")
        self.assertNotIn("--end", command)

    def test_command_is_accepted_by_daily_parser(self):
        for flags, expected in (([], True), (["--no-loss-sales", "false"], False),
                                (["--no-loss-sales", "TRUE"], True)):
            with self.subTest(flags=flags):
                command = build_command(parse_args(["--start", "2024-01-01", *flags]))
                self.assertEqual(command[command.index("--no-loss-sales") + 1], str(expected).lower())
                args = daily.build_parser().parse_args(command[4:])
                self.assertIs(args.no_loss_sales, expected)
                self.assertEqual(args.dca_pct, Parameters().dca_pct)

    def test_boolean_parameters_reject_non_boolean_words(self):
        for value in ("1", "yes", "", "Tru"):
            with self.subTest(value=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                parse_args(["--no-loss-sales", value])
            self.assertEqual(caught.exception.code, 2)

    def test_fixed_mode_is_explicit_and_propagated(self):
        command = build_command(parse_args(["--model-mode", "fixed"]))
        self.assertEqual(command[command.index("--model-mode") + 1], "fixed")

    def test_invalid_configuration_fails_before_starting_child(self):
        for flags in (["--dca-pct", "nan"], ["--fee", "-1"], ["--start", "not-a-date"], ["--symbol", "PENNY"]):
            with self.subTest(flags=flags), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                parse_args(flags)
            self.assertEqual(caught.exception.code, 2)

    @patch("investbell.scheduler.log")
    def test_failed_startup_retries_then_resumes_strictly_future_schedule(self, _log):
        args = parse_args(["--start", "2024-01-01"])
        runner = Mock(side_effect=[False, True])
        waiter = Mock(side_effect=[True, False])
        clock = Mock(side_effect=[datetime(2024, 6, 5, 22, 0, tzinfo=timezone.utc),
                                  datetime(2024, 6, 5, 22, 15, tzinfo=timezone.utc)])
        run_scheduler(args, threading.Event(), runner=runner, waiter=waiter, clock=clock, alert=Mock())
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(waiter.call_args_list[0].args[0], datetime(2024, 6, 5, 22, 15, tzinfo=timezone.utc))
        self.assertEqual(waiter.call_args_list[1].args[0], datetime(2024, 6, 6, 22, 0, tzinfo=timezone.utc))

    @patch("investbell.scheduler.log")
    def test_nearby_scheduled_run_precedes_retry_delay(self, _log):
        finished = datetime(2024, 6, 5, 21, 55, tzinfo=timezone.utc)
        waiter = Mock(return_value=False)
        run_scheduler(parse_args(["--start", "2024-01-01"]), threading.Event(),
                      runner=Mock(return_value=False), waiter=waiter, clock=lambda: finished, alert=Mock())
        self.assertEqual(waiter.call_args.args[0], datetime(2024, 6, 5, 22, 0, tzinfo=timezone.utc))

    @patch("investbell.scheduler.log")
    def test_alerts_once_when_reports_start_failing_and_once_on_recovery(self, _log):
        alert = Mock()
        run_scheduler(parse_args(["--start", "2024-01-01"]), threading.Event(),
                      runner=Mock(side_effect=[True, False, False, False, True, True]),
                      waiter=Mock(side_effect=[True] * 5 + [False]), alert=alert,
                      clock=lambda: datetime(2024, 6, 5, 12, tzinfo=timezone.utc))
        self.assertEqual([call.args[0] for call in alert.call_args_list],
                         ["investBell: SPY report finished", "investBell: research report failed",
                          "investBell: research report recovered", "investBell: SPY report finished"])
        self.assertIn("retries every 15 minutes", alert.call_args_list[1].args[1])
        self.assertEqual(alert.call_args_list[0].kwargs, {"priority": "low", "tags": ("bar_chart",)})
        self.assertEqual(alert.call_args_list[2].kwargs, {"priority": "default", "tags": ("white_check_mark",)})
        self.assertTrue(alert.call_args_list[2].args[1].startswith("The report succeeded after failing."))

    def test_report_summary_describes_plan_returns_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_sample_report(directory)
            printed = "noise\n" + json.dumps({"report": str(path), "created": False, "as_of": "2026-09-24"}) + "\n"
            title, message = report_summary("SPY", printed)
            self.assertEqual(title, "investBell: SPY report for 2026-09-24")
            self.assertEqual(message.splitlines(), [
                "Next-session plan: hold. Hypothetical, in the $10,000 replay; no orders were sent.",
                "No new trading session since this report was first written.",
                "Replay since 2021-01-04: strategy +108.3%, buy and hold +113.8%, simple DCA +118.1%.",
                "Fitted model: DCA 5%, VA 0.4%, capture 10%; retrains 2028-09-24."])
            path = write_sample_report(directory, action="buy_and_trim", fitted=False, simple_dca=False, held=True)
            _title, message = report_summary("SPY", json.dumps({"report": str(path), "created": True, "as_of": "2026-09-24"}))
            self.assertEqual(message.splitlines(), [
                "Next-session plan: buy about $1,041 and VA trim about $313 (the no-loss rule would hold a sale). "
                "Hypothetical, in the $10,000 replay; no orders were sent.",
                "Replay since 2021-01-04: strategy +108.3%, buy and hold +113.8%.",
                "Fixed parameters: DCA 5%, VA 0.4%, capture 10%."])

    def test_report_summary_falls_back_when_the_report_cannot_be_read(self):
        fallback = ("investBell: SPY report finished", "The research report finished. No orders were sent.")
        for printed in ("", "not json", json.dumps({"report": "/missing/report.json", "created": True, "as_of": "x"}),
                        json.dumps(["unexpected"])):
            with self.subTest(printed=printed):
                self.assertEqual(report_summary("SPY", printed), fallback)

    def test_scheduler_captures_report_output_for_the_journal_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            line = json.dumps({"report": str(write_sample_report(directory)), "created": True, "as_of": "2026-09-24"})
            def runner(_command, _stop, output):
                output.write((line + "\n").encode())
                return True
            alert, stdout = Mock(), io.StringIO()
            with patch("investbell.scheduler.log"), redirect_stdout(stdout):
                run_scheduler(parse_args(["--start", "2024-01-01"]), threading.Event(), runner=runner,
                              waiter=Mock(return_value=False), alert=alert,
                              clock=lambda: datetime(2024, 6, 5, 12, tzinfo=timezone.utc))
        self.assertEqual(stdout.getvalue(), line + "\n")
        self.assertEqual(alert.call_args.args[0], "investBell: SPY report for 2026-09-24")
        self.assertEqual(alert.call_args.kwargs["priority"], "low")

    def test_startup_log_reports_whether_alerts_are_on(self):
        stop = threading.Event()
        stop.set()
        for url, expected in (("", "off"), ("https://ntfy.example/topic", "on"),
                              ("ntfy.sh/topic", "must be an http or https ntfy topic URL")):
            with self.subTest(url=url), patch.dict(os.environ, {notify.URL_VARIABLE: url}), \
                    patch("investbell.scheduler.log") as log:
                run_scheduler(parse_args(["--start", "2024-01-01"]), stop)
            self.assertEqual(log.call_args_list[0].args[0], "scheduler_started")
            self.assertIn(expected, log.call_args_list[0].kwargs["alerts"])

    def test_wait_is_bounded_interruptible_and_rechecks_wall_clock(self):
        stop = Mock()
        stop.is_set.return_value = False
        due = datetime(2024, 6, 5, 22, 0, tzinfo=timezone.utc)
        clock = Mock(side_effect=[datetime(2024, 6, 5, 21, 0, tzinfo=timezone.utc), due])
        self.assertTrue(wait_until(due, stop, clock=clock))
        stop.wait.assert_called_once_with(60)
        stopped = threading.Event()
        stopped.set()
        self.assertFalse(wait_until(due, stopped))

    @patch("investbell.scheduler.log")
    def test_report_exit_status_and_launch_failure_are_retryable(self, _log):
        stop = threading.Event()
        for returncode in (0, 1):
            child = Mock()
            child.poll.return_value = returncode
            with self.subTest(returncode=returncode), patch("investbell.scheduler.subprocess.Popen", return_value=child):
                self.assertEqual(run_report(["report-command"], stop), returncode == 0)
                child.terminate.assert_not_called()
        with patch("investbell.scheduler.subprocess.Popen", side_effect=OSError("temporarily unavailable")):
            self.assertFalse(run_report(["report-command"], stop))
        child = Mock()
        child.poll.return_value = 0
        output = io.BytesIO()
        with patch("investbell.scheduler.subprocess.Popen", return_value=child) as popen:
            self.assertTrue(run_report(["report-command"], stop, output=output))
        self.assertIs(popen.call_args.kwargs["stdout"], output)

    @patch("investbell.scheduler.log")
    def test_timed_out_report_is_terminated_and_killed_if_necessary(self, _log):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("report-command", 5), -9]
        with patch("investbell.scheduler.subprocess.Popen", return_value=child):
            self.assertFalse(run_report(["report-command"], threading.Event(), timeout=0))
        child.terminate.assert_called_once()
        child.kill.assert_called_once()

    @patch("investbell.scheduler.log")
    def test_shutdown_interrupts_active_report_and_skips_followup_wait(self, _log):
        stop = threading.Event()
        child = Mock()
        child.poll.return_value = None
        def start_child(*_args, **_kwargs):
            stop.set()
            return child
        with patch("investbell.scheduler.subprocess.Popen", side_effect=start_child):
            self.assertFalse(run_report(["report-command"], stop))
        child.terminate.assert_called_once()
        waiter = Mock()
        run_scheduler(parse_args(["--start", "2024-01-01"]), stop, waiter=waiter)
        waiter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
