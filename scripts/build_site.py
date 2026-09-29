"""Build the GitHub Pages copy of investBell: saved results plus the static pages.

GitHub Pages only serves files, so it cannot run the Python backtest server.
`snapshot` runs the real service ahead of time for a few experiments and saves
what /api/run returns for the whole grid and for every cell; commit the result
in site/snapshot/. `assemble` copies static/ and those saved results into a
folder that the Pages workflow publishes unchanged. Only `snapshot` needs the
project's dependencies and market data.

    .venv/bin/python scripts/build_site.py snapshot
    python3 scripts/build_site.py assemble --out _site
"""

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parent.parent
SAVED = ROOT / "site" / "snapshot"
SOURCE_URL = "https://github.com/bell-kevin/investBell"
# The dashboard's grid; must match makeRequest() in static/app.js.
GRID = {"dca": [0.5, 1, 2, 3, 5], "va": [0, 0.05, 0.1, 0.2, 0.5], "capture": [2, 5, 10, 20, 30]}
YAHOO_RANGE = ("2021-01-04", "2026-09-29")
# The latest 20 years of the monthly-updated Ken French file when the snapshot was made.
SERIES_RANGE = ("2006-08-01", "2026-08-01")
EXPERIMENTS = [
    ("yahoo", "SPY", YAHOO_RANGE), ("yahoo", "QQQ", YAHOO_RANGE),
    ("yahoo", "SPXL", YAHOO_RANGE), ("yahoo", "TQQQ", YAHOO_RANGE),
    ("us_market", None, SERIES_RANGE), ("us_market_3x", None, SERIES_RANGE),
    ("demo", None, YAHOO_RANGE),
]
# Size limits keep the saved files near 10 MB: the equity chart is 826 px wide,
# and a cell over 20 years can hold thousands of mostly routine DCA buys.
CHART_POINTS = 300
TRADE_LIMIT = 200
PAGE_MARKER = '<meta name="color-scheme" content="dark light">'
SOURCE_LINK = 'href="/api/source" download="investBell-source.zip">AGPLv3 · Download source ↓</a>'


def cell_name(dca, va, capture):
    # JavaScript's String(number) spells these grid values the same way.
    return "_".join(f"{value:g}" for value in (dca, va, capture)) + ".json"


def chart_rows(count, limit=CHART_POINTS):
    """Evenly spaced row indices, always including the first and last observation."""
    if count <= limit:
        return list(range(count))
    step = (count - 1) / (limit - 1)
    return [round(index * step) for index in range(limit)]


def day_offset(first, day):
    return None if day is None else (date.fromisoformat(day) - first).days


def compact_trades(trades, first):
    """The latest TRADE_LIMIT trades; dates become days after `first`, and side and reason one code."""
    kinds, rows = [], []
    for trade in trades[-TRADE_LIMIT:]:
        kind = [trade["side"], trade["reason"]]
        if kind not in kinds:
            kinds.append(kind)
        rows.append([day_offset(first, trade["date"]), kinds.index(kind), round(trade["shares"], 6), round(trade["price"], 4),
                     round(trade["amount"], 2), round(trade["fee"], 2), round(trade["cash"], 2),
                     day_offset(first, trade["signal_date"])])
    return {"total": len(trades), "kinds": kinds, "rows": rows}


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))


def snapshot(data_dir, out):
    sys.path.insert(0, str(ROOT))
    from investbell import MODEL_VERSION, __version__
    from investbell.data import LEVERAGED_ETFS, PriceStore
    from investbell.engine import Parameters
    from investbell.service import run

    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty. Remove it first so no stale results remain.")
    # Reuse cached downloads however old they are, so every run sees the same observations.
    store = PriceStore(Path(data_dir) / "market.sqlite3", ttl_seconds=10**9, reference_ttl_seconds=10**9)
    defaults = asdict(Parameters())
    experiments = []
    for source, symbol, (start, end) in EXPERIMENTS:
        key = f"{source}-{symbol}" if symbol else source
        request = {"source": source, "symbol": symbol or "SPY", "start": start, "end": end, "grid": GRID, **defaults}
        result = run(request, store=store)
        first, equity = date.fromisoformat(result["range"]["start"]), result["selected"]["equity"]
        points = chart_rows(len(equity))
        # Buy and hold does not depend on the cell, so its chart line is saved once per experiment.
        saved = {**{name: value for name, value in result.items() if name != "selected"},
                 "chart": {"dates": [equity[index]["date"] for index in points],
                           "benchmark": [round(equity[index]["benchmark"], 2) for index in points]}}
        write_json(out / key / "run.json", saved)
        for cell in result["grid"]:
            # The same request the dashboard sends when a cube is inspected.
            settings = {name: cell[name] for name in ("dca_pct", "va_pct", "capture_pct")}
            inspected = run({**request, **settings, "grid": {"dca": [cell["dca_pct"]], "va": [cell["va_pct"]],
                                                             "capture": [cell["capture_pct"]]}}, store=store)
            selected = inspected["selected"]
            if (inspected["source"]["sha256"] != saved["source"]["sha256"]
                    or selected["metrics"]["final_equity"] != cell["final_equity"]
                    or [selected["equity"][index]["benchmark"] for index in points] != [equity[index]["benchmark"] for index in points]):
                raise SystemExit(f"{key}: the inspected cell {settings} does not match its grid result.")
            path = [selected["equity"][index] for index in points]
            payload = {"selected": {**selected, "equity": {"equity": [round(row["equity"], 2) for row in path],
                                                           "cash": [round(row["cash"], 2) for row in path]},
                                    "trades": compact_trades(selected["trades"], first)}}
            if inspected["warnings"] != saved["warnings"]:
                payload["warnings"] = inspected["warnings"]
            write_json(out / key / "cells" / cell_name(cell["dca_pct"], cell["va_pct"], cell["capture_pct"]), payload)
        experiments.append({"id": key, "source": source, "symbol": symbol, "start": start, "end": end,
                            "observations": result["bars_count"]})
        print(f"{key}: {result['bars_count']} observations, {len(result['grid'])} cells", flush=True)
    write_json(out / "manifest.json", {
        "generated": date.today().isoformat(), "version": __version__, "model_version": MODEL_VERSION,
        "grid": GRID, "defaults": {**defaults, "end": YAHOO_RANGE[1]},
        "leveraged_symbols": {symbol: LEVERAGED_ETFS[symbol] for _, symbol, _ in EXPERIMENTS if symbol in LEVERAGED_ETFS},
        "experiments": experiments})


def assemble(out, source_url=SOURCE_URL):
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty. Choose a new folder.")
    if not (SAVED / "manifest.json").is_file():
        raise SystemExit(f"No saved results in {SAVED}. Run the snapshot command first.")
    shutil.copytree(ROOT / "static", out, dirs_exist_ok=True)
    shutil.copytree(SAVED, out / "snapshot")
    index = out / "index.html"
    html = index.read_text()
    for old, new in ((PAGE_MARKER, PAGE_MARKER + '<meta name="investbell-snapshot" content="snapshot/">'),
                     (SOURCE_LINK, f'href="{source_url}">AGPLv3 · Source code ↗</a>')):
        if html.count(old) != 1:
            raise SystemExit(f"static/index.html no longer contains {old!r}; update build_site.py.")
        html = html.replace(old, new)
    index.write_text(html)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    saved = commands.add_parser("snapshot", help="compute the saved results (needs the dependencies and market data)")
    saved.add_argument("--data-dir", default="data")
    saved.add_argument("--out", type=Path, default=SAVED)
    pages = commands.add_parser("assemble", help="copy the pages and saved results into a folder for GitHub Pages")
    pages.add_argument("--out", type=Path, required=True)
    pages.add_argument("--source-url", default=SOURCE_URL)
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        snapshot(args.data_dir, args.out)
    else:
        assemble(args.out, args.source_url)


if __name__ == "__main__":
    main()
