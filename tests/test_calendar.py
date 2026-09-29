from datetime import date, datetime, timezone
import unittest
from unittest.mock import patch

from investbell.calendar import CalendarUnavailable, expected_sessions, latest_completed_session
from investbell.data import DataUnavailable, completed_end


class CalendarTests(unittest.TestCase):
    def test_sessions_exclude_holidays_weekends_and_exceptional_closures(self):
        cases = [
            ("2024-07-03", "2024-07-08", ("2024-07-03", "2024-07-05")),
            ("2024-06-18", "2024-06-21", ("2024-06-18", "2024-06-20")),
            ("2024-03-28", "2024-04-02", ("2024-03-28", "2024-04-01")),
            # Hurricane Sandy and President Carter's national day of mourning.
            ("2012-10-26", "2012-11-01", ("2012-10-26", "2012-10-31")),
            ("2025-01-08", "2025-01-11", ("2025-01-08", "2025-01-10")),
        ]
        for start, end, expected in cases:
            with self.subTest(start=start):
                self.assertEqual(expected_sessions(start, end), expected)

    def test_holiday_only_range_has_no_sessions(self):
        self.assertEqual(expected_sessions("2024-07-04", "2024-07-05"), ())

    def test_early_close_waits_for_publication_buffer_then_becomes_available(self):
        before = datetime(2024, 7, 3, 17, 14, 59, tzinfo=timezone.utc)
        after = datetime(2024, 7, 3, 17, 15, tzinfo=timezone.utc)
        self.assertEqual(latest_completed_session(before), date(2024, 7, 2))
        self.assertEqual(latest_completed_session(after), date(2024, 7, 3))
        self.assertEqual(completed_end(after), date(2024, 7, 4))

    def test_holiday_and_weekend_do_not_invent_completed_sessions(self):
        cases = [
            ("2024-07-04T23:00:00+00:00", date(2024, 7, 3)),
            ("2024-07-06T23:00:00+00:00", date(2024, 7, 5)),
            ("2024-07-08T13:00:00+00:00", date(2024, 7, 5)),
            # Date remains Friday in New York despite Saturday UTC.
            ("2024-07-06T01:00:00+00:00", date(2024, 7, 5)),
        ]
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(latest_completed_session(datetime.fromisoformat(now)), expected)

    def test_regular_close_tracks_daylight_saving_transition(self):
        cases = [
            ("2024-03-08T21:14:59+00:00", date(2024, 3, 7)),
            ("2024-03-08T21:15:00+00:00", date(2024, 3, 8)),
            ("2024-03-11T20:14:59+00:00", date(2024, 3, 8)),
            ("2024-03-11T20:15:00+00:00", date(2024, 3, 11)),
        ]
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(latest_completed_session(datetime.fromisoformat(now)), expected)

    def test_naive_clock_rejected_and_calendar_failure_never_uses_weekday_fallback(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            latest_completed_session(datetime(2024, 6, 5))
        with patch("investbell.calendar._calendar", side_effect=RuntimeError("broken calendar")):
            with self.assertRaises(CalendarUnavailable):
                expected_sessions("2024-06-03", "2024-06-05")
            with self.assertRaises(DataUnavailable):
                completed_end(datetime(2024, 6, 5, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
