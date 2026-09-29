from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from investbell.data import (LEVERAGED_ETFS, DataUnavailable, PriceStore, completed_end, demo_bars,
                             fetch_yahoo, parse_french)
from investbell.calendar import CalendarUnavailable
from investbell.engine import Bar, Parameters
from investbell.paper import Policy
from investbell.service import config, run


class DataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = datetime(2024, 6, 5, 15, 0, tzinfo=timezone.utc)
        self.rows = [Bar("2024-06-03", 100), Bar("2024-06-04", 101)]
        self.fetcher = Mock(return_value=self.rows)
        self.store = PriceStore(self.directory.name + "/cache.sqlite3", fetcher=self.fetcher)

    def test_cache_roundtrip_preserves_provenance_and_avoids_second_download(self):
        rows, initial = self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        again, cached = self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        self.assertEqual(rows, again)
        self.assertFalse(initial["cached"])
        self.assertTrue(cached["cached"])
        self.assertEqual(initial["sha256"], cached["sha256"])
        self.assertEqual(initial["session_coverage"], "complete")
        self.assertEqual(initial["expected_first_session"], "2024-06-03")
        self.assertEqual(initial["expected_last_session"], "2024-06-04")
        self.assertEqual(initial["expected_session_count"], 2)
        self.fetcher.assert_called_once()

    def test_adapter_version_is_cached_with_snapshot_not_current_install(self):
        with patch("investbell.data.fetch_yahoo", return_value=self.rows), patch("investbell.data.version", return_value="1.test"):
            store = PriceStore(self.directory.name + "/versioned.sqlite3")
            _, initial = store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        with patch("investbell.data.version", return_value="2.test"):
            _, cached = store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        self.assertEqual(initial["yfinance_version"], "1.test")
        self.assertEqual(cached["yfinance_version"], "1.test")

    def test_pre_version_cache_migrates_without_inventing_fetch_version(self):
        path = self.directory.name + "/legacy.sqlite3"
        payload = json.dumps([asdict(bar) for bar in self.rows])
        with sqlite3.connect(path) as connection:
            connection.execute("""CREATE TABLE snapshots (
                symbol TEXT, start TEXT, end TEXT, fetched_at TEXT, digest TEXT, payload TEXT,
                PRIMARY KEY (symbol, start, end))""")
            connection.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
                               ("SPY", "2024-06-01", "2024-06-05", self.now.isoformat(),
                                hashlib.sha256(payload.encode()).hexdigest(), payload))
        store = PriceStore(path, fetcher=self.fetcher)
        rows, source = store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        self.assertEqual(rows, self.rows)
        self.assertTrue(source["cached"])
        self.assertIsNone(source["yfinance_version"])
        self.fetcher.assert_not_called()

    def test_current_unfinished_session_is_excluded(self):
        rows, source = self.store.load("SPY", "2024-06-01", "2024-06-10", now=self.now)
        self.fetcher.assert_called_with("SPY", "2024-06-01", "2024-06-05")
        self.assertEqual(source["effective_end"], "2024-06-05")
        after = datetime(2024, 6, 5, 20, 15, tzinfo=timezone.utc)
        self.assertEqual(completed_end(after).isoformat(), "2024-06-06")

    def test_default_exclusive_end_includes_latest_completed_session(self):
        for cutoff in (date(2024, 6, 5), date(2024, 6, 6)):
            with self.subTest(cutoff=cutoff), patch("investbell.service.completed_end", return_value=cutoff):
                self.assertEqual(config()["defaults"]["end"], cutoff.isoformat())

    def test_completion_cutoff_uses_new_york_time_during_daylight_and_standard_time(self):
        for month, utc_hour in ((6, 20), (1, 21)):
            before = datetime(2024, month, 5, utc_hour, 14, tzinfo=timezone.utc)
            after = before + timedelta(minutes=1)
            with self.subTest(month=month):
                self.assertEqual(completed_end(before), before.date())
                self.assertEqual(completed_end(after), after.date() + timedelta(days=1))

    def test_stale_cache_does_not_hide_provider_failure(self):
        self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        self.fetcher.side_effect = DataUnavailable("rate limited")
        with self.assertRaisesRegex(DataUnavailable, "rate limited"):
            self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now + timedelta(days=2))

    def test_force_refresh_fetches_new_whole_snapshot(self):
        self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        self.fetcher.return_value = [Bar("2024-06-03", 50), Bar("2024-06-04", 50.5)]
        rows, source = self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now, refresh=True)
        self.assertEqual(rows[0].open, 50)
        self.assertFalse(source["cached"])

    def test_corrupt_cache_fails_instead_of_silently_simulating(self):
        self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        with self.store.connect() as connection:
            connection.execute("UPDATE snapshots SET payload='[]'")
        with self.assertRaises(DataUnavailable):
            self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)

    def test_cached_rows_must_still_be_inside_completed_session_range(self):
        self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        payload = json.dumps([asdict(Bar("2024-06-05", 100))])
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.store.connect() as connection:
            connection.execute("UPDATE snapshots SET payload=?, digest=?", (payload, digest))
        with self.assertRaisesRegex(DataUnavailable, "Cached observations fall outside"):
            self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)

    def test_invalid_cache_timestamps_fail_visibly_and_allow_explicit_refresh(self):
        self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        for timestamp in ("not-a-timestamp", "2024-06-05T15:00:00"):
            with self.subTest(timestamp=timestamp):
                with self.store.connect() as connection:
                    connection.execute("UPDATE snapshots SET fetched_at=?", (timestamp,))
                with self.assertRaisesRegex(DataUnavailable, "timestamp is invalid"):
                    self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
                rows, source = self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now, refresh=True)
                self.assertEqual(rows, self.rows)
                self.assertFalse(source["cached"])

    def test_invalid_provider_observations_are_data_failures_and_not_cached(self):
        for rows in ([], [Bar("2024-06-03", float("nan"))], [Bar("2024-06-03", -1)]):
            self.fetcher.return_value = rows
            with self.subTest(rows=rows), self.assertRaisesRegex(DataUnavailable, "opening observations are invalid"):
                self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)

    def test_bad_source_dates_and_unapproved_symbols_are_rejected(self):
        with self.assertRaises(ValueError):
            self.store.load("PENNY", "2024-06-01", "2024-06-05", now=self.now)
        self.fetcher.return_value = [Bar("2024-06-05", 100)]
        with self.assertRaises(DataUnavailable):
            self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)

    def test_missing_start_interior_or_tail_session_is_rejected_before_caching(self):
        complete = [Bar(f"2024-06-{day:02d}", 100) for day in range(3, 8)]
        now = datetime(2024, 6, 10, 15, tzinfo=timezone.utc)
        for missing_index in (0, 2, 4):
            self.fetcher.return_value = complete[:missing_index] + complete[missing_index + 1:]
            with self.subTest(missing_index=missing_index), self.assertRaisesRegex(DataUnavailable, "missing 1 exchange session"):
                self.store.load("SPY", "2024-06-03", "2024-06-08", now=now)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)

    def test_fresh_download_timestamp_cannot_hide_stale_provider_tail(self):
        now = datetime(2024, 6, 28, 21, tzinfo=timezone.utc)
        with self.assertRaisesRegex(DataUnavailable, "observations are incomplete"):
            self.store.load("SPY", "2024-06-01", "2024-06-29", now=now)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)

    def test_incomplete_but_correctly_checksummed_cache_is_rejected(self):
        self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        payload = json.dumps([asdict(self.rows[0])])
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.store.connect() as connection:
            connection.execute("UPDATE snapshots SET payload=?, digest=?", (payload, digest))
        with self.assertRaisesRegex(DataUnavailable, "Cached observations are incomplete"):
            self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        # The user can explicitly recover by fetching a complete replacement.
        rows, source = self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now, refresh=True)
        self.assertEqual(rows, self.rows)
        self.assertFalse(source["cached"])

    def test_non_session_provider_rows_are_rejected_not_counted_as_freshness(self):
        cases = [
            ("2024-06-01", "2024-06-05", [Bar("2024-06-01", 100), *self.rows]),
            ("2024-07-03", "2024-07-06", [Bar("2024-07-03", 100), Bar("2024-07-04", 101), Bar("2024-07-05", 102)]),
        ]
        for start, end, rows in cases:
            self.fetcher.return_value = rows
            with self.subTest(start=start), self.assertRaisesRegex(DataUnavailable, "non-session dates"):
                self.store.load("SPY", start, end, now=datetime(2024, 8, 1, tzinfo=timezone.utc))
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 0)

    def test_historical_range_and_holiday_gaps_remain_valid(self):
        self.fetcher.return_value = [Bar("2024-07-03", 100), Bar("2024-07-05", 102)]
        rows, source = self.store.load("SPY", "2024-07-03", "2024-07-08",
                                       now=datetime(2025, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(rows, self.fetcher.return_value)
        self.assertEqual(source["expected_session_count"], 2)
        self.assertEqual(source["effective_end"], "2024-07-08")

    def test_holiday_only_request_and_calendar_failure_do_not_download(self):
        with self.assertRaisesRegex(ValueError, "no completed exchange sessions"):
            self.store.load("SPY", "2024-07-04", "2024-07-05",
                            now=datetime(2024, 7, 6, tzinfo=timezone.utc))
        with patch("investbell.data.expected_sessions", side_effect=CalendarUnavailable("Calendar failed")):
            with self.assertRaisesRegex(DataUnavailable, "Calendar failed"):
                self.store.load("SPY", "2024-06-01", "2024-06-05", now=self.now)
        self.fetcher.assert_not_called()

    def test_demo_is_reproducible_across_overlapping_ranges(self):
        long = demo_bars("2024-01-01", "2024-02-01")
        short = demo_bars("2024-01-10", "2024-01-20")
        self.assertEqual([bar for bar in long if "2024-01-10" <= bar.date < "2024-01-20"], short)

    def test_yahoo_adapter_uses_unadjusted_open_and_actions_not_close(self):
        frame = SimpleNamespace(empty=False, iterrows=lambda: iter([
            (datetime(2024, 6, 3), {"Open": 100, "Close": 999, "Adj Close": 1111,
                                   "Dividends": 1, "Capital Gains": 0.5, "Stock Splits": 2}),
            (datetime(2024, 6, 4), {"Open": 101, "Close": float("nan"), "Dividends": 0, "Stock Splits": 0}),
        ]))
        history = Mock(return_value=frame)
        provider = SimpleNamespace(Ticker=Mock(return_value=SimpleNamespace(history=history)))
        with patch.dict("sys.modules", {"yfinance": provider}):
            rows = fetch_yahoo("SPY", "2024-06-01", "2024-06-05")
        self.assertEqual(rows, [Bar("2024-06-03", 100, dividend=1.5, split=2), Bar("2024-06-04", 101)])
        self.assertFalse(history.call_args.kwargs["auto_adjust"])
        self.assertFalse(history.call_args.kwargs["back_adjust"])
        self.assertTrue(history.call_args.kwargs["actions"])


def french_file():
    """A Ken French-format file: four chosen July 1926 days, then 1,100 flat days."""
    rows = [(date(1926, 7, day), excess, rate) for day, excess, rate in
            [(1, 0.5, .01), (2, 1.0, .01), (6, -2.0, .02), (7, 0.1, .01)]]
    rows += [(date(1926, 7, 8) + timedelta(days=offset), 0.0, .01) for offset in range(1100)]
    body = "\n".join(f"{day:%Y%m%d},    {excess:.2f},   0.00,   0.00,    {rate:.2f}" for day, excess, rate in rows)
    return ("This file was created by using the 202607 CRSP database.\n\n,Mkt-RF,SMB,HML,RF\n"
            + body + "\n\nCopyright 2026 Eugene F. Fama and Kenneth R. French\n")


class ResearchSourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.french = Mock(return_value=french_file())
        self.store = PriceStore(self.directory.name + "/cache.sqlite3", reference_fetcher=self.french)

    def test_market_series_levels_rates_and_3x_construction(self):
        bars, rates, source = self.store.load_market("us_market", "1926-07-01", "1926-07-08", now=self.now)
        self.assertEqual([bar.date for bar in bars], ["1926-07-01", "1926-07-02", "1926-07-06", "1926-07-07"])
        self.assertEqual(rates, [.0001, .0001, .0002, .0001])
        expected = 100.0
        for bar, (excess, rate) in zip(bars[1:], [(.01, .0001), (-.02, .0002), (.001, .0001)]):
            expected *= 1 + excess + rate
            self.assertAlmostEqual(bar.open, expected)
        self.assertEqual((source["kind"], source["leverage"], source["data_last_date"]), ("us_market", 1, (date(1926, 7, 8) + timedelta(days=1099)).isoformat()))
        tripled, _, source3 = self.store.load_market("us_market_3x", "1926-07-01", "1926-07-08", now=self.now)
        self.assertAlmostEqual(tripled[1].open, 100 * (1 + 3 * .01 + .0001 - .0095 / 252))
        self.assertAlmostEqual(tripled[2].open, tripled[1].open * (1 + 3 * -.02 + .0002 - .0095 / 252))
        self.assertIn("reset daily", source3["construction"])
        # The file downloads once and then serves both series from the cache.
        self.french.assert_called_once()
        self.assertTrue(source3["cached"])

    def test_market_series_refreshes_after_a_week_and_rejects_bad_files(self):
        self.store.load_market("us_market", "1926-07-01", "1926-07-08", now=self.now)
        self.store.load_market("us_market", "1926-07-01", "1926-07-08", now=self.now + timedelta(days=8))
        self.assertEqual(self.french.call_count, 2)
        with self.assertRaisesRegex(ValueError, "covers 1926-07-01"):
            self.store.load_market("us_market", "1900-01-01", "1910-01-01", now=self.now)
        with self.assertRaises(DataUnavailable):
            parse_french("not the French file")
        broken = PriceStore(self.directory.name + "/broken.sqlite3", reference_fetcher=Mock(return_value="garbage"))
        with self.assertRaises(DataUnavailable):
            broken.load_market("us_market_3x", "1926-07-01", "1926-07-08", now=self.now)

    def test_market_series_run_earns_interest_and_labels_the_simulation(self):
        request = {"source": "us_market_3x", "symbol": "SPY", "start": "1926-07-01", "end": "1926-08-01",
                   "grid": {"dca": [10], "va": [0], "capture": [5]}}
        with patch("investbell.data.datetime") as clock:
            clock.now.return_value = self.now
            clock.fromisoformat = datetime.fromisoformat
            result = run(request, store=self.store)
        self.assertEqual(result["source"]["symbol"], "US market 3x (simulated)")
        self.assertGreater(result["selected"]["metrics"]["cash_interest"], 0)
        self.assertTrue(any(warning.startswith("Simulated 3x") for warning in result["warnings"]))
        self.assertTrue(any("T-bill rate" in warning for warning in result["warnings"]))
        self.assertFalse(any("no cash interest" in warning for warning in result["warnings"]))

    def test_leveraged_funds_are_research_only_and_start_at_first_session(self):
        rows = [Bar("2024-06-03", 100), Bar("2024-06-04", 101)]
        store = PriceStore(self.directory.name + "/yahoo.sqlite3", fetcher=Mock(return_value=rows))
        now = datetime(2024, 6, 5, 15, 0, tzinfo=timezone.utc)
        bars, source = store.load("SPXL", "2024-06-01", "2024-06-05", now=now)
        self.assertEqual(bars, rows)
        with self.assertRaisesRegex(ValueError, "SPXL began trading on 2008-11-05"):
            store.load("SPXL", "2008-01-02", "2009-01-02", now=now)
        with self.assertRaises(ValueError):
            store.load("SOXL", "2024-06-01", "2024-06-05", now=now)
        result = run({"source": "yahoo", "symbol": "SPXL", "start": "2024-06-01", "end": "2024-06-05",
                      "grid": {"dca": [2], "va": [0], "capture": [10]}}, store=store)
        self.assertTrue(result["warnings"][0].startswith("SPXL seeks 3x the daily S&P 500 return"))
        self.assertEqual(config()["leveraged_symbols"], LEVERAGED_ETFS)
        # The paper runner keeps its unleveraged allowlist.
        for symbol in LEVERAGED_ETFS:
            with self.subTest(symbol=symbol), self.assertRaisesRegex(ValueError, "approved ETF"):
                Policy(symbol=symbol)


if __name__ == "__main__":
    unittest.main()
