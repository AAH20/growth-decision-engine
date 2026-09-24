"""Local, machine-dependent full-pipeline benchmark. No customer data used."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from growth_decision_engine.core import MAX_BOOTSTRAP_PICKS, MAX_UNITS, score


def run(units: int, resamples: int) -> dict:
    if units < 4 or units > MAX_UNITS or units % 2:
        raise ValueError(f"units must be even and between 4 and {MAX_UNITS}")
    if not 100 <= resamples <= 10000 or units * resamples > MAX_BOOTSTRAP_PICKS:
        raise ValueError("resamples must be 100-10000 and total draws <= 20 million")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        definitions = {
            "assignments": ("unit_id", "arm", "assigned_at"),
            "outcomes": ("unit_id", "observed_at", "accepted", "net_revenue_usd", "variable_cost_usd"),
            "costs": ("unit_id", "media_cost_usd", "inference_cost_usd", "infrastructure_cost_usd", "experiment_cost_usd"),
        }
        files = {name: root / f"{name}.csv" for name in definitions}
        handles = {name: path.open("w", newline="", encoding="utf-8") for name, path in files.items()}
        try:
            writers = {name: csv.writer(handle) for name, handle in handles.items()}
            for name, columns in definitions.items():
                writers[name].writerow(columns)
            for index in range(units):
                unit = f"B{index:07d}"
                arm = "control" if index % 2 == 0 else "treatment"
                accepted = index % 7 == 0
                writers["assignments"].writerow((unit, arm, "2026-01-01T00:00:00Z"))
                writers["outcomes"].writerow((unit, "2026-01-08T00:00:00Z", int(accepted),
                                               "90.00" if accepted else "0.00", "2.00"))
                writers["costs"].writerow((unit, "10.00", "0.50", "0.50", "1.00"))
        finally:
            for handle in handles.values():
                handle.close()
        tracemalloc.start()
        start = time.perf_counter()
        report = score(files["assignments"], files["outcomes"], files["costs"],
                       resamples=resamples)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return {"benchmark": "synthetic_full_score_pipeline", "units": units,
            "resamples": resamples, "bootstrap_draws": units * resamples,
            "elapsed_seconds": round(elapsed, 4), "peak_traced_python_bytes": peak,
            "python": platform.python_version(), "platform": platform.platform(),
            "scorecard_protocol": report["protocol"],
            "limits": "Local timing and traced Python allocations are machine dependent; peak is not process RSS."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--units", type=int, default=1000)
    parser.add_argument("--resamples", type=int, default=200)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.units, args.resamples), indent=2, sort_keys=True))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
