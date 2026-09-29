from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investbell.calendar import expected_sessions
from investbell.engine import Bar, Parameters, simulate
from investbell.training import (code_identity, digest, ensure_model, next_retraining,
                                 read_status)


class TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dates = expected_sessions("2020-01-02", "2023-01-01")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = datetime(2024, 2, 29, 22, tzinfo=timezone.utc)
        self.bars = [Bar(day, 100 + index * .02 + (index % 9))
                     for index, day in enumerate(self.dates[:630])]
        self.axes = {"dca": [1, 2], "va": [0, .1], "capture": [5, 10]}
        self.params = Parameters(initial_cash=15000, fee=.5, slippage_bps=7)

    def source(self, bars=None, **changes):
        bars = self.bars if bars is None else bars
        payload = json.dumps([asdict(bar) for bar in bars], separators=(",", ":"), allow_nan=False)
        return {"kind": "yahoo", "symbol": "SPY", "fetched_at": self.now.isoformat(),
                "sha256": hashlib.sha256(payload.encode()).hexdigest(), **changes}

    def fit(self, bars=None, **kwargs):
        bars = self.bars if bars is None else bars
        arguments = {"source": self.source(bars), "base_params": self.params,
                     "axes": self.axes, "model_dir": self.directory.name, "now": self.now}
        arguments.update(kwargs)
        return ensure_model(bars, **arguments)

    def test_fit_reproduces_training_objective_and_preserves_fixed_costs(self):
        artifact = self.fit()
        self.assertEqual(artifact["params"]["initial_cash"], 15000)
        self.assertEqual(artifact["params"]["fee"], .5)
        self.assertEqual(artifact["params"]["slippage_bps"], 7)
        candidates = artifact["selection"]["candidate_scores"]
        self.assertEqual(len(candidates), 8)
        peak_rank = lambda row: (-row["metrics"]["final_equity"],
                                 abs(row["metrics"]["max_drawdown_pct"]), row["metrics"]["trade_count"],
                                 row["dca_pct"], row["va_pct"], row["capture_pct"])
        # In a 2 x 2 x 2 grid, each axis step past the edge repeats the cell's own layer.
        by_cell = {(row["dca_pct"], row["va_pct"], row["capture_pct"]): row["metrics"]["final_equity"] for row in candidates}
        axes = [self.axes[key] for key in ("dca", "va", "capture")]
        def neighborhood(row):
            own = (row["dca_pct"], row["va_pct"], row["capture_pct"])
            layers = [[value, value, next(v for v in axis if v != value)] for value, axis in zip(own, axes)]
            return sum(by_cell[cell] for cell in product(*layers)) / 27
        winner = min(candidates, key=lambda row: (-neighborhood(row), *peak_rank(row)))
        for key in ("dca_pct", "va_pct", "capture_pct"):
            self.assertEqual(artifact["params"][key], winner[key])
        self.assertAlmostEqual(artifact["selection"]["neighborhood"]["mean_training_final_equity"],
                               neighborhood(winner), places=6)
        peak = min(candidates, key=peak_rank)
        self.assertEqual(artifact["selection"]["single_best_training_cell"]["training_final_equity"],
                         peak["metrics"]["final_equity"])
        # Every cell of a two-value grid is on its edge.
        self.assertEqual(artifact["selection"]["neighborhood"]["at_grid_edge"], ["dca", "va", "capture"])
        replay = simulate(self.bars[504:], Parameters(**artifact["params"]), detailed=False)
        self.assertEqual(artifact["evaluation"]["metrics"], replay["metrics"])
        peak_params = {key: peak[key] for key in ("dca_pct", "va_pct", "capture_pct")}
        peak_replay = simulate(self.bars[504:], replace(self.params, **peak_params), detailed=False)
        self.assertEqual(artifact["evaluation"]["single_best_training_cell"],
                         {"axes": peak_params, "metrics": peak_replay["metrics"], "used_for_selection": False})
        self.assertEqual(artifact["data"]["bars"], [asdict(bar) for bar in self.bars])
        self.assertEqual(artifact["data"]["bars_sha256"], digest(artifact["data"]["bars"]))
        self.assertEqual(artifact["configuration"]["code_sha256"], code_identity())
        self.assertFalse(artifact["evaluation"]["used_for_selection"])
        self.assertIn("previously explored", artifact["evaluation"]["classification"])

    def test_later_evaluation_cannot_change_selection(self):
        original = self.fit()
        changed = self.bars[:504] + [replace(bar, open=bar.open / 10) for bar in self.bars[504:]]
        with tempfile.TemporaryDirectory() as second:
            alternate = self.fit(changed, model_dir=second)
        self.assertEqual(original["selection"], alternate["selection"])
        self.assertEqual(original["params"], alternate["params"])
        self.assertNotEqual(original["evaluation"], alternate["evaluation"])
        self.assertLess(original["selection"]["training"]["end_inclusive"], original["evaluation"]["start"])

    def test_reuses_exact_artifact_until_two_calendar_year_boundary_then_refits(self):
        original = self.fit()
        due = datetime(2026, 2, 28, 22, tzinfo=timezone.utc)
        self.assertEqual(original["next_retrain_at"], due.isoformat())
        with patch("investbell.training.simulate", side_effect=AssertionError("Premature fitting")):
            self.assertEqual(self.fit(now=due - timedelta(microseconds=1)), original)
        replacement = self.fit(now=due)
        self.assertNotEqual(replacement["model_sha256"], original["model_sha256"])
        self.assertEqual(replacement["trained_at"], due.isoformat())
        self.assertEqual(len(list(Path(self.directory.name).glob("model-*.json"))), 2)
        self.assertEqual(self.fit(now=due), replacement)

    def test_new_observations_do_not_retrain_before_due(self):
        original = self.fit()
        added = self.bars + [Bar(day, 150) for day in self.dates[630:650]]
        self.assertEqual(self.fit(added), original)

    def test_configuration_and_code_changes_create_distinct_series(self):
        original = self.fit()
        cost_changed = self.fit(base_params=replace(self.params, fee=1))
        grid_changed = self.fit(axes={**self.axes, "dca": [3]})
        with patch("investbell.training.code_identity", return_value={"engine.py": "changed", "training.py": "changed"}):
            code_changed = self.fit()
        self.assertEqual(len({fit["configuration_sha256"] for fit in
                             (original, cost_changed, grid_changed, code_changed)}), 4)
        # Requested strategy defaults are not additional configuration axes.
        self.assertEqual(self.fit(base_params=replace(self.params, dca_pct=20)), original)

    def test_earlier_historical_request_cannot_consume_later_model(self):
        self.fit()
        with self.assertRaisesRegex(ValueError, "later history"):
            self.fit(self.bars[:-5])

    def test_complete_objective_ties_use_sorted_numeric_triple(self):
        axes = {"dca": [3, 1, 1], "va": [.1, 0], "capture": [10, 5]}
        metrics = {"final_equity": 10000, "max_drawdown_pct": -5, "trade_count": 30}
        with patch("investbell.training.simulate", return_value={"metrics": metrics}):
            artifact = self.fit(axes=axes)
        self.assertEqual(artifact["selection"]["selected_axes"], {"dca_pct": 1., "va_pct": 0., "capture_pct": 5.})
        self.assertEqual(artifact["configuration"]["axes"]["dca"], [1., 3.])

    def test_drawdown_and_trade_count_break_return_ties(self):
        def score(bars, parameters, detailed=False):
            return {"metrics": {"final_equity": 10000,
                                "max_drawdown_pct": -10 if parameters.dca_pct == 1 else -5,
                                "trade_count": 20 if parameters.va_pct else 40}}
        with patch("investbell.training.simulate", side_effect=score):
            artifact = self.fit()
        self.assertEqual(artifact["selection"]["selected_axes"], {"dca_pct": 2., "va_pct": .1, "capture_pct": 5.})

    def test_selects_center_of_bright_region_over_isolated_peak(self):
        axes = {"dca": [1, 2, 3, 4, 5], "va": [0, .1, .2], "capture": [5, 10, 15]}
        def score(bars, parameters, detailed=False):
            steps = (abs(axes["dca"].index(parameters.dca_pct) - 1), abs(axes["va"].index(parameters.va_pct) - 1),
                     abs(axes["capture"].index(parameters.capture_pct) - 1))
            if (parameters.dca_pct, parameters.va_pct, parameters.capture_pct) == (5, .2, 15):
                equity = 13000
            elif max(steps) == 0:
                equity = 12000
            elif max(steps) == 1:
                equity = 11500
            else:
                equity = 9000
            return {"metrics": {"final_equity": equity, "max_drawdown_pct": -5, "trade_count": 10}}
        with patch("investbell.training.simulate", side_effect=score):
            artifact = self.fit(axes=axes)
        self.assertEqual(artifact["selection"]["selected_axes"], {"dca_pct": 2., "va_pct": .1, "capture_pct": 10.})
        self.assertAlmostEqual(artifact["selection"]["neighborhood"]["mean_training_final_equity"],
                               (12000 + 26 * 11500) / 27)
        self.assertEqual(artifact["selection"]["neighborhood"]["at_grid_edge"], [])
        # dca=1 is an edge cell beside the center; its neighborhood ties, so its own score decides.
        self.assertEqual(artifact["selection"]["single_best_training_cell"],
                         {"dca_pct": 5., "va_pct": .2, "capture_pct": 15., "training_final_equity": 13000})
        self.assertEqual(artifact["evaluation"]["single_best_training_cell"]["axes"],
                         {"dca_pct": 5., "va_pct": .2, "capture_pct": 15.})
        self.assertEqual(read_status(self.directory.name)["latest"]["params"], artifact["params"])

    def test_retained_single_best_v1_artifacts_still_verify(self):
        current = self.fit()
        legacy = json.loads(json.dumps(current))
        objective = ["maximum training final_equity", "minimum training drawdown magnitude",
                     "minimum training trade_count", "ascending DCA, VA, capture triple"]
        legacy["artifact_version"] = legacy["configuration"]["artifact_version"] = "three-parameter-fit-v1"
        legacy["configuration"]["objective"] = objective
        legacy["configuration_sha256"] = digest(legacy["configuration"])
        candidates = legacy["selection"]["candidate_scores"]
        peak = min(candidates, key=lambda row: (-row["metrics"]["final_equity"],
                   abs(row["metrics"]["max_drawdown_pct"]), row["metrics"]["trade_count"],
                   row["dca_pct"], row["va_pct"], row["capture_pct"]))
        selected = {key: peak[key] for key in ("dca_pct", "va_pct", "capture_pct")}
        legacy["selection"] = {"objective": objective, "training": legacy["selection"]["training"],
                               "candidate_scores": candidates, "selected_axes": selected}
        legacy["params"].update(selected)
        del legacy["evaluation"]["single_best_training_cell"]
        def save(artifact):
            artifact["model_sha256"] = digest({key: value for key, value in artifact.items() if key != "model_sha256"})
            path = Path(self.directory.name) / f"model-{artifact['configuration_sha256']}-legacy.json"
            path.write_text(json.dumps(artifact))
            return path
        path = save(legacy)
        self.assertEqual(read_status(self.directory.name)["models"], 2)
        # A different series: the current configuration keeps reusing its own fit.
        self.assertEqual(self.fit(), current)
        other = next(row for row in candidates if row is not peak)
        legacy["selection"]["selected_axes"] = {key: other[key] for key in ("dca_pct", "va_pct", "capture_pct")}
        legacy["params"].update(legacy["selection"]["selected_axes"])
        path.unlink()
        save(legacy)
        with self.assertRaisesRegex(ValueError, "selected parameters"):
            read_status(self.directory.name)

    def test_corrupt_artifact_is_rejected_without_replacing_it(self):
        self.fit()
        path = next(Path(self.directory.name).glob("model-*.json"))
        contents = json.loads(path.read_text())
        contents["params"]["dca_pct"] = 5
        path.write_text(json.dumps(contents))
        with patch("investbell.training.simulate", side_effect=AssertionError("Corruption silently refitted")):
            with self.assertRaisesRegex(ValueError, "checksum"):
                self.fit()
        self.assertEqual(len(list(Path(self.directory.name).glob("model-*.json"))), 1)
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_status(self.directory.name)

    def test_invalid_sources_timestamps_history_and_axes_fail(self):
        cases = [
            {"source": self.source(kind="demo")},
            {"source": self.source(symbol="PENNY")},
            {"source": self.source(symbol="SPXL")},
            {"source": self.source(sha256="wrong")},
            {"source": self.source(fetched_at="2024-02-29T22:00:00")},
            {"source": self.source(fetched_at="2025-02-28T22:00:00+00:00")},
            {"source": self.source(expected_session_count=1)},
            {"now": self.now.replace(tzinfo=None)},
            {"axes": {**self.axes, "momentum": [1]}},
            {"axes": {**self.axes, "dca": [True]}},
            {"axes": {**self.axes, "va": [float("nan")]}},
            {"axes": {**self.axes, "capture": list(range(1, 9))}},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.fit(**kwargs)
        with self.assertRaisesRegex(ValueError, "378"):
            self.fit(self.bars[:377])
        with self.assertRaisesRegex(ValueError, "every exchange session"):
            self.fit(self.bars[:200] + self.bars[201:])
        with self.assertRaisesRegex(ValueError, "not completed"):
            self.fit(source=self.source(fetched_at="2020-01-02T15:00:00+00:00"))

    def test_minimum_split_and_status_do_not_expose_large_raw_data(self):
        self.assertFalse(read_status(self.directory.name)["initialized"])
        artifact = self.fit(self.bars[:378])
        self.assertEqual(artifact["selection"]["training"]["observations"], 252)
        self.assertEqual(artifact["evaluation"]["observations"], 126)
        status = read_status(self.directory.name)
        self.assertTrue(status["initialized"])
        self.assertTrue(status["healthy"])
        self.assertEqual(status["models"], 1)
        self.assertEqual(status["latest"]["params"], artifact["params"])
        self.assertEqual(status["latest"]["data"]["source"]["symbol"], "SPY")
        self.assertNotIn("bars", status["latest"]["data"])

    def test_two_year_schedule_uses_calendar_not_730_days(self):
        self.assertEqual(next_retraining("2023-03-01T10:00:00+00:00"),
                         datetime(2025, 3, 1, 10, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
