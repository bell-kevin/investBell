from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import product
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from investbell.calendar import CalendarUnavailable, expected_sessions
from investbell.engine import Bar, Parameters, simulate
from investbell.research import (canonical_digest, freeze_plan, load_cached_snapshot,
                                 synthetic_bars, validate_history, verify_plan,
                                 write_new_json)
from investbell.research_execution import ExecutionAssumptions, simulate_execution


class ResearchExecutionTests(unittest.TestCase):
    def test_zero_friction_matches_reference_across_paths_and_fees(self):
        rng = random.Random(130)
        held = 0
        cases = product((0, 1, 100), (0.1, 2, 50, 100), ((True, None), (False, None), (True, [0.0002] * 80)))
        for fee, dca, (no_loss_sales, rates) in cases:
            bars = synthetic_bars([100 * rng.uniform(0.2, 2.0) for _ in range(80)])
            bars = [replace(bar, dividend=2 if i % 7 == 0 else 0) for i, bar in enumerate(bars)]
            params = Parameters(initial_cash=1000, dca_pct=dca, capture_pct=5, fee=fee, no_loss_sales=no_loss_sales)
            reference = simulate(bars, params, cash_rates=rates)
            candidate = simulate_execution(bars, params, cash_rates=rates)
            held += reference["metrics"]["held_loss_sales"]
            with self.subTest(fee=fee, dca=dca, no_loss_sales=no_loss_sales, rates=bool(rates)):
                for key in ("held_loss_sales", "losing_sales"):
                    self.assertEqual(reference["metrics"][key], candidate["metrics"][key])
                self.assertAlmostEqual(reference["metrics"]["cash_interest"], candidate["metrics"]["cash_interest"])
                self.assertAlmostEqual(reference["metrics"]["final_equity"], candidate["metrics"]["final_equity"])
                self.assertAlmostEqual(reference["metrics"]["max_drawdown_pct"], candidate["metrics"]["max_opening_drawdown_pct"])
                self.assertEqual(reference["metrics"]["trade_count"], candidate["metrics"]["trade_count"])
                self.assertEqual(reference["metrics"]["capture_count"], candidate["metrics"]["capture_count"])
                for expected, actual in zip(reference["equity"], candidate["equity"]):
                    self.assertAlmostEqual(expected["equity"], actual["equity"])
                    self.assertAlmostEqual(expected["cash"], actual["cash"])
                benchmark = simulate_execution(bars, params, policy="buy_and_hold", cash_rates=rates)
                self.assertAlmostEqual(reference["metrics"]["benchmark_return_pct"], benchmark["metrics"]["total_return_pct"])
        # The random paths must exercise the no-loss rule, not just pass around it.
        self.assertGreater(held, 0)

    def test_unsettled_sale_and_unpaid_distribution_remain_assets(self):
        bars = synthetic_bars([100, 200, 300, 300])
        bars[2] = replace(bars[2], dividend=2)
        result = simulate_execution(bars, Parameters(initial_cash=1000, dca_pct=50, va_pct=5,
                                                    capture_pct=5, slippage_bps=0),
                                    ExecutionAssumptions(settlement_sessions=1, dividend_delay_sessions=20))
        exit_day = result["equity"][2]
        self.assertEqual(exit_day["cash"], 0)
        self.assertEqual(exit_day["receivables"], 2265)
        self.assertEqual(exit_day["equity"], 2265)
        self.assertEqual(exit_day["cycle_start"], 2265)
        self.assertEqual([t["side"] for t in result["trades"] if t["date"] == bars[2].date], ["SELL"])
        self.assertAlmostEqual(result["equity"][3]["receivables"], 15)
        self.assertAlmostEqual(result["trades"][-1]["amount"], 1132.5)
        for point in result["equity"]:
            self.assertAlmostEqual(point["equity"], point["cash"] + point["receivables"] + point["invested"])
            self.assertGreaterEqual(point["cash"], 0)

    def test_payment_delay_preserves_entitlement_without_early_reinvestment(self):
        bars = synthetic_bars([100, 99, 99])
        bars[1] = replace(bars[1], dividend=1)
        params = Parameters(initial_cash=1000, dca_pct=100, capture_pct=1000, slippage_bps=0)
        delayed = simulate_execution(bars, params, ExecutionAssumptions(dividend_delay_sessions=1))
        immediate = simulate_execution(bars, params)
        self.assertEqual(delayed["equity"][1]["receivables"], 10)
        self.assertEqual(delayed["equity"][1]["equity"], 1000)
        self.assertEqual(delayed["trades"][-1]["date"], bars[2].date)
        self.assertEqual(immediate["trades"][-1]["date"], bars[1].date)
        self.assertAlmostEqual(delayed["metrics"]["final_equity"], immediate["metrics"]["final_equity"])

    def test_post_sale_buy_has_separate_price_and_reentry_stays_enabled(self):
        bars = synthetic_bars([100, 110, 110])
        params = Parameters(initial_cash=1000, dca_pct=100, capture_pct=5, slippage_bps=0)
        delayed_price = simulate_execution(bars, params, ExecutionAssumptions(post_sale_buy_bps=25))
        same_price = simulate_execution(bars, params)
        sale, buy = delayed_price["trades"][-2:]
        self.assertEqual(sale["date"], buy["date"])
        self.assertEqual(sale["price"], 110)
        self.assertAlmostEqual(buy["price"], 110 * 1.0025)
        self.assertTrue(buy["post_exit"])
        self.assertLess(delayed_price["metrics"]["final_equity"], same_price["metrics"]["final_equity"])

    def test_future_observation_changes_neither_prior_equity_nor_sale_quantity(self):
        # Gap fills below cost are the point here, so the no-loss rule is off.
        params = Parameters(initial_cash=1000, dca_pct=50, va_pct=5, capture_pct=5, slippage_bps=0, no_loss_sales=False)
        original = simulate_execution(synthetic_bars([100, 200, 300]), params, ExecutionAssumptions(1, 20, 10))
        gap = simulate_execution(synthetic_bars([100, 200, 1]), params, ExecutionAssumptions(1, 20, 10))
        self.assertEqual(original["equity"][:2], gap["equity"][:2])
        self.assertEqual(original["trades"][-1]["shares"], gap["trades"][-1]["shares"])
        self.assertLess(gap["metrics"]["total_return_pct"], 0)

    def test_fixed_dca_exhausts_cash_without_phantom_deposits(self):
        result = simulate_execution(synthetic_bars([100] * 60 + [50]), Parameters(), policy="simple_dca")
        self.assertEqual(result["metrics"]["trade_count"], 50)
        self.assertEqual(result["metrics"]["minimum_spendable_cash"], 0)
        self.assertLess(result["metrics"]["total_return_pct"], -50)
        self.assertEqual(result["metrics"]["capture_count"], 0)

    def test_dca_va_trims_at_zero_va_without_capture_or_budget_reset(self):
        bars = synthetic_bars([100, 200, 300, 400])
        params = Parameters(initial_cash=1000, dca_pct=50, va_pct=0,
                            capture_pct=0.1, slippage_bps=0)
        result = simulate_execution(bars, params, policy="dca_va")
        sales = [trade for trade in result["trades"] if trade["side"] == "SELL"]
        buys = [trade for trade in result["trades"] if trade["side"] == "BUY"]
        self.assertTrue(sales)
        self.assertTrue(all(not trade["capture"] for trade in sales))
        self.assertEqual(result["metrics"]["capture_count"], 0)
        self.assertTrue(all(point["cycle_start"] == 1000 for point in result["equity"]))
        self.assertTrue(all(trade["amount"] == 500 for trade in buys))
        self.assertTrue(all(not trade["post_exit"] for trade in buys))
        self.assertAlmostEqual(sales[0]["shares"], 2.5)
        self.assertGreater(result["equity"][2]["invested"], 0)
        strategy = simulate_execution(bars, params)
        self.assertGreater(strategy["metrics"]["capture_count"], 0)
        high_threshold = simulate_execution(bars, replace(params, capture_pct=1000), policy="dca_va")
        for field in ("metrics", "equity", "trades", "execution"):
            self.assertEqual(result[field], high_threshold[field])
        self.assertNotEqual(result["parameters"], high_threshold["parameters"])

    def test_dca_va_without_trim_signal_matches_dca_with_same_costs_and_funding(self):
        bars = synthetic_bars([100, 90, 80, 70, 60, 50])
        bars[2] = replace(bars[2], dividend=1)
        params = Parameters(initial_cash=1000, dca_pct=25, va_pct=0,
                            capture_pct=0.1, fee=1, slippage_bps=10)
        assumptions = ExecutionAssumptions(1, 2, 25)
        dca = simulate_execution(bars, params, assumptions, policy="simple_dca")
        dca_va = simulate_execution(bars, params, assumptions, policy="dca_va")
        self.assertTrue(all(trade["side"] == "BUY" for trade in dca_va["trades"]))
        self.assertGreater(dca_va["equity"][2]["receivables"], 0)
        self.assertGreater(dca_va["metrics"]["fees_paid"], 0)
        for field in ("parameters", "execution", "metrics", "equity", "trades"):
            self.assertEqual(dca_va[field], dca[field])

    def test_dca_va_uses_prior_signal_and_waits_for_settlement_and_distributions(self):
        bars = synthetic_bars([100, 200, 300, 300])
        bars[2] = replace(bars[2], dividend=2)
        params = Parameters(initial_cash=1000, dca_pct=50, va_pct=0,
                            capture_pct=0.1, slippage_bps=0, no_loss_sales=False)
        assumptions = ExecutionAssumptions(1, 20, 10)
        result = simulate_execution(bars, params, assumptions, policy="dca_va")
        sales = [trade for trade in result["trades"] if trade["side"] == "SELL"]
        self.assertEqual(sales[0]["date"], bars[2].date)
        self.assertEqual(sales[0]["shares"], 2.5)
        self.assertEqual(sales[0]["price"], 300)
        self.assertEqual(result["equity"][2]["cash"], 0)
        self.assertEqual(result["equity"][2]["receivables"], 765)
        self.assertEqual(result["equity"][2]["equity"], 2265)
        self.assertEqual([trade["side"] for trade in result["trades"]
                          if trade["date"] == bars[2].date], ["SELL"])
        self.assertEqual(result["trades"][-1]["amount"], 500)
        self.assertAlmostEqual(result["trades"][-1]["price"], 300 * 1.001)
        self.assertEqual(result["equity"][3]["receivables"], 515)
        for point in result["equity"]:
            self.assertAlmostEqual(point["equity"], point["cash"] + point["receivables"] + point["invested"])
            self.assertGreaterEqual(point["cash"], 0)
        gap_bars = bars[:2] + [replace(bar, open=1) for bar in bars[2:]]
        gap = simulate_execution(gap_bars, params, assumptions, policy="dca_va")
        self.assertEqual(result["equity"][:2], gap["equity"][:2])
        gap_sale = next(trade for trade in gap["trades"] if trade["side"] == "SELL")
        self.assertEqual(gap_sale["shares"], sales[0]["shares"])
        self.assertLess(gap["metrics"]["final_equity"], result["metrics"]["final_equity"])

    def test_va_trim_and_repurchase_costs_reduce_equity_once_on_flat_fill_day(self):
        bars = synthetic_bars([100, 110, 110])
        params = Parameters(initial_cash=1000, dca_pct=100, va_pct=0,
                            capture_pct=0.1, fee=1, slippage_bps=10)
        assumptions = ExecutionAssumptions(post_sale_buy_bps=25)
        result = simulate_execution(bars, params, assumptions, policy="dca_va")
        dca = simulate_execution(bars, params, assumptions, policy="simple_dca")
        initial_buy, sale, rebuy = result["trades"]
        self.assertEqual(sale["side"], "SELL")
        self.assertEqual(rebuy["side"], "BUY")
        self.assertEqual(sale["date"], rebuy["date"])
        self.assertTrue(rebuy["post_sale"])
        self.assertAlmostEqual(sale["price"], 110 * 0.999)
        self.assertAlmostEqual(rebuy["price"], 110 * 1.001 * 1.0025)
        round_trip_cost = (2 + sale["shares"] * (110 - sale["price"])
                           + rebuy["shares"] * (rebuy["price"] - 110))
        metrics = result["metrics"]
        self.assertEqual(metrics["va_sell_count"], 1)
        self.assertEqual(metrics["fees_paid"], 3)
        self.assertAlmostEqual(metrics["gross_traded_notional"],
                               999 + sale["amount"] + rebuy["amount"])
        self.assertAlmostEqual(metrics["modeled_slippage_cost"],
                               initial_buy["shares"] * 0.1 + round_trip_cost - 2)
        self.assertAlmostEqual(metrics["modeled_execution_cost"],
                               metrics["fees_paid"] + metrics["modeled_slippage_cost"])
        self.assertAlmostEqual(result["equity"][1]["equity"] - result["equity"][2]["equity"],
                               round_trip_cost)
        self.assertAlmostEqual(dca["metrics"]["final_equity"] - metrics["final_equity"], round_trip_cost)
        self.assertAlmostEqual(metrics["final_equity"],
                               1000 + initial_buy["shares"] * 10 - metrics["modeled_execution_cost"])

    def test_invalid_execution_inputs_fail(self):
        for kwargs in ({"settlement_sessions": True}, {"settlement_sessions": -1},
                       {"dividend_delay_sessions": 1.1}, {"post_sale_buy_bps": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ExecutionAssumptions(**kwargs)


class ResearchReportTests(unittest.TestCase):
    def setUp(self):
        self.bars = synthetic_bars([100 + i * 0.2 for i in range(80)])
        self.split = self.bars[40].date
        self.provenance = {"symbol": "SPY", "observed_end": self.bars[-1].date}
        self.now = datetime(2026, 9, 23, 15, tzinfo=timezone.utc)

    def report(self, bars=None):
        return validate_history(bars or self.bars, Parameters(), self.split, self.provenance, now=self.now)

    def test_chronological_partition_has_no_shared_observations_or_training_leakage(self):
        report = self.report()
        self.assertLess(report["training"]["end_inclusive"], report["later_retrospective_evaluation"]["start"])
        self.assertEqual(report["training"]["observations"], 40)
        self.assertEqual(report["later_retrospective_evaluation"]["observations"], 40)
        changed = self.bars[:40] + [replace(bar, open=bar.open / 10) for bar in self.bars[40:]]
        alternate = self.report(changed)
        self.assertEqual(report["training"], alternate["training"])
        self.assertEqual(report["parameters"], alternate["parameters"])
        self.assertIn("retrospective", report["validation_design"]["classification"])
        self.assertEqual(report["status"], "research_only_not_live_approval")
        self.assertTrue(report["parameter_sensitivity"])
        self.assertEqual(len(report["execution_sensitivity"]), 3)
        self.assertEqual(len(report["synthetic_stress"]), 4)
        self.assertEqual(canonical_digest(report), canonical_digest(self.report()))

    def test_layer_comparisons_propagate_with_matching_parameters_and_execution(self):
        params = Parameters(initial_cash=2000, dca_pct=20, va_pct=0,
                            capture_pct=1, fee=0.5, slippage_bps=7)
        bars = [replace(bar, dividend=1 if index % 7 == 0 else 0)
                for index, bar in enumerate(self.bars)]
        report = validate_history(bars, params, self.split, self.provenance, now=self.now)
        self.assertEqual(report["report_version"], "retrospective-validation-v2")
        policies = ("simple_dca", "dca_va", "strategy", "buy_and_hold")
        self.assertEqual(set(report["comparison_design"]["policies"]), set(policies))
        evaluation = bars[40:]
        comparisons = [("training", report["training"], bars[:40], params, None),
                       ("evaluation", report["later_retrospective_evaluation"], evaluation, params, None),
                       ("full_history", report["full_history_descriptive"], bars, params, None)]
        for variant in report["parameter_sensitivity"]:
            candidate = replace(params, **{variant["changed"]: variant["value"]})
            comparisons.append((f"parameter:{variant['changed']}:{variant['value']}",
                                variant["result"], evaluation, candidate, None))
        for variant in report["cost_sensitivity"]:
            candidate = replace(params, slippage_bps=variant["slippage_bps"], fee=variant["fee"])
            comparisons.append((f"cost:{variant['slippage_bps']}:{variant['fee']}",
                                variant["result"], evaluation, candidate, None))
        for variant in report["execution_sensitivity"]:
            assumptions = ExecutionAssumptions(**variant["assumptions"])
            comparisons.append((variant["name"], variant["result"], evaluation, params, assumptions))
        for variant in report["synthetic_stress"]:
            observations = synthetic_bars(variant["construction"]["prices"])
            comparisons.append((variant["name"], variant["result"], observations, params, None))
        for name, summary, observations, candidate, assumptions in comparisons:
            with self.subTest(comparison=name):
                for policy in policies:
                    expected = simulate_execution(observations, candidate, assumptions, policy=policy)
                    self.assertEqual(summary[policy], expected["metrics"])
                    self.assertEqual(summary[policy]["initial_cash"], params.initial_cash)
                self.assertEqual(summary["dca_va"]["capture_count"], 0)
                for added, baseline in (("dca_va", "simple_dca"), ("strategy", "dca_va"),
                                        ("strategy", "simple_dca"), ("strategy", "buy_and_hold")):
                    self.assertAlmostEqual(summary[f"{added}_minus_{baseline}_return_pp"],
                                           summary[added]["total_return_pct"] - summary[baseline]["total_return_pct"])
                self.assertAlmostEqual(summary["dca_va_minus_simple_dca_return_pp"]
                                       + summary["strategy_minus_dca_va_return_pp"],
                                       summary["strategy_minus_simple_dca_return_pp"])

    def test_empty_or_too_small_partitions_fail(self):
        for split in (self.bars[0].date, self.bars[5].date, self.bars[-1].date):
            with self.subTest(split=split), self.assertRaises(ValueError):
                validate_history(self.bars, Parameters(), split, self.provenance)

    def test_frozen_plan_rejects_hindsight_and_detects_edits(self):
        report = self.report()
        plan = freeze_plan(report, now=self.now)
        self.assertTrue(verify_plan(plan))
        self.assertEqual(plan["prospective_start_not_before"], "2026-09-24")
        window = plan["evaluation"]
        self.assertEqual(window["first_expected_session"], "2026-09-24")
        self.assertEqual(len(expected_sessions(window["first_expected_session"],
                         (datetime.fromisoformat(window["minimum_window_end_inclusive"]) + timedelta(days=1)).date())), 252)
        self.assertIn("insufficient_evidence", window["incomplete"])
        self.assertTrue(window["pass_requires_all"])
        self.assertEqual(plan["retrospective_report_sha256"], canonical_digest(report))
        with self.assertRaises(ValueError):
            freeze_plan(report, prospective_start="2026-09-23", now=self.now)
        with self.assertRaises(ValueError):
            freeze_plan(report, now=self.now.replace(tzinfo=None))
        plan["parameters"]["dca_pct"] = 3
        with self.assertRaises(ValueError):
            verify_plan(plan)

    def test_new_artifacts_cannot_overwrite_existing_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            write_new_json(path, {"version": 1})
            with self.assertRaises(FileExistsError):
                write_new_json(path, {"version": 2})
            self.assertEqual(json.loads(path.read_text()), {"version": 1})

    def test_cached_snapshot_is_read_only_and_digest_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.sqlite3"
            start, end = "2024-07-03", "2024-07-08"
            observations = [Bar(session, 100) for session in expected_sessions(start, end)]
            payload = json.dumps([asdict(bar) for bar in observations])
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE snapshots (symbol,start,end,fetched_at,digest,payload,adapter_version)")
                conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", ("SPY", start, end,
                             self.now.isoformat(), hashlib.sha256(payload.encode()).hexdigest(), payload, "test"))
            before = path.read_bytes()
            bars, provenance = load_cached_snapshot(path, "SPY", start, end, now=self.now)
            self.assertEqual(bars, observations)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(provenance["observed_end"], "2024-07-05")
            self.assertEqual(provenance["expected_session_count"], 2)
            with self.assertRaises(ValueError):
                load_cached_snapshot(path, "SPY", "2024-07-02", end, now=self.now)
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE snapshots SET digest='corrupt'")
            with self.assertRaises(ValueError):
                load_cached_snapshot(path, "SPY", start, end, now=self.now)

    def test_cache_rejects_correctly_checksummed_gaps_and_non_sessions(self):
        start, end = "2024-07-01", "2024-07-09"
        complete = [Bar(session, 100) for session in expected_sessions(start, end)]
        variants = [complete[1:], complete[:-1], complete[:2] + complete[3:],
                    sorted(complete + [Bar("2024-07-04", 100)], key=lambda bar: bar.date)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE snapshots (symbol,start,end,fetched_at,digest,payload,adapter_version)")
            for observations in variants:
                payload = json.dumps([asdict(bar) for bar in observations])
                with sqlite3.connect(path) as conn:
                    conn.execute("DELETE FROM snapshots")
                    conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", ("SPY", start, end,
                                 self.now.isoformat(), hashlib.sha256(payload.encode()).hexdigest(), payload, "test"))
                with self.subTest(dates=[bar.date for bar in observations]), self.assertRaisesRegex(ValueError, "missing.*exchange sessions"):
                    load_cached_snapshot(path, "SPY", start, end, now=self.now)

    def test_cache_rejects_future_timestamps_incomplete_sessions_and_calendar_failure(self):
        start, end = "2024-07-03", "2024-07-04"
        payload = json.dumps([asdict(Bar("2024-07-03", 100))])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "market.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE snapshots (symbol,start,end,fetched_at,digest,payload,adapter_version)")
                conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", ("SPY", start, end,
                             "2024-07-03T16:00:00+00:00", hashlib.sha256(payload.encode()).hexdigest(), payload, "test"))
            # July 3 is an early close at 17:00 UTC, plus publication buffer.
            with self.assertRaisesRegex(ValueError, "not completed"):
                load_cached_snapshot(path, "SPY", start, end, now=self.now)
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE snapshots SET fetched_at=?", ((self.now + timedelta(days=1)).isoformat(),))
            with self.assertRaisesRegex(ValueError, "future"):
                load_cached_snapshot(path, "SPY", start, end, now=self.now)
            with patch("investbell.research.expected_sessions", side_effect=CalendarUnavailable("Unavailable")):
                with self.assertRaises(CalendarUnavailable):
                    load_cached_snapshot(path, "SPY", start, end, now=self.now)


if __name__ == "__main__":
    unittest.main()
