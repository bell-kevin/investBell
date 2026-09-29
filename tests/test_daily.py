from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from investbell.calendar import expected_sessions
from investbell.daily import run_daily, write_report
from investbell.engine import Bar, Parameters


class DailyModelTests(unittest.TestCase):
    def setUp(self):
        self.bars = [Bar(day, 100 + index * .1)
                     for index, day in enumerate(expected_sessions("2020-01-01", "2022-01-01"))]
        payload = json.dumps([asdict(bar) for bar in self.bars], separators=(",", ":"))
        self.source = {"kind": "yahoo", "symbol": "SPY", "label": "Yahoo fixture",
                       "fetched_at": "2022-01-02T00:00:00+00:00",
                       "sha256": hashlib.sha256(payload.encode()).hexdigest()}
        self.store = Mock()
        self.store.load.return_value = (self.bars, self.source)
        self.request = {"source": "yahoo", "symbol": "SPY", "start": "2020-01-01", "end": "2022-01-01",
                        **asdict(Parameters())}

    def test_daily_fits_then_reuses_parameters_and_binds_report_to_saved_model(self):
        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory) / "models"
            now = datetime(2022, 1, 2, tzinfo=timezone.utc)
            result, request = run_daily(self.request, store=self.store, model_dir=models, now=now)
            artifact = json.loads(next(models.glob("model-*.json")).read_text())
            self.assertEqual(result["parameter_mode"], "fitted")
            self.assertEqual(result["selected"]["params"], artifact["params"])
            self.assertEqual(result["fitted_model"]["model_sha256"], artifact["model_sha256"])
            self.assertEqual(len(result["grid"]), 1)
            self.assertFalse(result["fitted_model"]["evaluation"]["used_for_selection"])
            self.assertIn("includes training data", result["warnings"][-1])
            again, second_request = run_daily(self.request, store=self.store, model_dir=models, now=now)
            self.assertEqual(result["fitted_model"], again["fitted_model"])
            reports = Path(directory) / "reports"
            first, created = write_report(result, request, reports)
            self.assertTrue(created)
            second, created = write_report(again, second_request, reports)
            self.assertEqual(first, second)
            self.assertFalse(created)
            self.assertEqual(self.store.load.call_count, 2)  # One acquisition per daily run.
            changed = {**result, "fitted_model": {**result["fitted_model"], "model_sha256": "new-fit"}}
            third, created = write_report(changed, request, reports)
            self.assertTrue(created)
            self.assertNotEqual(first, third)
            self.assertEqual(len(list(models.glob("model-*.json"))), 1)

    def test_refresh_is_acquired_once_and_shared_by_fit_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = run_daily({**self.request, "refresh": True}, store=self.store,
                                  model_dir=directory, now=datetime(2022, 1, 2, tzinfo=timezone.utc))
            self.store.load.assert_called_once_with("SPY", "2020-01-01", "2022-01-01", refresh=True)
            artifact = json.loads(next(Path(directory).glob("model-*.json")).read_text())
            self.assertEqual(result["source"]["sha256"], artifact["data"]["source"]["sha256"])

    def test_fixed_mode_preserves_manual_values_without_training_or_model_files(self):
        with tempfile.TemporaryDirectory() as directory, patch("investbell.daily.ensure_model") as fit:
            models = Path(directory) / "models"
            request = {**self.request, "dca_pct": 3, "va_pct": .2, "capture_pct": 15}
            result, _ = run_daily(request, store=self.store, model_dir=models, model_mode="fixed")
            self.assertEqual(result["selected"]["params"]["dca_pct"], 3)
            self.assertEqual(result["selected"]["params"]["va_pct"], .2)
            self.assertEqual(result["selected"]["params"]["capture_pct"], 15)
            self.assertNotIn("fitted_model", result)
            self.assertFalse(models.exists())
            fit.assert_not_called()

    def test_invalid_request_cannot_fetch_or_fit(self):
        with tempfile.TemporaryDirectory() as directory, patch("investbell.daily.ensure_model") as fit:
            for changes in ({"source": "demo"}, {"dca_pct": -1}, {"news": True}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    run_daily({**self.request, **changes}, store=self.store, model_dir=directory)
            self.store.load.assert_not_called()
            fit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
