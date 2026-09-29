"""Restricted Alpaca paper transport; no live endpoint or implicit retries.

The caller owns durable order intents, reconciliation, freshness and strategy
limits. This adapter adds a second cash/owned-share check immediately before a
submission. Those reads are not atomic: use one orchestrator per paper account.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
import json
import math
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .engine import SYMBOLS


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"
_MAX_RESPONSE_BYTES = 2_000_000


class BrokerError(RuntimeError):
    """Safe error metadata; ambiguous writes require lookup, never blind retry."""

    def __init__(self, message, *, status_code=None, retryable=False, ambiguous=False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.ambiguous = ambiguous


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib otherwise carries authentication headers across some redirects.
        return None


def _number(value, name):
    try:
        if isinstance(value, bool) or value is None:
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return number
    except (ValueError, TypeError, OverflowError):
        raise BrokerError(f"Broker returned an invalid {name}.") from None


def _mapping(value, required=()):
    if not isinstance(value, dict) or any(key not in value for key in required):
        raise BrokerError("Broker returned an incomplete response.")
    return value


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (AttributeError, ValueError, TypeError):
        raise BrokerError("Broker returned an invalid timestamp.") from None


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Order identifiers must contain 1–128 letters, digits, underscores or hyphens.")
    return value


def _symbol(value):
    if value not in SYMBOLS:
        raise ValueError("Paper orders and market data are limited to the supported ETF symbols.")
    return value


def _positive_decimal(value, name, decimals):
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or number > Decimal("1000000000"):
            raise ValueError
        if number != number.quantize(Decimal(1).scaleb(-decimals)):
            raise ValueError
        return number
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be positive, finite, and have at most {decimals} decimal places.") from None


def _order(value):
    value = _mapping(value, ("id", "client_order_id", "symbol", "side", "status", "filled_qty"))
    fields = ("id", "client_order_id", "symbol", "side", "status", "type", "time_in_force",
              "created_at", "updated_at", "submitted_at", "filled_at", "expired_at",
              "canceled_at", "failed_at", "replaced_at", "replaced_by", "replaces",
              "asset_class", "order_class")
    result = {key: value[key] for key in fields if key in value}
    if any(not isinstance(result[key], str) or not result[key]
           for key in ("id", "client_order_id", "symbol", "side", "status")):
        raise BrokerError("Broker returned invalid order identifiers or status.")
    for key in ("qty", "filled_qty", "filled_avg_price", "limit_price", "notional", "stop_price"):
        result[key] = None if value.get(key) is None else _number(value[key], key)
    if result["filled_qty"] is None or result["filled_qty"] < 0:
        raise BrokerError("Broker returned an invalid filled quantity.")
    if result["qty"] is not None and (result["qty"] <= 0 or result["filled_qty"] > result["qty"]):
        raise BrokerError("Broker returned inconsistent order quantities.")
    if result["side"] not in ("buy", "sell"):
        raise BrokerError("Broker returned an unsupported order side.")
    if any(result[key] is not None and result[key] <= 0
           for key in ("filled_avg_price", "limit_price", "notional", "stop_price")):
        raise BrokerError("Broker returned an invalid order price or notional.")
    if result["filled_qty"] > 0 and result["filled_avg_price"] is None:
        raise BrokerError("Broker returned filled shares without a fill price.")
    return result


class AlpacaPaperBroker:
    """Alpaca paper account using environment credentials and an explicit feed.

    SIP is the default; an unavailable subscription fails closed. IEX is allowed
    only when explicitly selected and is not a consolidated market quote.
    ``opener`` is a test seam implementing urllib's ``open(request, timeout=)``.
    """

    def __init__(self, *, api_key=None, api_secret=None, base_url=PAPER_BASE_URL,
                 timeout=15, feed="sip", opener=None):
        for origin in (base_url, os.environ.get("APCA_API_BASE_URL", PAPER_BASE_URL)):
            if not isinstance(origin, str) or origin.rstrip("/") != PAPER_BASE_URL:
                raise ValueError("Only the fixed Alpaca paper endpoint is permitted.")
        if feed not in ("sip", "iex"):
            raise ValueError("Select the sip or iex real-time market data feed explicitly.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError("Broker timeout must be between 0 and 60 seconds.")
        key = api_key if api_key is not None else os.environ.get("APCA_API_KEY_ID")
        secret = api_secret if api_secret is not None else os.environ.get("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise BrokerError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY to paper account credentials.")
        if any(not isinstance(value, str) or any(not 33 <= ord(c) <= 126 for c in value)
               for value in (key, secret)):
            raise BrokerError("Paper account credentials have an invalid format.")
        self._key, self._secret = key, secret
        self.timeout, self.feed = timeout, feed
        self._opener = opener if opener is not None else build_opener(_NoRedirect())

    @property
    def base_url(self):
        return PAPER_BASE_URL

    def _request(self, method, path, *, query=None, payload=None, market_data=False):
        if not path.startswith("/v2/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("Invalid broker request path.")
        if market_data and method != "GET":
            raise ValueError("The market data connection is read-only.")
        url = (DATA_BASE_URL if market_data else PAPER_BASE_URL) + path
        if query:
            url += "?" + urlencode({key: value for key, value in query.items() if value is not None})
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode("utf-8")
        request = Request(url, data=data, method=method, headers={
            "APCA-API-KEY-ID": self._key, "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "investbell-paper/1.0",
        })
        write = method != "GET"
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if response.geturl() != url:
                    raise BrokerError("Broker redirects are forbidden.", ambiguous=write)
                status = response.status
                if not 200 <= status < 300:
                    raise BrokerError(f"Broker request failed (HTTP {status}).", status_code=status,
                                      retryable=status == 429 or status >= 500, ambiguous=write)
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise BrokerError("Broker response exceeded its size limit.", ambiguous=write)
                if status == 204:
                    return None
                return json.loads(raw)
        except HTTPError as error:
            code = error.code
            error.close()
            # Bodies, URLs and underlying exception strings may contain secrets.
            raise BrokerError(f"Broker request failed (HTTP {code}).", status_code=code,
                              retryable=code == 429 or code >= 500,
                              ambiguous=write and code not in (400, 401, 403, 404, 422)) from None
        except (URLError, TimeoutError, OSError, HTTPException):
            raise BrokerError("Broker connection failed; reconcile before retrying a write.",
                              retryable=True, ambiguous=write) from None
        except (UnicodeError, ValueError, TypeError):
            raise BrokerError("Broker returned invalid JSON; reconcile before retrying a write.",
                              ambiguous=write) from None

    def account(self):
        keys = ("id", "status", "cash", "equity", "buying_power", "trading_blocked", "account_blocked", "currency")
        value = _mapping(self._request("GET", "/v2/account"), keys)
        result = {key: value[key] for key in keys}
        for key in ("cash", "equity", "buying_power", "last_equity", "non_marginable_buying_power"):
            if key in value:
                result[key] = _number(value[key], key)
        if any(not isinstance(result[key], bool) for key in ("trading_blocked", "account_blocked")):
            raise BrokerError("Broker returned invalid account restriction flags.")
        if "trade_suspended_by_user" in value:
            if not isinstance(value["trade_suspended_by_user"], bool):
                raise BrokerError("Broker returned an invalid user trading suspension flag.")
            result["trade_suspended_by_user"] = value["trade_suspended_by_user"]
        if any(not isinstance(result[key], str) or not result[key] for key in ("id", "status", "currency")):
            raise BrokerError("Broker returned invalid account identifiers or status.")
        return result

    def positions(self):
        values = self._request("GET", "/v2/positions")
        if not isinstance(values, list):
            raise BrokerError("Broker returned an invalid position list.")
        results, symbols = [], set()
        for value in values:
            value = _mapping(value, ("symbol", "qty", "market_value"))
            if not isinstance(value["symbol"], str) or not value["symbol"] or value["symbol"] in symbols:
                raise BrokerError("Broker returned invalid or duplicate position symbols.")
            symbols.add(value["symbol"])
            result = {"symbol": value["symbol"]}
            for key in ("qty", "market_value", "qty_available", "cost_basis", "avg_entry_price", "current_price"):
                if key in value:
                    result[key] = _number(value[key], key)
            results.append(result)
        return results

    def list_orders(self, status="all", after=None, until=None, limit=500, direction="asc"):
        if status not in ("all", "open", "closed") or direction not in ("asc", "desc"):
            raise ValueError("Invalid broker order query.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Order query limit must be an integer from 1 to 500.")
        values = self._request("GET", "/v2/orders", query={"status": status, "after": after,
                               "until": until, "limit": limit, "direction": direction, "nested": "false"})
        if not isinstance(values, list):
            raise BrokerError("Broker returned an invalid order list.")
        return [_order(value) for value in values]

    def get_order_by_client_id(self, client_order_id):
        _identifier(client_order_id)
        try:
            result = self._request("GET", "/v2/orders:by_client_order_id",
                                   query={"client_order_id": client_order_id})
        except BrokerError as error:
            if error.status_code == 404:
                return None
            raise
        order = _order(result)
        if order["client_order_id"] != client_order_id:
            raise BrokerError("Broker returned an order for the wrong client identifier.")
        return order

    def get_order(self, order_id):
        order = _order(self._request("GET", "/v2/orders/" + _identifier(order_id)))
        if order["id"] != order_id:
            raise BrokerError("Broker returned an order for the wrong identifier.")
        return order

    def submit_order(self, client_order_id, symbol, side, qty=None, *, notional=None,
                     type="limit", time_in_force="day", limit_price=None, valid_until=None,
                     pre_submit=None):
        """Submit once after inventory, callback, and deadline checks.

        ``pre_submit`` is called after all preflight network reads, so a durable
        emergency stop can still veto the write. It must raise or return False
        to block submission. The orchestrator supplies a quote/window deadline.
        """
        _identifier(client_order_id)
        _symbol(symbol)
        if side not in ("buy", "sell") or type != "limit" or time_in_force != "day":
            raise ValueError("Only buy/sell DAY limit orders are supported.")
        if notional is not None:
            raise ValueError("Use explicit share quantities for price-bounded orders.")
        quantity = _positive_decimal(qty, "qty", 9)
        price = _positive_decimal(limit_price, "limit_price", 4)
        if price >= 1 and price != price.quantize(Decimal("0.01")):
            raise ValueError("Limit prices of at least $1 must use cent increments.")
        deadline = None
        if valid_until is not None:
            deadline = _timestamp(valid_until.isoformat() if isinstance(valid_until, datetime) else valid_until)
        if pre_submit is not None and not callable(pre_submit):
            raise ValueError("pre_submit must be callable.")
        self._check_inventory(symbol, side, quantity, price)
        payload = {"client_order_id": client_order_id, "symbol": symbol, "side": side,
                   "qty": format(quantity, "f"), "type": "limit", "time_in_force": "day",
                   "limit_price": format(price, "f"), "extended_hours": False, "order_class": "simple"}
        if pre_submit is not None and pre_submit() is False:
            raise BrokerError("Order submission was blocked by the pre-submit safety check.")
        if deadline is not None and datetime.now(timezone.utc) >= deadline:
            raise BrokerError("Order quote or market window expired during broker preflight.")
        result = self._request("POST", "/v2/orders", payload=payload)
        try:
            order = _order(result)
            if any(order.get(key) != payload[key] for key in
                   ("client_order_id", "symbol", "side", "type", "time_in_force")):
                raise BrokerError("Broker order identity does not match the request.")
            if order["qty"] is None or Decimal(str(order["qty"])) != quantity or order["limit_price"] is None or Decimal(str(order["limit_price"])) != price:
                raise BrokerError("Broker order size or price does not match the request.")
            return order
        except BrokerError:
            raise BrokerError("Order response could not be validated; reconcile by client order ID.",
                              ambiguous=True) from None

    def _check_inventory(self, symbol, side, quantity, price):
        account = self.account()
        positions = self.positions()
        orders = self.list_orders(status="open")
        if account["status"] != "ACTIVE" or account["currency"] != "USD" or account["trading_blocked"] or account["account_blocked"] or account.get("trade_suspended_by_user"):
            raise BrokerError("The paper account is not active and unrestricted in USD.")
        if account["cash"] < 0 or any(position["qty"] < 0 for position in positions):
            raise BrokerError("Borrowed cash or short positions are not permitted.")
        if len(orders) >= 500:
            raise BrokerError("Cannot establish all open order reservations; trading is blocked.")
        reserved_cash, reserved_shares = Decimal(0), Decimal(0)
        for order in orders:
            if order.get("qty") is None or order.get("type") != "limit" or order.get("limit_price") is None:
                raise BrokerError("An open order has unsupported cash/share reservations.")
            remaining = Decimal(str(order["qty"])) - Decimal(str(order["filled_qty"]))
            if order["side"] == "buy":
                reserved_cash += remaining * Decimal(str(order["limit_price"]))
            elif order["symbol"] == symbol:
                reserved_shares += remaining
        if side == "buy":
            cash = min(Decimal(str(account["cash"])) - reserved_cash, Decimal(str(account["buying_power"])))
            if quantity * price > cash:
                raise BrokerError("Order exceeds unreserved cash; margin is not permitted.")
        else:
            owned = sum((Decimal(str(position["qty"])) for position in positions if position["symbol"] == symbol), Decimal(0))
            if quantity > owned - reserved_shares:
                raise BrokerError("Order exceeds unreserved owned shares; short selling is not permitted.")

    def cancel_order(self, order_id):
        return self._request("DELETE", "/v2/orders/" + _identifier(order_id))

    def clock(self):
        value = _mapping(self._request("GET", "/v2/clock"), ("timestamp", "is_open", "next_open", "next_close"))
        if not isinstance(value["is_open"], bool):
            raise BrokerError("Broker returned an invalid market clock.")
        for field in ("timestamp", "next_open", "next_close"):
            _timestamp(value[field])
        return {key: value[key] for key in ("timestamp", "is_open", "next_open", "next_close")}

    def calendar(self, start, end):
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first.isoformat() != start or last.isoformat() != end or last < first:
            raise ValueError("Calendar dates must be ascending YYYY-MM-DD values.")
        values = self._request("GET", "/v2/calendar", query={"start": start, "end": end})
        if not isinstance(values, list):
            raise BrokerError("Broker returned an invalid market calendar.")
        results, previous = [], ""
        for value in values:
            value = _mapping(value, ("date", "open", "close"))
            try:
                day = date.fromisoformat(value["date"]).isoformat()
                opens, closes = time.fromisoformat(value["open"]), time.fromisoformat(value["close"])
                if not start <= day <= end or day <= previous or opens >= closes:
                    raise ValueError
            except (ValueError, TypeError):
                raise BrokerError("Broker returned an invalid market calendar session.") from None
            results.append({key: value[key] for key in ("date", "open", "close")})
            previous = day
        return results

    def latest_quote(self, symbol):
        _symbol(symbol)
        value = _mapping(self._request("GET", f"/v2/stocks/{symbol}/quotes/latest",
                                      query={"feed": self.feed}, market_data=True), ("quote", "symbol"))
        if value["symbol"] != symbol:
            raise BrokerError("Broker returned a quote for the wrong symbol.")
        quote = _mapping(value["quote"], ("t", "bp", "ap", "bs", "as"))
        _timestamp(quote["t"])
        result = {"symbol": symbol, "timestamp": quote["t"], "feed": self.feed}
        for target, source in (("bid_price", "bp"), ("ask_price", "ap"), ("bid_size", "bs"), ("ask_size", "as")):
            result[target] = _number(quote[source], target)
            if result[target] <= 0:
                raise BrokerError("Broker returned an empty or invalid quote.")
        if result["bid_price"] > result["ask_price"]:
            raise BrokerError("Broker returned a crossed quote.")
        return result

    def daily_bar(self, symbol, session):
        _symbol(symbol)
        day = date.fromisoformat(session)
        if day.isoformat() != session:
            raise ValueError("Session must use YYYY-MM-DD.")
        value = _mapping(self._request("GET", f"/v2/stocks/{symbol}/bars", market_data=True,
                         query={"timeframe": "1Day", "start": session,
                                "end": (day + timedelta(days=1)).isoformat(), "adjustment": "raw",
                                "feed": self.feed, "sort": "asc", "limit": 10}), ("bars", "symbol"))
        if value["symbol"] != symbol or not isinstance(value["bars"], list) or value.get("next_page_token"):
            raise BrokerError("Broker returned an incomplete daily bar response.")
        matches = []
        for row in value["bars"]:
            row = _mapping(row, ("t", "o"))
            if _timestamp(row["t"]).astimezone(ZoneInfo("America/New_York")).date() == day:
                matches.append(row)
        if len(matches) != 1:
            raise BrokerError("Broker has no unique daily opening bar for the requested session.")
        opening = _number(matches[0]["o"], "daily open")
        if opening <= 0:
            raise BrokerError("Broker returned an invalid daily open.")
        return {"symbol": symbol, "date": session, "timestamp": matches[0]["t"],
                "open": opening, "feed": self.feed, "adjustment": "raw"}
