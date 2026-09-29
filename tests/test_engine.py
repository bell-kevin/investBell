from dataclasses import replace
import math
import unittest

from investbell.engine import Bar, Parameters, simulate, validate_bars


def bars(prices):
    return [Bar(f"2024-01-{index + 1:02}", price) for index, price in enumerate(prices)]


class EngineTests(unittest.TestCase):
    def test_dca_is_cycle_capital_percentage_and_cash_capped(self):
        result = simulate(bars([100] * 8), Parameters(initial_cash=1000, dca_pct=30, slippage_bps=0, fee=1))
        buys = [trade for trade in result["trades"] if trade["side"] == "BUY"]
        self.assertEqual([trade["amount"] for trade in buys], [300, 300, 300, 96])
        self.assertTrue(all(point["cash"] >= 0 for point in result["equity"]))
        self.assertAlmostEqual(result["metrics"]["final_equity"], 996)
        self.assertEqual(result["next_action"]["action"], "hold")

    def test_full_exit_allows_same_session_dca_from_actual_net_cash(self):
        result = simulate(bars([100, 200, 300, 300]), Parameters(initial_cash=1000, dca_pct=50, va_pct=5,
                                                             capture_pct=5, slippage_bps=0))
        trades = result["trades"]
        self.assertEqual([trade["side"] for trade in trades], ["BUY", "BUY", "SELL", "BUY", "BUY"])
        self.assertEqual(trades[2]["date"], "2024-01-03")
        self.assertEqual(trades[2]["signal_date"], "2024-01-02")
        self.assertEqual(trades[2]["reason"], "capture: full exit")
        self.assertAlmostEqual(trades[2]["shares"], 7.5)
        self.assertAlmostEqual(trades[2]["amount"], 2250)
        self.assertEqual(trades[3]["date"], trades[2]["date"])
        self.assertEqual(trades[3]["reason"], "post-exit DCA")
        self.assertAlmostEqual(result["equity"][2]["cycle_start"], 2250)
        self.assertAlmostEqual(result["equity"][2]["va_target"], 1125)
        self.assertAlmostEqual(result["equity"][2]["invested"], 1125)
        self.assertAlmostEqual(result["equity"][2]["cash"], 1125)
        self.assertEqual([trade["amount"] for trade in trades if trade["side"] == "BUY"], [500, 500, 1125, 1125])
        self.assertEqual(result["metrics"]["capture_count"], 1)
        self.assertEqual(result["next_action"]["cycle_start_equity"], 2250)
        self.assertEqual(result["next_action"]["buy_dollars"], 1125)

    def test_capture_plan_estimates_buy_but_actual_exit_proceeds_set_fill_size(self):
        params = Parameters(initial_cash=1000, dca_pct=50, va_pct=5, capture_pct=5, slippage_bps=0)
        prefix = simulate(bars([100, 200]), params)
        plan = prefix["next_action"]
        self.assertEqual(plan["action"], "capture")
        self.assertTrue(plan["buy_dollars_contingent_on_exit"])
        self.assertEqual(plan["sell_shares"], 7.5)
        self.assertEqual(plan["estimated_sell_net"], 1500)
        self.assertEqual(plan["buy_dollars"], 750)
        self.assertEqual(plan["estimated_buy_dollars"], 750)
        self.assertEqual(plan["buy_dollars_cash_capped"], 0)
        unchanged = simulate(bars([100, 200, 200]), params)
        self.assertEqual(unchanged["trades"][-1]["amount"], plan["estimated_buy_dollars"])
        gap = simulate(bars([100, 200, 300]), params)
        self.assertEqual(gap["equity"][:2], prefix["equity"])
        self.assertEqual(gap["trades"][-1]["amount"], 1125)

    def test_dca_continues_through_dips_without_selling(self):
        result = simulate(bars([100, 80, 60]), Parameters(initial_cash=1000, dca_pct=10,
                                                       va_pct=0, slippage_bps=0))
        trades = result["trades"]
        self.assertEqual([trade["side"] for trade in trades], ["BUY"] * 3)
        self.assertEqual([trade["amount"] for trade in trades], [100] * 3)
        for trade, quantity in zip(trades, [1, 1.25, 100 / 60]):
            self.assertAlmostEqual(trade["shares"], quantity)
        self.assertEqual(result["metrics"]["capture_count"], 0)

    def test_same_session_reentry_includes_exit_costs_and_distribution(self):
        observations = [Bar("2024-01-02", 100), Bar("2024-01-03", 200),
                        Bar("2024-01-04", 300, dividend=2), Bar("2024-01-05", 300)]
        result = simulate(observations, Parameters(initial_cash=1000, dca_pct=50, va_pct=5,
                                                  capture_pct=5, slippage_bps=10, fee=1))
        exit_day = [trade for trade in result["trades"] if trade["date"] == "2024-01-04"]
        self.assertEqual([trade["side"] for trade in exit_day], ["DIVIDEND", "SELL", "BUY"])
        prior_holdings = sum(trade["shares"] for trade in result["trades"][:2])
        self.assertAlmostEqual(exit_day[0]["shares"], prior_holdings)
        self.assertAlmostEqual(exit_day[0]["amount"], prior_holdings * 2)
        self.assertAlmostEqual(exit_day[1]["shares"], prior_holdings)
        self.assertAlmostEqual(exit_day[1]["cash"], result["equity"][2]["cycle_start"])
        self.assertAlmostEqual(exit_day[2]["amount"], exit_day[1]["cash"] * 0.5)
        self.assertAlmostEqual(exit_day[2]["cash"], exit_day[1]["cash"] - exit_day[2]["amount"] - 1)

    def test_fee_blocked_capture_keeps_cycle_and_ordinary_dca_budget(self):
        observations = [Bar("2024-01-01", 100), Bar("2024-01-02", 100, dividend=3000)]
        params = Parameters(initial_cash=1000, dca_pct=1, va_pct=0,
                            capture_pct=5, slippage_bps=0, fee=100)
        plan = simulate(observations, params)["next_action"]
        self.assertEqual(plan["action"], "capture")
        self.assertEqual(plan["estimated_sell_net"], 0)
        self.assertFalse(plan["buy_dollars_contingent_on_exit"])
        self.assertIn("cannot cover the sell fee", plan["buy_dollars_basis"])
        self.assertEqual(plan["buy_dollars"], 10)
        self.assertEqual(plan["estimated_buy_dollars"], 10)
        filled = simulate(observations + [Bar("2024-01-03", 100)], params)
        execution_day = [trade for trade in filled["trades"] if trade["date"] == "2024-01-03"]
        self.assertEqual([trade["side"] for trade in execution_day], ["BUY"])
        self.assertEqual(execution_day[0]["amount"], plan["estimated_buy_dollars"])
        self.assertEqual(filled["equity"][-1]["cycle_start"], 1000)
        self.assertEqual(filled["metrics"]["capture_count"], 0)
        self.assertEqual(filled["metrics"]["skipped_orders"], 1)

    def test_full_exit_still_completes_when_cash_cannot_fund_post_exit_buy_fee(self):
        # The exit realizes a loss after fees; disable the no-loss rule to test the fee path.
        result = simulate(bars([100, 400, 120]), Parameters(initial_cash=100, dca_pct=100,
                                                         va_pct=0, capture_pct=5,
                                                         slippage_bps=0, fee=50, no_loss_sales=False))
        self.assertEqual([trade["side"] for trade in result["trades"]], ["BUY", "SELL"])
        self.assertEqual(result["metrics"]["capture_count"], 1)
        self.assertEqual(result["equity"][-1]["cycle_start"], 10)
        self.assertEqual(result["equity"][-1]["cash"], 10)
        self.assertEqual(result["equity"][-1]["invested"], 0)
        self.assertEqual(result["next_action"]["action"], "hold")

    def test_next_plan_distinguishes_requested_cash_and_trim_funded_buy(self):
        params = Parameters(initial_cash=1000, dca_pct=100, va_pct=0,
                            capture_pct=1000, slippage_bps=0, fee=1)
        empty_cash = simulate(bars([100]), params)["next_action"]
        self.assertEqual(empty_cash["action"], "hold")
        self.assertEqual(empty_cash["buy_dollars"], 1000)
        self.assertEqual(empty_cash["buy_dollars_cash_capped"], 0)
        self.assertEqual(empty_cash["estimated_buy_dollars"], 0)
        funded = simulate(bars([100, 110]), params)["next_action"]
        self.assertEqual(funded["action"], "buy_and_trim")
        self.assertEqual(funded["buy_dollars_cash_capped"], 0)
        self.assertAlmostEqual(funded["estimated_sell_net"], 98.9)
        self.assertAlmostEqual(funded["estimated_buy_dollars"], 97.9)
        filled = simulate(bars([100, 110, 110]), params)
        self.assertAlmostEqual(filled["trades"][-1]["amount"], funded["estimated_buy_dollars"])

    def test_va_sells_prior_open_excess_but_keeps_position(self):
        result = simulate(bars([100, 150, 150]), Parameters(initial_cash=1000, dca_pct=10, va_pct=0,
                                                        capture_pct=1000, slippage_bps=0))
        sale = [trade for trade in result["trades"] if trade["side"] == "SELL"][0]
        self.assertEqual(sale["date"], "2024-01-03")
        self.assertEqual(sale["signal_date"], "2024-01-02")
        self.assertAlmostEqual(sale["amount"], 50)
        self.assertAlmostEqual(result["equity"][-1]["invested"], 300)
        self.assertEqual([trade["side"] for trade in result["trades"][-2:]], ["SELL", "BUY"])
        self.assertAlmostEqual(result["trades"][-1]["amount"], 100)
        self.assertEqual(result["metrics"]["capture_count"], 0)

    def test_future_open_changes_fill_but_never_prior_signal(self):
        params = Parameters(initial_cash=1000, dca_pct=10, va_pct=0, capture_pct=1000, slippage_bps=0,
                            no_loss_sales=False)
        ordinary = simulate(bars([100, 150, 150]), params)
        gap = simulate(bars([100, 150, 30]), params)
        ordinary_sale = next(trade for trade in ordinary["trades"] if trade["side"] == "SELL")
        gap_sale = next(trade for trade in gap["trades"] if trade["side"] == "SELL")
        self.assertEqual(ordinary["equity"][:2], gap["equity"][:2])
        self.assertAlmostEqual(ordinary_sale["shares"], gap_sale["shares"])
        self.assertAlmostEqual(gap_sale["price"], 30)
        # With the rule on, the same signal is held at the gapped price.
        held = simulate(bars([100, 150, 30]), replace(params, no_loss_sales=True))
        self.assertEqual(held["equity"][:2], ordinary["equity"][:2])
        self.assertEqual([trade["side"] for trade in held["trades"]][-2:], ["HOLD", "BUY"])

    def test_capture_can_lose_money_after_gap_without_no_loss_rule(self):
        result = simulate(bars([100, 200, 1]), Parameters(initial_cash=1000, dca_pct=50, va_pct=5,
                                                      capture_pct=5, slippage_bps=0, no_loss_sales=False))
        self.assertEqual(result["metrics"]["capture_count"], 1)
        sale = next(trade for trade in result["trades"] if trade["side"] == "SELL")
        self.assertEqual(sale["price"], 1)
        self.assertGreater(result["equity"][-1]["invested"], 0)
        self.assertLess(result["metrics"]["final_equity"], 1000)
        self.assertEqual(result["metrics"]["losing_sales"], 1)
        self.assertAlmostEqual(result["metrics"]["realized_gain"], 7.5 - 1000)

    def test_no_loss_rule_holds_losing_exit_and_reports_unsold_loss(self):
        params = Parameters(initial_cash=1000, dca_pct=50, va_pct=5, capture_pct=5, slippage_bps=0)
        plan = simulate(bars([100, 200]), params)["next_action"]
        self.assertEqual(plan["action"], "capture")
        self.assertFalse(plan["sale_held_at_reference_price"])
        self.assertAlmostEqual(plan["average_cost"], 1000 / 7.5)
        result = simulate(bars([100, 200, 1]), params)
        metrics = result["metrics"]
        self.assertEqual([trade["side"] for trade in result["trades"]], ["BUY", "BUY", "HOLD"])
        self.assertIn("no-loss rule", result["trades"][-1]["reason"])
        self.assertEqual((metrics["capture_count"], metrics["held_loss_sales"], metrics["losing_sales"]), (0, 1, 0))
        self.assertAlmostEqual(metrics["unrealized_gain"], 7.5 - 1000)
        self.assertAlmostEqual(metrics["worst_unrealized_loss_pct"], 99.25)
        self.assertEqual(result["equity"][-1]["cost_basis"], 1000)

    def test_no_loss_rule_counts_fees_in_the_cost_of_shares_sold(self):
        # 9.99 shares bought at 100 plus a $1 fee cost $1,000; a trim also pays $1.
        params = Parameters(initial_cash=1000, dca_pct=100, va_pct=0, capture_pct=1000, slippage_bps=0, fee=1)
        # 100.05 is above the average fill price but not above the cost with fees.
        held = simulate(bars([100, 110, 100.05]), params)
        self.assertEqual([trade["side"] for trade in held["trades"]], ["BUY", "HOLD"])
        sold = simulate(bars([100, 110, 110]), params)
        self.assertEqual([trade["side"] for trade in sold["trades"]], ["BUY", "SELL", "BUY"])
        self.assertEqual((sold["metrics"]["held_loss_sales"], sold["metrics"]["losing_sales"]), (0, 0))
        self.assertAlmostEqual(sold["metrics"]["realized_gain"], 99.9 - 1 - 1000 * (99.9 / 110) / 9.99)
        # The plan also estimates the hold at the latest price.
        plan = simulate(bars([100, 101]), params)["next_action"]
        self.assertTrue(plan["sale_held_at_reference_price"])
        self.assertEqual((plan["action"], plan["estimated_sell_net"]), ("hold", 0))

    def test_cash_interest_accrues_on_idle_cash_after_the_first_observation(self):
        params = Parameters(initial_cash=1000, dca_pct=10, slippage_bps=0)
        result = simulate(bars([100] * 3), params, cash_rates=[.01] * 3)
        self.assertAlmostEqual(result["metrics"]["cash_interest"], 9 + 8.09)
        self.assertAlmostEqual(result["metrics"]["final_equity"], 1017.09)
        self.assertEqual(simulate(bars([100] * 3), params, cash_rates=[0] * 3)["metrics"],
                         simulate(bars([100] * 3), params)["metrics"])
        for rates in ([.01] * 2, [.01, .01, 1]):
            with self.subTest(rates=rates), self.assertRaises(ValueError):
                simulate(bars([100] * 3), params, cash_rates=rates)

    def test_split_normalized_prices_do_not_double_shares(self):
        result = simulate([Bar("2024-01-01", 50), Bar("2024-01-02", 50, split=2)],
                          Parameters(initial_cash=1000, dca_pct=100, slippage_bps=0))
        self.assertAlmostEqual(result["metrics"]["final_equity"], 1000)
        self.assertAlmostEqual(result["equity"][-1]["invested"], 1000)
        self.assertEqual(result["metrics"]["trade_count"], 1)

    def test_dividend_uses_prior_holdings_and_matches_benchmark_basis(self):
        result = simulate([Bar("2024-01-01", 100, dividend=3), Bar("2024-01-02", 99, dividend=1)],
                          Parameters(initial_cash=1000, dca_pct=100, slippage_bps=0))
        self.assertAlmostEqual(result["metrics"]["dividend_cash"], 10)
        self.assertAlmostEqual(result["metrics"]["final_equity"], 1000)
        self.assertAlmostEqual(result["equity"][-1]["benchmark"], 1000)
        distributions = [trade for trade in result["trades"] if trade["side"] == "DIVIDEND"]
        self.assertEqual(len(distributions), 1)

    def test_cash_is_preserved_if_fee_makes_purchase_impossible(self):
        result = simulate(bars([100, 105]), Parameters(initial_cash=100, fee=100))
        self.assertEqual(result["metrics"]["trade_count"], 0)
        self.assertEqual(result["metrics"]["final_equity"], 100)
        self.assertEqual(result["equity"][-1]["benchmark"], 100)

    def test_costs_reduce_returns_and_accounting_reconciles(self):
        result = simulate(bars([100, 104, 110, 108]), Parameters(initial_cash=1000, dca_pct=20,
                                                             va_pct=0, slippage_bps=10, fee=1))
        cash = 1000
        for trade in result["trades"]:
            if trade["side"] != "HOLD":
                cash += (-trade["amount"] if trade["side"] == "BUY" else trade["amount"]) - trade["fee"]
            self.assertAlmostEqual(cash, trade["cash"])
        for point in result["equity"]:
            self.assertAlmostEqual(point["equity"], point["cash"] + point["invested"])
        metrics = result["metrics"]
        self.assertAlmostEqual(metrics["final_equity"], 1000 + metrics["realized_gain"] + metrics["unrealized_gain"]
                               + metrics["dividend_cash"] + metrics["cash_interest"])
        free = simulate(bars([100, 104, 110, 108]), Parameters(initial_cash=1000, dca_pct=20, va_pct=0,
                                                           slippage_bps=0, fee=0))
        self.assertLess(result["metrics"]["final_equity"], free["metrics"]["final_equity"])

    def test_invalid_prices_dates_and_parameters_fail(self):
        cases = [[], [Bar("2024-01-01", math.nan)], [Bar("2024-01-01", 0)],
                 [Bar("2024-01-02", 100), Bar("2024-01-01", 100)],
                 [Bar("2024-01-01", 100), Bar("2024-01-01", 100)],
                 [Bar("not-a-date", 100)], [Bar("2024-01-01", 100, dividend=-1)],
                 [Bar("2024-01-01", 100, split=math.inf)]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_bars(rows)
        for value in (math.nan, math.inf, True, "2", -1, 101):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Parameters(dca_pct=value)
        for value in (1, 0, "true", None):
            with self.subTest(no_loss_sales=value), self.assertRaises(ValueError):
                Parameters(no_loss_sales=value)

    def test_grid_metric_mode_matches_detailed_mode(self):
        prices = bars([100, 200, 50, 300, 90])
        params = Parameters(initial_cash=1000, dca_pct=50, capture_pct=5)
        self.assertEqual(simulate(prices, params)["metrics"], simulate(prices, params, detailed=False)["metrics"])


if __name__ == "__main__":
    unittest.main()
