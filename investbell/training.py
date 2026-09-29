"""Fit only DCA, VA/DVA and capture on Yahoo history; retain biennial artifacts.

This is an explicit project calibration method, not a reconstruction of an
undisclosed model or a price predictor. Later historical evaluation never picks
parameters. Saved fits are research artifacts and never change broker policies.
"""

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import tempfile

from . import MODEL_VERSION
from .calendar import expected_sessions, latest_completed_session
from .engine import Bar, Parameters, SYMBOLS, number, simulate, validate_bars


ARTIFACT_VERSION = "three-parameter-fit-v2"
# v1 picked the single best training cell. Retained v1 artifacts stay verifiable.
LEGACY_ARTIFACT_VERSION = "three-parameter-fit-v1"
AXES = {"dca": ("dca_pct", .01, 100), "va": ("va_pct", 0, 5),
        "capture": ("capture_pct", .1, 1000)}
NEIGHBORHOOD = ("the 27 grid positions within one step of the candidate on each axis, including itself; "
                "positions past the edge of the grid use the nearest edge cell")
OBJECTIVE = ["maximum neighborhood mean training final_equity, where the neighborhood is " + NEIGHBORHOOD,
             "maximum own training final_equity", "minimum training drawdown magnitude",
             "minimum training trade_count", "ascending DCA, VA, capture triple"]
MIN_TRAIN = 252
MIN_EVALUATION = 126


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def code_identity():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("engine.py", "training.py")}


def _instant(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Missing timezone")
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Training timestamps must be timezone-aware ISO datetimes.") from None


def next_retraining(trained_at):
    """Two calendar years, using February 28 when a leap day does not exist."""
    trained_at = _instant(trained_at)
    try:
        return trained_at.replace(year=trained_at.year + 2)
    except ValueError:
        return trained_at.replace(year=trained_at.year + 2, day=28)


def _axes(axes):
    if not isinstance(axes, dict) or set(axes) != set(AXES):
        raise ValueError("Training grid must contain only dca, va, and capture arrays.")
    result = {}
    for name, (_, low, high) in AXES.items():
        values = axes[name]
        if not isinstance(values, list) or not 1 <= len(values) <= 7:
            raise ValueError("Each training axis needs 1 to 7 values; at most 343 candidates.")
        result[name] = sorted(set(number(value, name, low, high) for value in values))
    return result


def _observations(bars, source, now):
    bars = validate_bars(bars)
    if not isinstance(source, dict) or source.get("kind") != "yahoo":
        raise ValueError("Model fitting requires Yahoo observations; synthetic data is not accepted.")
    if source.get("symbol") not in SYMBOLS:
        raise ValueError("Model fitting requires a supported Yahoo index ETF symbol.")
    fetched_at = _instant(source.get("fetched_at"))
    if fetched_at > now:
        raise ValueError("Yahoo snapshot fetch timestamp cannot be in the future.")
    if bars[-1].date > latest_completed_session(fetched_at).isoformat():
        raise ValueError("Yahoo fitting observations were not completed when fetched.")
    end = (date.fromisoformat(bars[-1].date) + timedelta(days=1)).isoformat()
    sessions = expected_sessions(bars[0].date, end)
    if tuple(bar.date for bar in bars) != sessions:
        raise ValueError("Yahoo fitting observations must cover every exchange session without gaps.")
    for key, expected in (("expected_first_session", bars[0].date),
                          ("expected_last_session", bars[-1].date),
                          ("expected_session_count", len(bars))):
        if key in source and source[key] != expected:
            raise ValueError("Yahoo snapshot session metadata does not match fitting observations.")
    payload = json.dumps([asdict(bar) for bar in bars], separators=(",", ":"), allow_nan=False)
    if source.get("sha256") != hashlib.sha256(payload.encode()).hexdigest():
        raise ValueError("Yahoo snapshot checksum does not match fitting observations.")
    if len(bars) < MIN_TRAIN + MIN_EVALUATION:
        raise ValueError("Fitting needs at least 378 completed sessions: 252 training and 126 later evaluation.")
    return bars


def _partition(bars):
    evaluation_count = max(MIN_EVALUATION, len(bars) // 5)
    return bars[:-evaluation_count], bars[-evaluation_count:]


def _range(bars):
    return {"start": bars[0].date, "end_inclusive": bars[-1].date,
            "observations": len(bars), "bars_sha256": digest([asdict(bar) for bar in bars])}


def _rank(candidate):
    metrics = candidate["metrics"]
    return (-metrics["final_equity"], abs(metrics["max_drawdown_pct"]),
            metrics["trade_count"], *(candidate[name] for name, _, _ in AXES.values()))


def _selected(candidate):
    return {name: candidate[name] for name, _, _ in AXES.values()}


def _neighborhood_means(candidates, axes):
    """Mean training final equity over each candidate's 3 x 3 x 3 grid neighborhood.

    Every neighborhood has 27 equally weighted entries. Dropping positions past
    the edge instead would let an edge cell beside a peak outscore the peak.
    """
    sizes = [len(axes[key]) for key in AXES]
    positions = [{value: index for index, value in enumerate(axes[key])} for key in AXES]
    cells = [tuple(position[candidate[name]] for position, (name, _, _) in zip(positions, AXES.values()))
             for candidate in candidates]
    equity = dict(zip(cells, (candidate["metrics"]["final_equity"] for candidate in candidates)))
    return [sum(equity[tuple(min(max(index + step, 0), size - 1) for index, step, size in zip(cell, steps, sizes))]
                for steps in product((-1, 0, 1), repeat=3)) / 27
            for cell in cells]


def _selection(candidates, axes, version=ARTIFACT_VERSION):
    """Training-only selection fields, recomputable from stored candidate scores.

    A lone bright cell among poor neighbors is usually noise; the center of a
    bright region is less sensitive to small changes in the settings.
    """
    peak = min(candidates, key=_rank)
    if version == LEGACY_ARTIFACT_VERSION:
        return {"selected_axes": _selected(peak)}
    means = _neighborhood_means(candidates, axes)
    best = min(range(len(candidates)), key=lambda index: (-means[index], *_rank(candidates[index])))
    selected = _selected(candidates[best])
    # A center on the grid's edge may belong to a region that extends past the tested values.
    edges = [key for key, (name, _, _) in AXES.items()
             if len(axes[key]) > 1 and selected[name] in (axes[key][0], axes[key][-1])]
    return {"selected_axes": selected,
            "neighborhood": {"definition": NEIGHBORHOOD, "mean_training_final_equity": means[best],
                             "at_grid_edge": edges},
            "single_best_training_cell": {**_selected(peak), "training_final_equity": peak["metrics"]["final_equity"]}}


def _fit(bars, source, base_params, configuration, now):
    training, evaluation = _partition(bars)
    candidates = []
    for dca, va, capture in product(*(configuration["axes"][key] for key in AXES)):
        parameters = replace(base_params, dca_pct=dca, va_pct=va, capture_pct=capture)
        candidates.append({"dca_pct": dca, "va_pct": va, "capture_pct": capture,
                           "metrics": simulate(training, parameters, detailed=False)["metrics"]})
    selection = _selection(candidates, configuration["axes"])
    selected_axes = selection["selected_axes"]
    parameters = replace(base_params, **selected_axes)
    # This evaluation is deliberately performed only after the winner is fixed.
    metrics = simulate(evaluation, parameters, detailed=False)["metrics"]
    # Reported for comparison only: how the single best training cell did later.
    peak_axes = _selected(selection["single_best_training_cell"])
    peak_metrics = metrics if peak_axes == selected_axes else simulate(
        evaluation, replace(base_params, **peak_axes), detailed=False)["metrics"]
    result = {
        "artifact_version": ARTIFACT_VERSION, "model_version": MODEL_VERSION,
        "kind": "research_parameter_fit", "trained_at": now.isoformat(),
        "next_retrain_at": next_retraining(now).isoformat(),
        "configuration": configuration, "configuration_sha256": digest(configuration),
        "params": asdict(parameters),
        "data": {"observed_start": bars[0].date, "observed_end": bars[-1].date,
                 "observations": len(bars), "bars_sha256": digest([asdict(bar) for bar in bars]),
                 "source_sha256": digest(source), "source": source,
                 "bars": [asdict(bar) for bar in bars]},
        "selection": {"objective": OBJECTIVE, "training": _range(training),
                      "candidate_scores": candidates, **selection},
        "evaluation": {"classification": "later chronological retrospective evaluation; previously explored history",
                       "used_for_selection": False, **_range(evaluation), "metrics": metrics,
                       "single_best_training_cell": {"axes": peak_axes, "metrics": peak_metrics,
                                                     "used_for_selection": False}},
        "limitations": [
            "Calibration of three strategy parameters only; no price predictions or additional fundamentals.",
            "The objective, grid, split and formulas are project choices, not the speaker's undisclosed method.",
            "Evaluation rows are excluded from this fit's parameter selection; prior human exploration remains possible.",
            "Training and evaluation each start with identical initial cash and no carried positions.",
            "Historical selection does not establish future returns or authorize broker policy changes.",
            "Data, capital, costs and operational limits are not additional fitted strategy variables.",
        ],
    }
    result["model_sha256"] = digest(result)
    return result


def _verify(artifact, *, configuration=None):
    """Validate stored integrity and selection provenance without rerunning fits."""
    try:
        version = artifact["artifact_version"]
        if version not in (ARTIFACT_VERSION, LEGACY_ARTIFACT_VERSION):
            raise ValueError("Unsupported fitting artifact version.")
        if digest({key: value for key, value in artifact.items() if key != "model_sha256"}) != artifact["model_sha256"]:
            raise ValueError("Model artifact checksum mismatch.")
        config = artifact["configuration"]
        if (digest(config) != artifact["configuration_sha256"] or config["artifact_version"] != version
                or (configuration is not None and config != configuration)):
            raise ValueError("Model artifact configuration mismatch.")
        if artifact["next_retrain_at"] != next_retraining(artifact["trained_at"]).isoformat():
            raise ValueError("Model artifact retraining date mismatch.")
        data = artifact["data"]
        bars = _observations([Bar(**bar) for bar in data["bars"]], data["source"], _instant(artifact["trained_at"]))
        if (data["bars_sha256"] != digest(data["bars"]) or data["source_sha256"] != digest(data["source"])
                or data["observed_start"] != bars[0].date or data["observed_end"] != bars[-1].date
                or data["observations"] != len(bars)):
            raise ValueError("Model artifact data provenance mismatch.")
        training, evaluation = _partition(bars)
        if artifact["selection"]["training"] != _range(training):
            raise ValueError("Model artifact training partition mismatch.")
        if any(artifact["evaluation"].get(key) != value for key, value in _range(evaluation).items()):
            raise ValueError("Model artifact evaluation partition mismatch.")
        if artifact["evaluation"]["used_for_selection"] is not False:
            raise ValueError("Model artifact used evaluation for selection.")
        parameters = Parameters(**artifact["params"])
        candidates = artifact["selection"]["candidate_scores"]
        expected = list(product(*(config["axes"][name] for name in AXES)))
        actual = [tuple(row[name] for name, _, _ in AXES.values()) for row in candidates]
        if expected != actual:
            raise ValueError("Model artifact candidate grid mismatch.")
        expected_selection = _selection(candidates, config["axes"], version)
        selected = expected_selection["selected_axes"]
        if (any(artifact["selection"][key] != value for key, value in expected_selection.items())
                or any(getattr(parameters, key) != value for key, value in selected.items())):
            raise ValueError("Model artifact selected parameters mismatch.")
        if (version == ARTIFACT_VERSION and artifact["evaluation"]["single_best_training_cell"]["axes"]
                != _selected(expected_selection["single_best_training_cell"])):
            raise ValueError("Model artifact single-best comparison mismatch.")
        if any(getattr(parameters, key) != value for key, value in config["fixed_params"].items()):
            raise ValueError("Model artifact changed fixed capital or execution costs.")
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Malformed fitting artifact.") from error
    return artifact


def _read(path, *, configuration=None):
    try:
        with path.open() as stream:
            result = json.load(stream)
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read model artifact {path.name}; inspect the retained file.") from error
    return _verify(result, configuration=configuration)


def _write(directory, artifact):
    stamp = _instant(artifact["trained_at"]).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"model-{artifact['configuration_sha256']}-{stamp}-{artifact['model_sha256'][:16]}.json"
    descriptor, temporary = tempfile.mkstemp(prefix=".model-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(artifact, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _read(path) != artifact:
                raise ValueError("Conflicting immutable model artifact already exists.")
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        os.unlink(temporary)


def ensure_model(bars, source, base_params, axes, model_dir, *, now=None):
    """Reuse the matching research fit until due, otherwise save a new fit.

    Starting cash and costs stay fixed. The available history is split into the
    oldest approximately 80% (at least 252 sessions) and the later approximately
    20% (at least 126). A changed grid, costs, start, symbol or code opens a new
    artifact series. Older requested ranges never consume a later model.
    """
    now = _instant(now if now is not None else datetime.now(timezone.utc))
    bars = _observations(bars, source, now)
    if not isinstance(base_params, Parameters):
        raise ValueError("base_params must be validated Parameters.")
    configuration = {"symbol": source["symbol"], "observed_start": bars[0].date,
                     "axes": _axes(axes), "code_sha256": code_identity(),
                     "model_version": MODEL_VERSION, "artifact_version": ARTIFACT_VERSION,
                     "fixed_params": {key: getattr(base_params, key)
                                      for key in ("initial_cash", "slippage_bps", "fee", "no_loss_sales")},
                     "split": {"minimum_training": MIN_TRAIN, "minimum_evaluation": MIN_EVALUATION,
                               "evaluation_fraction": .2}, "objective": OBJECTIVE}
    series = digest(configuration)
    directory = Path(model_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".training.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        artifacts = [_read(path, configuration=configuration)
                     for path in sorted(directory.glob(f"model-{series}-*.json"))]
        if any(_instant(artifact["trained_at"]) <= now and artifact["data"]["observed_end"] > bars[-1].date
               for artifact in artifacts):
            raise ValueError("This model series already used later history. Choose a separate model directory for the earlier range.")
        eligible = [artifact for artifact in artifacts
                    if _instant(artifact["trained_at"]) <= now and artifact["data"]["observed_end"] <= bars[-1].date]
        if eligible:
            latest = max(eligible, key=lambda item: (_instant(item["trained_at"]), item["model_sha256"]))
            if now < _instant(latest["next_retrain_at"]):
                return latest
        artifact = _fit(bars, source, base_params, configuration, now)
        _write(directory, artifact)
        return artifact


def read_status(model_dir):
    """Read retained fits for the dashboard without fetching or fitting data."""
    artifacts = [_read(path) for path in sorted(Path(model_dir).glob("model-*.json"))]
    if not artifacts:
        return {"initialized": False, "healthy": False, "models": 0, "latest": None}
    latest = max(artifacts, key=lambda item: (_instant(item["trained_at"]), item["model_sha256"]))
    return {"initialized": True, "healthy": True, "models": len(artifacts),
            "latest": {**{key: latest[key] for key in ("model_sha256", "trained_at", "next_retrain_at", "params", "model_version", "evaluation")},
                       "data": {**{key: latest["data"][key] for key in ("observed_start", "observed_end", "observations")},
                                "source": {"symbol": latest["data"]["source"]["symbol"]}}},
            "observed_end": latest["data"]["observed_end"]}
