"""Replay bounded pilot histories and emit deterministic, review-only BI alerts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .bi import diff_reports
from .core import DataError
from .pilot import verify_pilot
from .plan import PlanError, _unique_keys, timestamp

SOURCES = ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest")
SNAPSHOT_FIELDS = set(SOURCES) | {"packet", "seed", "resamples"}


def monitor_series(series_path: str | Path, *, as_of: str, freshness_hours: int = 24) -> dict:
    """Verify each packet against local sources before computing history diagnostics."""
    if isinstance(freshness_hours, bool) or not isinstance(freshness_hours, int) or not 1 <= freshness_hours <= 720:
        raise DataError("freshness_hours must be an integer from 1 to 720")
    try:
        now = timestamp(as_of, "as_of")
    except PlanError as exc:
        raise DataError(str(exc)) from exc
    series_file = Path(series_path)
    if series_file.stat().st_size > 64 * 1024:
        raise DataError("monitor series exceeds 64 KiB")
    raw = series_file.read_bytes()
    try:
        series = json.loads(raw, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, PlanError) as exc:
        raise DataError("monitor series must be UTF-8 JSON with unique keys") from exc
    if not isinstance(series, dict) or set(series) != {"schema_version", "snapshots"} or series["schema_version"] != "growth-decision-monitor-series/v1":
        raise DataError("unsupported monitor series schema")
    entries = series["snapshots"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
        raise DataError("monitor series requires 1-8 snapshots")
    packets = []
    cutoffs = []
    seen = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != SNAPSHOT_FIELDS:
            raise DataError(f"snapshot {index}: invalid fields")
        if any(not isinstance(entry[name], str) or not entry[name] for name in SOURCES + ("packet",)):
            raise DataError(f"snapshot {index}: paths must be nonempty strings")
        seed, resamples = entry["seed"], entry["resamples"]
        if isinstance(seed, bool) or not isinstance(seed, int) or isinstance(resamples, bool) or not isinstance(resamples, int) or not 100 <= resamples <= 10000:
            raise DataError(f"snapshot {index}: invalid seed or resamples")
        packet_path = (series_file.parent / entry["packet"]).resolve()
        if packet_path in seen:
            raise DataError("monitor series repeats a packet path")
        seen.add(packet_path)
        source_paths = [(series_file.parent / entry[name]).resolve() for name in SOURCES]
        packet = verify_pilot(packet_path, *source_paths, seed=seed, resamples=resamples)
        try:
            cutoff = timestamp(packet["manifest"]["export_cutoff"], "export_cutoff")
        except PlanError as exc:
            raise DataError(str(exc)) from exc
        if cutoffs and cutoff <= cutoffs[-1]:
            raise DataError("snapshot export_cutoff must strictly increase")
        if packets and packet["experiment_id"] != packets[0]["experiment_id"]:
            raise DataError("monitor series mixes experiment IDs")
        cutoffs.append(cutoff)
        packets.append(packet)
    if now < cutoffs[-1]:
        raise DataError("as_of precedes latest export_cutoff")
    age_hours = round((now - cutoffs[-1]).total_seconds() / 3600, 6)
    comparison = (diff_reports(packets[-2]["scorecard"], packets[-1]["scorecard"])
                  if len(packets) > 1 else None)
    alerts = []
    if age_hours > freshness_hours:
        alerts.append("stale_export")
    if comparison:
        if comparison["restated_date_arm_rows"] or comparison["removed_date_arm_rows"]:
            alerts.append("bi_history_restated_or_removed")
        if comparison["plan_changed"]:
            alerts.append("plan_changed")
    gates = packets[-1]["scorecard"]["plan"]["gates"]
    for gate, alert in (("minimum_sample_met", "minimum_sample_not_met"),
                        ("sample_ratio_ok", "sample_ratio_mismatch"),
                        ("incremental_cost_guardrail_met", "incremental_cost_guardrail_breach")):
        if not gates[gate]:
            alerts.append(alert)
    return {
        "protocol": "growth-decision-bi-monitor/v1",
        "claim": "bundled_synthetic_fixture" if all(p["claim"] == "bundled_synthetic_fixture" for p in packets) else "operator_supplied_unverified",
        "experiment_id": packets[0]["experiment_id"],
        "series_sha256": hashlib.sha256(raw).hexdigest(),
        "verified_snapshots": len(packets),
        "export_cutoffs": [p["manifest"]["export_cutoff"] for p in packets],
        "as_of": as_of,
        "latest_export_age_hours": age_hours,
        "freshness_threshold_hours": freshness_hours,
        "latest_diff": comparison,
        "alerts": alerts,
        "human_review_status": "pending",
        "workload": {"bootstrap_unit_draws": sum(sum(p["scorecard"]["counts"].values()) * p["scorecard"]["bootstrap"]["resamples"] for p in packets),
                     "note": "Deterministic work count, not measured runtime, query cost or USD."},
        "limits": ["Packet replay proves consistency with supplied files, not source authenticity or completeness.",
                   "Alerts are deterministic review signals, not measured false-alert rates or automatic actions.",
                   "Only the latest adjacent pair is diffed; retain the full output history for a longer audit."],
    }
