from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from investbell.broker import AlpacaPaperBroker, BrokerError, PAPER_BASE_URL, _NoRedirect


ACCOUNT = {"id": "paper-account", "status": "ACTIVE", "cash": "1000", "equity": "2000",
           "buying_power": "4000", "trading_blocked": False, "account_blocked": False, "currency": "USD"}
ORDER = {"id": "broker-order", "client_order_id": "intent-1", "symbol": "SPY", "side": "buy",
         "status": "new", "qty": "0.5", "filled_qty": "0", "filled_avg_price": None,
         "limit_price": "600.01", "type": "limit", "time_in_force": "day"}


class FakeOpener:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request)
        route = self.routes[(request.method, urlsplit(request.full_url).path)]
        if isinstance(route, Exception):
            raise route
        value = route(request) if callable(route) else route
        response = Mock()
        response.status = 204 if value is None else 200
        response.geturl.return_value = request.full_url
        response.read.return_value = b"" if value is None else json.dumps(value).encode()
        return nullcontext(response)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.routes = {("GET", "/v2/account"): ACCOUNT,
                       ("GET", "/v2/positions"): [{"symbol": "SPY", "qty": "2", "market_value": "1200"}],
                       ("GET", "/v2/orders"): [],
                       ("POST", "/v2/orders"): lambda request: {**ORDER, **json.loads(request.data)}}
        self.opener = FakeOpener(self.routes)
        self.broker = AlpacaPaperBroker(api_key="test-key", api_secret="test-secret", opener=self.opener)

    def error(self, code):
        return HTTPError(PAPER_BASE_URL + "/v2/orders", code, "test-secret", {}, io.BytesIO(b"test-secret"))

    def test_fixed_paper_origin_and_environment_credentials(self):
        with patch.dict(os.environ, {"APCA_API_KEY_ID": "env-key", "APCA_API_SECRET_KEY": "env-secret"}):
            broker = AlpacaPaperBroker(opener=self.opener)
            self.assertEqual(broker.account()["cash"], 1000.0)
        request = self.opener.calls[0]
        self.assertEqual(request.full_url, PAPER_BASE_URL + "/v2/account")
        self.assertEqual(request.get_header("Apca-api-key-id"), "env-key")
        self.assertEqual(request.get_header("Apca-api-secret-key"), "env-secret")
        self.assertNotIn("env-secret", repr(broker))

    def test_live_and_custom_origins_are_rejected_before_network(self):
        for origin in ("https://api.alpaca.markets", "https://paper-api.alpaca.markets.evil.test",
                       "https://paper-api.alpaca.markets/path", "http://paper-api.alpaca.markets", "//evil.test"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                AlpacaPaperBroker(api_key="key", api_secret="secret", base_url=origin, opener=self.opener)
        with patch.dict(os.environ, {"APCA_API_BASE_URL": "https://api.alpaca.markets"}):
            with self.assertRaises(ValueError):
                AlpacaPaperBroker(api_key="key", api_secret="secret", opener=self.opener)
        self.assertEqual(self.opener.calls, [])

    def test_credentials_are_required_and_header_injection_is_rejected(self):
        with self.assertRaises(BrokerError):
            AlpacaPaperBroker(opener=self.opener)
        with self.assertRaises(BrokerError) as caught:
            AlpacaPaperBroker(api_key="key", api_secret="test-secret\nAnother: value", opener=self.opener)
        self.assertNotIn("test-secret", str(caught.exception))

    def test_redirect_handler_refuses_cross_origin_redirect(self):
        request = Request(PAPER_BASE_URL + "/v2/account", headers={"APCA-API-SECRET-KEY": "test-secret"})
        self.assertIsNone(_NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://evil.test"))
        with patch("investbell.broker.build_opener", return_value=self.opener) as build:
            AlpacaPaperBroker(api_key="key", api_secret="secret")
        self.assertIsInstance(build.call_args.args[0], _NoRedirect)
        self.routes[("GET", "/v2/account")] = self.error(302)
        with self.assertRaises(BrokerError) as caught:
            self.broker.account()
        self.assertEqual(caught.exception.status_code, 302)
        self.assertEqual(len(self.opener.calls), 1)

    def test_fractional_limit_submission_uses_exact_bounded_payload(self):
        result = self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01")
        self.assertEqual(result["qty"], 0.5)
        request = self.opener.calls[-1]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {
            "client_order_id": "intent-1", "symbol": "SPY", "side": "buy", "qty": "0.5",
            "type": "limit", "time_in_force": "day", "limit_price": "600.01",
            "extended_hours": False, "order_class": "simple"})

    def test_risky_or_invalid_orders_do_not_reach_network(self):
        cases = ({"symbol": "TQQQ"}, {"symbol": "SPY/../../account"}, {"side": "short"},
                 {"type": "market"}, {"time_in_force": "gtc"}, {"notional": 100},
                 {"qty": 0}, {"qty": True}, {"qty": "nan"}, {"qty": "0.0000000001"},
                 {"limit_price": -1}, {"limit_price": "Infinity"}, {"limit_price": "600.001"},
                 {"client_order_id": "../account"})
        for changes in cases:
            kwargs = {"client_order_id": "intent-1", "symbol": "SPY", "side": "buy", "qty": 1, "limit_price": 600}
            kwargs.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.broker.submit_order(**kwargs)
        self.assertEqual(self.opener.calls, [])

    def test_cash_blocks_margin_despite_large_buying_power(self):
        with self.assertRaisesRegex(BrokerError, "cash"):
            self.broker.submit_order("intent-1", "SPY", "buy", 2, limit_price=600)
        self.assertFalse(any(call.method == "POST" for call in self.opener.calls))

    def test_pending_buys_reserve_cash_and_partial_fills_reduce_reservation(self):
        self.routes[("GET", "/v2/orders")] = [{**ORDER, "qty": "1", "filled_qty": "0.5", "filled_avg_price": "600", "limit_price": "600"}]
        self.broker.submit_order("intent-2", "SPY", "buy", 1, limit_price=600)
        self.routes[("GET", "/v2/orders")] = [{**ORDER, "qty": "1", "limit_price": "600"}]
        with self.assertRaisesRegex(BrokerError, "cash"):
            self.broker.submit_order("intent-3", "SPY", "buy", 1, limit_price=600)

    def test_sells_cannot_exceed_owned_shares_or_open_sell_reservations(self):
        with self.assertRaisesRegex(BrokerError, "short selling"):
            self.broker.submit_order("intent-1", "SPY", "sell", 3, limit_price=600)
        self.routes[("GET", "/v2/orders")] = [{**ORDER, "side": "sell", "qty": "1.5", "filled_qty": "0.25", "filled_avg_price": "600"}]
        with self.assertRaisesRegex(BrokerError, "short selling"):
            self.broker.submit_order("intent-1", "SPY", "sell", 1, limit_price=600)

    def test_restricted_account_and_unsupported_reservations_fail_closed(self):
        self.routes[("GET", "/v2/account")] = {**ACCOUNT, "trading_blocked": True}
        with self.assertRaises(BrokerError):
            self.broker.submit_order("intent-1", "SPY", "buy", 1, limit_price=600)
        self.routes[("GET", "/v2/account")] = ACCOUNT
        self.routes[("GET", "/v2/orders")] = [{**ORDER, "type": "market", "limit_price": None}]
        with self.assertRaisesRegex(BrokerError, "reservations"):
            self.broker.submit_order("intent-1", "SPY", "buy", 1, limit_price=600)

    def test_only_client_order_lookup_maps_404_to_absence(self):
        self.routes[("GET", "/v2/orders:by_client_order_id")] = self.error(404)
        self.assertIsNone(self.broker.get_order_by_client_id("intent-1"))
        self.routes[("GET", "/v2/orders/broker-order")] = self.error(404)
        with self.assertRaises(BrokerError) as caught:
            self.broker.get_order("broker-order")
        self.assertEqual(caught.exception.status_code, 404)
        self.routes[("GET", "/v2/orders:by_client_order_id")] = self.error(401)
        with self.assertRaises(BrokerError):
            self.broker.get_order_by_client_id("intent-1")

    def test_http_rate_limit_is_safe_and_never_automatically_retried(self):
        self.routes[("GET", "/v2/account")] = self.error(429)
        with self.assertRaises(BrokerError) as caught:
            self.broker.account()
        self.assertTrue(caught.exception.retryable)
        self.assertFalse(caught.exception.ambiguous)
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(len(self.opener.calls), 1)

    def test_timeout_after_submission_is_ambiguous_and_never_retried(self):
        self.routes[("POST", "/v2/orders")] = TimeoutError("test-secret")
        with self.assertRaises(BrokerError) as caught:
            self.broker.submit_order("intent-1", "SPY", "buy", 1, limit_price=600)
        self.assertTrue(caught.exception.ambiguous)
        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertEqual(sum(call.method == "POST" for call in self.opener.calls), 1)

    def test_definite_rejection_and_server_failure_have_different_ambiguity(self):
        for code, ambiguous in ((422, False), (403, False), (500, True), (429, True)):
            self.routes[("POST", "/v2/orders")] = self.error(code)
            with self.subTest(code=code), self.assertRaises(BrokerError) as caught:
                self.broker.submit_order("intent-1", "SPY", "buy", 1, limit_price=600)
            self.assertEqual(caught.exception.ambiguous, ambiguous)

    def test_invalid_successful_order_response_stays_ambiguous(self):
        self.routes[("POST", "/v2/orders")] = {"unexpected": "value"}
        with self.assertRaises(BrokerError) as caught:
            self.broker.submit_order("intent-1", "SPY", "buy", 1, limit_price=600)
        self.assertTrue(caught.exception.ambiguous)

    def test_wrong_order_identity_or_size_after_submission_is_ambiguous(self):
        for change in ({"client_order_id": "another-intent"}, {"qty": "20"}, {"limit_price": "650"}):
            self.routes[("POST", "/v2/orders")] = {**ORDER, **change}
            with self.subTest(change=change), self.assertRaises(BrokerError) as caught:
                self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01")
            self.assertTrue(caught.exception.ambiguous)

    def test_expired_quote_deadline_blocks_post_after_preflight(self):
        deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.assertRaisesRegex(BrokerError, "expired"):
            self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01", valid_until=deadline)
        self.assertFalse(any(call.method == "POST" for call in self.opener.calls))
        future = datetime.now(timezone.utc) + timedelta(seconds=60)
        self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01", valid_until=future.isoformat())

    def test_quote_can_expire_during_network_preflight(self):
        class ControlledDateTime(datetime):
            current = datetime(2026, 9, 23, 14, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current

        deadline = ControlledDateTime.current + timedelta(seconds=10)
        original = self.broker._check_inventory

        def slow_preflight(*args):
            original(*args)
            ControlledDateTime.current += timedelta(seconds=20)

        with patch("investbell.broker.datetime", ControlledDateTime), patch.object(self.broker, "_check_inventory", slow_preflight):
            with self.assertRaisesRegex(BrokerError, "expired"):
                self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01", valid_until=deadline.isoformat())
        self.assertEqual(len(self.opener.calls), 3)
        self.assertFalse(any(call.method == "POST" for call in self.opener.calls))

    def test_emergency_stop_callback_can_veto_after_preflight(self):
        def stop():
            self.assertEqual(len(self.opener.calls), 3)
            raise RuntimeError("Emergency stop active")

        with self.assertRaisesRegex(RuntimeError, "Emergency stop"):
            self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01", pre_submit=stop)
        self.assertFalse(any(call.method == "POST" for call in self.opener.calls))
        with self.assertRaisesRegex(BrokerError, "safety check"):
            self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01", pre_submit=lambda: False)

    def test_user_suspension_is_retained_and_blocks_submission(self):
        self.routes[("GET", "/v2/account")] = {**ACCOUNT, "trade_suspended_by_user": True}
        self.assertTrue(self.broker.account()["trade_suspended_by_user"])
        with self.assertRaisesRegex(BrokerError, "unrestricted"):
            self.broker.submit_order("intent-1", "SPY", "buy", "0.5", limit_price="600.01")

    def test_incomplete_http_response_does_not_expose_body(self):
        self.routes[("POST", "/v2/orders")] = IncompleteRead(b"test-secret", 100)
        with self.assertRaises(BrokerError) as caught:
            self.broker.submit_order("intent-1", "SPY", "buy", 1, limit_price=600)
        self.assertTrue(caught.exception.ambiguous)
        self.assertNotIn("test-secret", str(caught.exception))

    def test_negative_order_reservation_and_duplicate_positions_fail_closed(self):
        self.routes[("GET", "/v2/orders")] = [{**ORDER, "limit_price": "-600"}]
        with self.assertRaises(BrokerError):
            self.broker.submit_order("intent-1", "SPY", "buy", 1, limit_price=600)
        self.routes[("GET", "/v2/orders")] = []
        self.routes[("GET", "/v2/positions")] *= 2
        with self.assertRaises(BrokerError):
            self.broker.positions()

    def test_transport_and_non_finite_account_values_fail_closed(self):
        self.routes[("GET", "/v2/account")] = URLError("test-secret")
        with self.assertRaises(BrokerError) as caught:
            self.broker.account()
        self.assertNotIn("test-secret", str(caught.exception))
        for value in (None, "nan", "Infinity", True):
            self.routes[("GET", "/v2/account")] = {**ACCOUNT, "cash": value}
            with self.subTest(value=value), self.assertRaises(BrokerError):
                self.broker.account()

    def test_order_query_and_cancellation_use_expected_endpoints(self):
        self.routes[("GET", "/v2/orders")] = [ORDER]
        result = self.broker.list_orders(status="open", after="2026-09-01T00:00:00Z", limit=100)
        query = parse_qs(urlsplit(self.opener.calls[-1].full_url).query)
        self.assertEqual(query["after"], ["2026-09-01T00:00:00Z"])
        self.assertEqual(query["status"], ["open"])
        self.assertEqual(result[0]["limit_price"], 600.01)
        self.routes[("DELETE", "/v2/orders/broker-order")] = None
        self.assertIsNone(self.broker.cancel_order("broker-order"))
        self.assertEqual(self.opener.calls[-1].method, "DELETE")

    def test_quote_uses_explicit_sip_feed_and_preserves_timestamp(self):
        quote = {"symbol": "SPY", "quote": {"t": "2026-09-23T14:00:00Z", "bp": 599.99,
                                              "ap": 600.01, "bs": 10, "as": 20}}
        self.routes[("GET", "/v2/stocks/SPY/quotes/latest")] = quote
        result = self.broker.latest_quote("SPY")
        self.assertEqual(result["timestamp"], quote["quote"]["t"])
        self.assertEqual(result["ask_price"], 600.01)
        self.assertEqual(result["feed"], "sip")
        self.assertEqual(urlsplit(self.opener.calls[-1].full_url).netloc, "data.alpaca.markets")
        self.assertEqual(parse_qs(urlsplit(self.opener.calls[-1].full_url).query)["feed"], ["sip"])
        broker = AlpacaPaperBroker(api_key="key", api_secret="secret", feed="iex", opener=self.opener)
        self.assertEqual(broker.latest_quote("SPY")["feed"], "iex")
        with self.assertRaises(ValueError):
            AlpacaPaperBroker(api_key="key", api_secret="secret", feed="delayed_sip")

    def test_quote_missing_entitlement_does_not_fall_back_to_another_feed(self):
        self.routes[("GET", "/v2/stocks/SPY/quotes/latest")] = self.error(403)
        with self.assertRaises(BrokerError):
            self.broker.latest_quote("SPY")
        self.assertEqual(len(self.opener.calls), 1)

    def test_crossed_quote_and_naive_timestamp_are_rejected(self):
        for timestamp, bid in (("2026-09-23T14:00:00Z", 601), ("2026-09-23T14:00:00", 599)):
            self.routes[("GET", "/v2/stocks/SPY/quotes/latest")] = {
                "symbol": "SPY", "quote": {"t": timestamp, "bp": bid, "ap": 600, "bs": 1, "as": 1}}
            with self.subTest(timestamp=timestamp), self.assertRaises(BrokerError):
                self.broker.latest_quote("SPY")

    def test_daily_open_is_raw_and_requires_exact_new_york_session(self):
        self.routes[("GET", "/v2/stocks/SPY/bars")] = {
            "symbol": "SPY", "bars": [{"t": "2026-09-23T04:00:00Z", "o": 600},
                                       {"t": "2026-09-24T04:00:00Z", "o": 601}], "next_page_token": None}
        result = self.broker.daily_bar("SPY", "2026-09-23")
        self.assertEqual(result["open"], 600)
        self.assertEqual(result["date"], "2026-09-23")
        self.assertEqual(result["adjustment"], "raw")
        query = parse_qs(urlsplit(self.opener.calls[-1].full_url).query)
        self.assertEqual(query["adjustment"], ["raw"])
        self.assertEqual(query["timeframe"], ["1Day"])
        self.assertEqual(query["feed"], ["sip"])
        for bars in ([], [{"t": "2026-09-22T04:00:00Z", "o": 600}]):
            self.routes[("GET", "/v2/stocks/SPY/bars")] = {"symbol": "SPY", "bars": bars}
            with self.assertRaises(BrokerError):
                self.broker.daily_bar("SPY", "2026-09-23")

    def test_calendar_preserves_early_close_and_clock_rejects_naive_time(self):
        session = {"date": "2026-11-27", "open": "09:30", "close": "13:00"}
        self.routes[("GET", "/v2/calendar")] = [session]
        self.assertEqual(self.broker.calendar("2026-11-26", "2026-11-28"), [session])
        clock = {"timestamp": "2026-11-27T10:00:00-05:00", "is_open": True,
                 "next_open": "2026-11-30T09:30:00-05:00", "next_close": "2026-11-27T13:00:00-05:00"}
        self.routes[("GET", "/v2/clock")] = clock
        self.assertEqual(self.broker.clock(), clock)
        self.routes[("GET", "/v2/clock")] = {**clock, "timestamp": "2026-11-27T10:00:00"}
        with self.assertRaises(BrokerError):
            self.broker.clock()


if __name__ == "__main__":
    unittest.main()
