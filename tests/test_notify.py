from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import os
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from investbell import notify


TOPIC = "https://ntfy.example/investbell-private-topic"


class Response:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class NotifyTests(unittest.TestCase):
    def test_unconfigured_alerts_send_nothing(self):
        opener = Mock()
        for environ in ({}, {notify.URL_VARIABLE: "  "}):
            with self.subTest(environ=environ):
                self.assertFalse(notify.send("t", "m", environ=environ, opener=opener))
        opener.assert_not_called()

    def test_posts_title_priority_tags_and_optional_token(self):
        opener = Mock(return_value=Response())
        environ = {notify.URL_VARIABLE: TOPIC}
        self.assertTrue(notify.send("Report failed", "details", environ=environ, opener=opener))
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, TOPIC)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b"details")
        self.assertEqual(request.get_header("Title"), "Report failed")
        self.assertEqual(request.get_header("Priority"), "high")
        self.assertEqual(request.get_header("Tags"), "warning")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(opener.call_args.kwargs["timeout"], notify.TIMEOUT_SECONDS)
        environ[notify.TOKEN_VARIABLE] = "tk_secret"
        notify.send("t", "m", priority="default", tags=("white_check_mark",), environ=environ, opener=opener)
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer tk_secret")
        self.assertEqual(request.get_header("Priority"), "default")
        self.assertEqual(request.get_header("Tags"), "white_check_mark")

    def test_titles_are_single_line_ascii_and_long_messages_stay_valid_utf8(self):
        opener = Mock(return_value=Response())
        notify.send("Café\r\nInjected: header", "é" * 5000, environ={notify.URL_VARIABLE: TOPIC}, opener=opener)
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Title"), "Caf? Injected: header")
        self.assertLessEqual(len(request.data), notify.MAX_MESSAGE_BYTES)
        request.data.decode()

    def test_failures_return_false_without_raising_or_logging_the_topic(self):
        failures = [urllib.error.URLError("unreachable"), TimeoutError(),
                    urllib.error.HTTPError(TOPIC, 429, "Too Many Requests", {}, None)]
        for error in failures:
            stderr = io.StringIO()
            with self.subTest(error=type(error).__name__), redirect_stderr(stderr):
                self.assertFalse(notify.send("t", "m", environ={notify.URL_VARIABLE: TOPIC},
                                             opener=Mock(side_effect=error)))
            self.assertIn("alert_failed", stderr.getvalue())
            self.assertNotIn("private-topic", stderr.getvalue())
        self.assertIn('"status": 429', stderr.getvalue())

    def test_rejects_urls_that_are_not_http_topics(self):
        for url in ("file:///etc/passwd", "ntfy.sh/topic", "https://ntfy.sh/", "https:///topic"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    notify.topic_url({notify.URL_VARIABLE: url})
                opener = Mock()
                with redirect_stderr(io.StringIO()):
                    self.assertFalse(notify.send("t", "m", environ={notify.URL_VARIABLE: url}, opener=opener))
                opener.assert_not_called()

    def test_unit_report_prefers_container_output_and_falls_back_to_the_unit(self):
        run = Mock(return_value=Mock(stdout='{"error": "Quote is stale"}\n'))
        title, message = notify.unit_report("investbell-paper.service", run=run)
        self.assertEqual(title, "investBell: investbell-paper failed")
        self.assertTrue(message.startswith('{"error": "Quote is stale"}\n\n'))
        self.assertTrue(message.endswith("journalctl --user -u investbell-paper.service -n 50"))
        self.assertEqual(run.call_args.args[0][:4], ["journalctl", "--user", "-t", "investbell-paper"])
        run = Mock(side_effect=[Mock(stdout=""), Mock(stdout="python exited")])
        _title, message = notify.unit_report("investbell-daily.service", run=run)
        self.assertEqual(run.call_args.args[0][2:4], ["-u", "investbell-daily.service"])
        self.assertTrue(message.startswith("python exited"))
        run = Mock(side_effect=subprocess.TimeoutExpired("journalctl", 10))
        _title, message = notify.unit_report("investbell-scheduler.service", run=run)
        self.assertEqual(message, "Inspect: journalctl --user -u investbell-scheduler.service -n 50")

    def test_command_line_requires_a_topic_and_reports_send_failures(self):
        stderr = io.StringIO()
        with patch.dict(os.environ, {notify.URL_VARIABLE: ""}), redirect_stderr(stderr), \
                self.assertRaises(SystemExit) as caught:
            notify.main(["--message", "hello"])
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("not set", stderr.getvalue())
        with patch.dict(os.environ, {notify.URL_VARIABLE: TOPIC}), \
                patch("investbell.notify.urllib.request.urlopen", return_value=Response()) as opener:
            self.assertEqual(notify.main(["--message", "hello", "--title", "Test", "--priority", "low"]), 0)
        request = opener.call_args.args[0]
        self.assertEqual((request.data, request.get_header("Title"), request.get_header("Priority")),
                         (b"hello", "Test", "low"))
        with patch.dict(os.environ, {notify.URL_VARIABLE: TOPIC}), redirect_stderr(io.StringIO()), \
                patch("investbell.notify.urllib.request.urlopen", side_effect=TimeoutError()):
            self.assertEqual(notify.main(["--message", "hello"]), 1)

    def test_unit_alerts_are_limited_to_one_per_unit_every_ten_minutes(self):
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        start = datetime(2026, 9, 24, 22, tzinfo=timezone.utc)
        environ = {notify.URL_VARIABLE: TOPIC, "XDG_RUNTIME_DIR": runtime.name}
        journal = Mock(return_value=Mock(stdout="boom"))
        with patch.dict(os.environ, environ), redirect_stderr(io.StringIO()) as stderr, \
                patch("investbell.notify.subprocess.run", journal), \
                patch("investbell.notify.urllib.request.urlopen", return_value=Response()) as opener:
            def alert(unit, minutes):
                return notify.main(["--unit", unit], clock=lambda: start + timedelta(minutes=minutes))
            self.assertEqual(alert("investbell-dashboard.service", 0), 0)
            self.assertEqual(alert("investbell-dashboard.service", 2), 0)
            self.assertEqual(alert("investbell-dashboard.service", 9), 0)
            self.assertEqual(opener.call_count, 1)
            self.assertIn("alert_suppressed", stderr.getvalue())
            self.assertEqual(alert("investbell-scheduler.service", 9), 0)
            self.assertEqual(opener.call_count, 2)
            self.assertEqual(alert("investbell-dashboard.service", 10), 0)
            self.assertEqual(opener.call_count, 3)
            self.assertIn(b"failed 2 more time(s) after the previous alert", opener.call_args.args[0].data)
            self.assertEqual(alert("investbell-dashboard.service", 21), 0)
            self.assertNotIn(b"more time(s)", opener.call_args.args[0].data)

    def test_failed_unit_alert_is_not_recorded_so_the_next_failure_retries(self):
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        now = datetime(2026, 9, 24, 22, tzinfo=timezone.utc)
        with patch.dict(os.environ, {notify.URL_VARIABLE: TOPIC, "XDG_RUNTIME_DIR": runtime.name}), \
                redirect_stderr(io.StringIO()), patch("investbell.notify.subprocess.run", Mock(return_value=Mock(stdout=""))), \
                patch("investbell.notify.urllib.request.urlopen", side_effect=[TimeoutError(), Response()]) as opener:
            self.assertEqual(notify.main(["--unit", "investbell-paper.service"], clock=lambda: now), 1)
            self.assertEqual(notify.main(["--unit", "investbell-paper.service"], clock=lambda: now), 0)
        self.assertEqual(opener.call_count, 2)

    def test_quiet_state_tolerates_missing_runtime_directory_and_corrupt_files(self):
        self.assertIsNone(notify.quiet_path("x.service", {}))
        self.assertEqual(notify.read_quiet(None), (None, 0))
        notify.write_quiet(None, datetime.now(timezone.utc), 0)
        with tempfile.TemporaryDirectory() as runtime:
            path = notify.quiet_path("x.service", {"XDG_RUNTIME_DIR": runtime})
            path.parent.mkdir()
            path.write_text("not json")
            self.assertEqual(notify.read_quiet(path), (None, 0))


if __name__ == "__main__":
    unittest.main()
