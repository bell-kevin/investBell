"""Generate an idempotent Yahoo-based research report; never send orders."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from . import MODEL_VERSION, __version__
from .data import PriceStore, completed_end
from .engine import Parameters, SYMBOLS
from .service import DEFAULT_GRID, run, validate_request
from .training import ensure_model


def write_report(result, request, report_dir):
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps({"request": request, "data": result["source"]["sha256"],
                                            "version": result.get("version", __version__),
                                            "model_version": result.get("model_version", MODEL_VERSION),
                                            "fitted_model_sha256": result.get("fitted_model", {}).get("model_sha256")},
                                          sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
    name = f"{result['range']['end']}-{request['symbol']}-{fingerprint}.json"
    destination = directory / name
    if destination.exists():
        return destination, False
    report = {"report_kind": "daily research snapshot; no broker or actual paper fills",
              "created_at": datetime.now(timezone.utc).isoformat(), "request": request, **result}
    descriptor, temporary = tempfile.mkstemp(prefix=".report-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
            return destination, True
        except FileExistsError:
            return destination, False
    finally:
        os.unlink(temporary)


def run_daily(request, *, store, model_dir, model_mode="fitted", now=None):
    """Fit/reuse the research model; never change a broker's frozen policy."""
    if model_mode not in ("fitted", "fixed"):
        raise ValueError("model_mode must be fitted or fixed.")
    if request.get("source") != "yahoo":
        raise ValueError("Daily model reports require Yahoo observations.")
    request = dict(request)
    validate_request(request)
    model = None
    report_store = store
    if model_mode == "fitted":
        params = Parameters(**{name: request[name] for name in Parameters.__dataclass_fields__ if name in request})
        bars, source = store.load(request["symbol"], request["start"], request["end"],
                                  refresh=request.get("refresh", False))
        model = ensure_model(bars, source, params, DEFAULT_GRID, model_dir, now=now)
        request.update(model["params"])
        # Replay exactly the acquired snapshot: another fetch could cross a
        # cache expiry or session boundary between fitting and report creation.
        report_store = SimpleNamespace(load=lambda *_args, **_kwargs: (bars, source))
    request["grid"] = {axis: [request.get(field, getattr(Parameters(), field))]
                       for axis, field in (("dca", "dca_pct"), ("va", "va_pct"), ("capture", "capture_pct"))}
    result = run(request, store=report_store)
    result["parameter_mode"] = model_mode
    if model:
        result["fitted_model"] = {
            key: model[key] for key in ("model_sha256", "trained_at", "next_retrain_at",
                                       "model_version", "params", "evaluation")}
        result["fitted_model"]["selection"] = {key: value for key, value in model["selection"].items()
                                                 if key != "candidate_scores"}
        result["fitted_model"]["observed_end"] = model["data"]["observed_end"]
        result["warnings"].append(
            "This daily path replays all requested history with the saved fitted parameters; it is descriptive and includes training data. "
            "The model artifact separately reports a later chronological evaluation excluded from selection. "
            "A saved model is reused until its two-year retraining date; no frozen paper-account policy is changed.")
    return result, request


def parse_bool(text):
    # bool("False") is True, so parse the words explicitly.
    value = text.strip().lower()
    if value not in ("true", "false"):
        raise argparse.ArgumentTypeError(f"expected true or false, not {text!r}")
    return value == "true"


def add_parameter_arguments(parser):
    """Add one --flag per Parameters field; booleans take true or false."""
    for name, default in asdict(Parameters()).items():
        kind = parse_bool if isinstance(default, bool) else float
        parser.add_argument("--" + name.replace("_", "-"), type=kind, default=default)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=SYMBOLS, default="SPY")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=completed_end().isoformat(), help="exclusive end date; defaults to completed-session cutoff")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--report-dir", default="data/reports")
    parser.add_argument("--model-dir", help="defaults to DATA_DIR/models")
    parser.add_argument("--model-mode", choices=("fitted", "fixed"), default="fitted",
                        help="fitted: calibrate DCA/DVA/capture and reuse for two years; fixed: use supplied percentages")
    add_parameter_arguments(parser)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    request = {"symbol": args.symbol, "source": "yahoo", "start": args.start, "end": args.end,
               **{name: getattr(args, name) for name in Parameters.__dataclass_fields__},
               "grid": {"dca": [args.dca_pct], "va": [args.va_pct], "capture": [args.capture_pct]}}
    try:
        result, request = run_daily(
            request, store=PriceStore(Path(args.data_dir) / "market.sqlite3"),
            model_dir=args.model_dir or Path(args.data_dir) / "models", model_mode=args.model_mode)
        path, created = write_report(result, request, args.report_dir)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f"Daily report failed: {error}\n")
    print(json.dumps({"report": str(path), "created": created, "as_of": result["range"]["end"],
                      "parameter_mode": args.model_mode,
                      "fitted_model_sha256": result.get("fitted_model", {}).get("model_sha256"),
                      "next_retrain_at": result.get("fitted_model", {}).get("next_retrain_at"), "orders_sent": 0}))


if __name__ == "__main__":
    main()
