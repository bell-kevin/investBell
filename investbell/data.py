"""Yahoo daily opens, explicit synthetic fixtures, and provenance-aware caching.

Also serves Ken French's daily US market returns (1926 onward) and a simulated
3x series built from them, for the research lab only.
"""

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import io
import json
import math
from pathlib import Path
import random
import sqlite3
import urllib.request
import zipfile

from .calendar import CALENDAR_NAME, CalendarUnavailable, expected_sessions, latest_completed_session
from .engine import Bar, SYMBOLS, validate_bars


class DataUnavailable(RuntimeError):
    """A requested external source could not supply valid observations."""


# Research lab only. Paper trading, model fitting and daily reports accept only
# engine.SYMBOLS. First sessions are the earliest Yahoo daily bars.
LEVERAGED_ETFS = {
    "SSO": {"leverage": 2, "index": "S&P 500", "first_session": "2006-06-21"},
    "SPXL": {"leverage": 3, "index": "S&P 500", "first_session": "2008-11-05"},
    "UPRO": {"leverage": 3, "index": "S&P 500", "first_session": "2009-06-25"},
    "QLD": {"leverage": 2, "index": "Nasdaq-100", "first_session": "2006-06-21"},
    "TQQQ": {"leverage": 3, "index": "Nasdaq-100", "first_session": "2010-02-11"},
    "UDOW": {"leverage": 3, "index": "Dow Jones Industrial Average", "first_session": "2010-02-11"},
}

FRENCH_URL = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
              "F-F_Research_Data_Factors_daily_CSV.zip")
# The 3x construction matches scripts/century_backtest.py and docs/century-backtest.md.
LEVERAGED_EXPENSE = 0.0095
MARKET_SERIES = {
    "us_market": {"leverage": 1, "symbol": "US market",
                  "label": "US MARKET · KEN FRENCH DAILY · 1x"},
    "us_market_3x": {"leverage": 3, "symbol": "US market 3x (simulated)",
                     "label": "SIMULATED 3x US MARKET · not a real fund"},
}


def fetch_french():
    try:
        request = urllib.request.Request(FRENCH_URL, headers={"User-Agent": "investBell research lab"})
        with urllib.request.urlopen(request, timeout=60) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        return archive.read(archive.namelist()[0]).decode("latin1")
    except Exception as error:
        raise DataUnavailable(f"Ken French data download failed ({type(error).__name__}). Retry later.") from error


def parse_french(text):
    """Daily (date, market return above T-bills, T-bill return) rows, as fractions."""
    rows = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) == 5 and len(fields[0]) == 8 and fields[0].isdigit():
            try:
                rows.append((f"{fields[0][:4]}-{fields[0][4:6]}-{fields[0][6:]}",
                             float(fields[1]) / 100, float(fields[4]) / 100))
            except ValueError:
                raise DataUnavailable("The Ken French daily file contains an unreadable row.") from None
    if len(rows) < 1000 or any(later[0] <= earlier[0] for earlier, later in zip(rows, rows[1:])):
        raise DataUnavailable("The Ken French daily file has an unexpected format.")
    return rows


def market_series(rows, leverage, start, end):
    """Total-return levels rebased to 100, with each day's T-bill rate for idle cash.

    The 3x series earns 3 x the market's return above T-bills plus the T-bill
    rate, less expenses, reset daily. No such fund existed before 2006.
    """
    window = [row for row in rows if start <= row[0] < end]
    if not window:
        raise ValueError(f"The US market series covers {rows[0][0]} to {rows[-1][0]}. Choose dates in that range.")
    level, bars, rates = 100.0, [], []
    for index, (day, excess, rate) in enumerate(window):
        if index:
            daily = excess + rate if leverage == 1 else max(-0.999, rate + leverage * excess - LEVERAGED_EXPENSE / 252)
            level *= 1 + daily
        bars.append(Bar(day, level))
        rates.append(rate)
    try:
        return validate_bars(bars), rates
    except ValueError:
        raise ValueError("The simulated level left the supported range. Choose a shorter period.") from None


def parse_range(start, end):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, TypeError):
        raise ValueError("start and end must be ISO dates (YYYY-MM-DD).") from None
    if first.isoformat() != start or last.isoformat() != end:
        raise ValueError("start and end must use YYYY-MM-DD.")
    if not first < last:
        raise ValueError("end must follow start; end is exclusive.")
    if (last - first).days > 20 * 366:
        raise ValueError("Choose a period of at most 20 years.")
    return first, last


def completed_end(now=None):
    """Exclusive date bound after the latest verified, completed exchange session."""
    try:
        return latest_completed_session(now) + timedelta(days=1)
    except CalendarUnavailable as error:
        raise DataUnavailable(str(error)) from error


def _validate_sessions(bars, start, end, sessions, *, label):
    if any(not start <= bar.date < end for bar in bars):
        raise DataUnavailable(f"{label} observations fall outside the requested completed-session range. Refresh the data.")
    observed, expected = {bar.date for bar in bars}, set(sessions)
    extra = sorted(observed - expected)
    if extra:
        raise DataUnavailable(f"{label} observations include non-session dates: {', '.join(extra[:3])}. Refresh the data.")
    missing = sorted(expected - observed)
    if missing:
        raise DataUnavailable(
            f"{label} observations are incomplete: missing {len(missing)} exchange session(s), "
            f"including {', '.join(missing[:3])}. Retry or refresh the data; trading must wait.")


def demo_bars(start, end):
    """Synthetic weekday observations; never masquerades as Yahoo history.

    Anchored to an absolute date so overlapping requests agree. The simulated
    price path is a UI/engine fixture, not a market or return forecast.
    """
    first, last = parse_range(start, end)
    epoch = date(1900, 1, 1)
    if first < epoch or last > date(2101, 1, 1):
        raise ValueError("Demo dates must be between 1900-01-01 and 2101-01-01.")
    observations = []
    day = first
    while day < last:
        if day.weekday() < 5:
            n = (day - epoch).days
            noise = random.Random(n + 42017).uniform(-0.007, 0.007)
            value = 110 * math.exp(0.00010 * (n - 43829) + 0.15 * math.sin(n / 91) + 0.045 * math.sin(n / 13) + noise)
            observations.append(Bar(day.isoformat(), round(value, 6)))
        day += timedelta(days=1)
    return validate_bars(observations)


def fetch_yahoo(symbol, start, end):
    try:
        import yfinance as yf
    except ImportError:
        raise DataUnavailable("Yahoo support needs the dependencies: pip install -r requirements.txt") from None
    try:
        frame = yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False,
                                         back_adjust=False, actions=True, repair=False,
                                         keepna=True, timeout=20)
        if frame.empty:
            raise DataUnavailable("Yahoo returned no rows. Check the dates or retry later; no synthetic data was substituted.")
        rows = []
        for timestamp, row in frame.iterrows():
            observed = timestamp.date().isoformat()
            if not start <= observed < end:
                continue
            # Close/Adj Close/High/Low are deliberately neither persisted nor
            # passed to the engine. Splits are informational in this basis.
            rows.append(Bar(observed, float(row["Open"]),
                            float(row.get("Dividends", 0)) + float(row.get("Capital Gains", 0)),
                            float(row.get("Stock Splits", 0))))
        return validate_bars(rows)
    except DataUnavailable:
        raise
    except Exception as error:
        raise DataUnavailable(f"Yahoo daily-open download failed ({type(error).__name__}). Retry later; no synthetic data was substituted.") from error


class PriceStore:
    """Cache whole snapshots instead of mixing incompatible split-adjustment eras."""

    def __init__(self, path="data/market.sqlite3", *, ttl_seconds=86400, fetcher=None,
                 reference_fetcher=None, reference_ttl_seconds=7 * 86400):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.fetcher = fetcher or fetch_yahoo
        self.reference_fetcher = reference_fetcher or fetch_french
        self.reference_ttl_seconds = reference_ttl_seconds
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS reference_files (
                name TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                symbol TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
                fetched_at TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                adapter_version TEXT,
                PRIMARY KEY (symbol, start, end))""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(snapshots)")}
            if "adapter_version" not in columns:
                connection.execute("ALTER TABLE snapshots ADD COLUMN adapter_version TEXT")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def load(self, symbol, start, end, *, now=None, refresh=False):
        symbol = str(symbol).upper()
        if symbol not in SYMBOLS and symbol not in LEVERAGED_ETFS:
            raise ValueError("Only the configured broad-index ETF allowlist is supported.")
        if symbol in LEVERAGED_ETFS and start < LEVERAGED_ETFS[symbol]["first_session"]:
            raise ValueError(f"{symbol} began trading on {LEVERAGED_ETFS[symbol]['first_session']}. "
                             "Choose a later start, or the simulated 3x US market for earlier years.")
        first, requested_end = parse_range(start, end)
        now = now or datetime.now(timezone.utc)
        effective_end = min(requested_end, completed_end(now))
        if first >= effective_end:
            raise ValueError("The range contains no completed sessions yet.")
        end = effective_end.isoformat()
        try:
            sessions = expected_sessions(start, end)
        except CalendarUnavailable as error:
            raise DataUnavailable(str(error)) from error
        if not sessions:
            raise ValueError("The range contains no completed exchange sessions.")
        cached = False
        with self.connect() as connection:
            row = connection.execute("SELECT fetched_at, digest, payload, adapter_version FROM snapshots WHERE symbol=? AND start=? AND end=?",
                                     (symbol, start, end)).fetchone()
        if row and not refresh:
            fetched_at, digest, payload, adapter_version = row
            try:
                downloaded_at = datetime.fromisoformat(fetched_at)
                if downloaded_at.tzinfo is None:
                    raise ValueError("Missing timezone")
                age = (now - downloaded_at).total_seconds()
            except (TypeError, ValueError) as error:
                raise DataUnavailable("The cached data timestamp is invalid. Refresh the data.") from error
            if 0 <= age < self.ttl_seconds:
                if hashlib.sha256(payload.encode()).hexdigest() != digest:
                    raise DataUnavailable("The cached data checksum does not match. Refresh the data.")
                try:
                    bars = validate_bars(Bar(**item) for item in json.loads(payload))
                except (TypeError, ValueError, KeyError) as error:
                    raise DataUnavailable("Cached observations are invalid. Refresh the data.") from error
                _validate_sessions(bars, start, end, sessions, label="Cached")
                cached = True
        if not cached:
            try:
                bars = validate_bars(self.fetcher(symbol, start, end))
            except (TypeError, ValueError) as error:
                raise DataUnavailable("Downloaded daily opening observations are invalid. Retry later.") from error
            _validate_sessions(bars, start, end, sessions, label="Downloaded")
            payload = json.dumps([asdict(bar) for bar in bars], separators=(",", ":"), allow_nan=False)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            fetched_at = now.isoformat()
            try:
                adapter_version = version("yfinance") if self.fetcher is fetch_yahoo else None
            except PackageNotFoundError:
                adapter_version = None
            with self.connect() as connection:
                connection.execute("""INSERT OR REPLACE INTO snapshots
                    (symbol, start, end, fetched_at, digest, payload, adapter_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                   (symbol, start, end, fetched_at, digest, payload, adapter_version))
        source = {"kind": "yahoo", "label": "Yahoo Finance · daily opens", "symbol": symbol,
                  "cached": cached, "fetched_at": fetched_at, "sha256": digest,
                  "yfinance_version": adapter_version,
                  "calendar": CALENDAR_NAME, "session_coverage": "complete",
                  "expected_first_session": sessions[0], "expected_last_session": sessions[-1],
                  "expected_session_count": len(sessions),
                  "exchange_calendars_version": version("exchange-calendars"),
                  "price_basis": "split-adjusted, not dividend-adjusted; normalized share units",
                  "requested_end": requested_end.isoformat(), "effective_end": end,
                  "end_exclusive": True}
        return bars, source

    def load_market(self, kind, start, end, *, now=None, refresh=False):
        """Ken French daily US market series (1x or simulated 3x) and T-bill rates."""
        if kind not in MARKET_SERIES:
            raise ValueError("Unknown market series.")
        parse_range(start, end)
        now = now or datetime.now(timezone.utc)
        with self.connect() as connection:
            row = connection.execute("SELECT fetched_at, digest, payload FROM reference_files WHERE name='french_daily'").fetchone()
        cached = False
        if row and not refresh:
            fetched_at, digest, text = row
            age = (now - datetime.fromisoformat(fetched_at)).total_seconds()
            if 0 <= age < self.reference_ttl_seconds:
                if hashlib.sha256(text.encode()).hexdigest() != digest:
                    raise DataUnavailable("The cached Ken French file checksum does not match. Refresh the data.")
                cached = True
        if not cached:
            text = self.reference_fetcher()
            parse_french(text)
            fetched_at, digest = now.isoformat(), hashlib.sha256(text.encode()).hexdigest()
            with self.connect() as connection:
                connection.execute("INSERT OR REPLACE INTO reference_files (name, fetched_at, digest, payload) VALUES (?, ?, ?, ?)",
                                   ("french_daily", fetched_at, digest, text))
        rows = parse_french(text)
        series = MARKET_SERIES[kind]
        bars, rates = market_series(rows, series["leverage"], start, end)
        payload = json.dumps([[asdict(bar) for bar in bars], rates], separators=(",", ":"), allow_nan=False)
        source = {"kind": kind, "label": series["label"], "symbol": series["symbol"], "leverage": series["leverage"],
                  "provider": "Kenneth R. French Data Library, Fama/French 3 Factors [Daily]: CRSP value-weighted US market "
                              "return (dividends included) and 1-month T-bill return",
                  "url": FRENCH_URL, "cached": cached, "fetched_at": fetched_at, "file_sha256": digest,
                  "sha256": hashlib.sha256(payload.encode()).hexdigest(), "data_last_date": rows[-1][0],
                  "observation": "daily close-to-close total-return level, rebased to 100",
                  "construction": ("market return" if series["leverage"] == 1 else
                                   f"{series['leverage']} x market return above T-bills + T-bill rate "
                                   f"- {LEVERAGED_EXPENSE:.2%}/yr, reset daily"),
                  "cash_interest": "idle cash earns the daily 1-month T-bill rate",
                  "requested_end": end, "end_exclusive": True}
        return bars, rates, source
