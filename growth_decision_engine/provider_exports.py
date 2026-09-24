"""Local audits of projected provider exports; never create assignment rows."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path

from .core import DataError, MAX_INPUT_BYTES, _date, _read
from .plan import PlanError, _unique_keys, load_plan

MAX_EVENTS = 100_000
EXPOSURE_FIELDS = ("event_id", "unit_id", "flag_key", "variant", "observed_at")
EDGE_FIELDS = {"RayID", "EdgeStartTimestamp", "EdgeResponseStatus"}
EDGE_OPTIONAL = {"SampleInterval"}
EDGE_MANIFEST = {"schema_version", "dataset", "sample_rate", "expected_records",
                 "output_format", "timestamp_format"}


def _bytes(path: str | Path, name: str) -> bytes:
    file = Path(path)
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise DataError(f"{name}: exceeds 64 MiB local safety limit")
    return file.read_bytes()


def exposure_audit(assignments: str | Path, exposure_export: str | Path,
                   plan: str | Path, *, provider: str, flag_key: str,
                   expected_events: int) -> dict:
    """Compare provider-projected exposure records to the assignment census."""
    if provider not in ("posthog", "vercel"):
        raise DataError("provider must be posthog or vercel")
    if not flag_key or flag_key != flag_key.strip() or not flag_key.isprintable():
        raise DataError("flag_key must be a nonblank printable value")
    if isinstance(expected_events, bool) or not 1 <= expected_events <= MAX_EVENTS:
        raise DataError("expected_events must be between 1 and 100000")
    assignments_rows, assignment_hash = _read(assignments, "assignments")
    try:
        design = load_plan(plan)
    except PlanError as exc:
        raise DataError(str(exc)) from exc
    for unit, assignment in assignments_rows.items():
        if assignment["arm"] not in ("control", "treatment"):
            raise DataError("assignments: arm must be control or treatment")
        assigned = _date(assignment["assigned_at"], f"{unit}.assigned_at")
        if not design.assignment_start <= assigned <= design.assignment_end:
            raise DataError("assignments: timestamp outside declared plan window")
    raw = _bytes(exposure_export, "exposure export")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    except UnicodeDecodeError as exc:
        raise DataError("exposure export: expected UTF-8 CSV") from exc
    if reader.fieldnames is None or set(reader.fieldnames) != set(EXPOSURE_FIELDS) or len(reader.fieldnames) != len(EXPOSURE_FIELDS):
        raise DataError("exposure export: incorrect columns")
    events = set()
    units: dict[str, str] = {}
    rows = 0
    try:
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None or not value or value != value.strip() or not value.isprintable()
                                  for value in row.values()):
                raise DataError(f"exposure export:{line}: blank, padded or malformed field")
            if row["event_id"] in events:
                raise DataError(f"exposure export:{line}: duplicate event_id")
            events.add(row["event_id"])
            rows += 1
            if rows > MAX_EVENTS:
                raise DataError("exposure export: exceeds 100000 events")
            unit = row["unit_id"]
            if unit not in assignments_rows:
                raise DataError(f"exposure export:{line}: unknown unit_id")
            if row["flag_key"] != flag_key:
                raise DataError(f"exposure export:{line}: unexpected flag_key")
            arm = row["variant"]
            if arm not in ("control", "treatment") or arm != assignments_rows[unit]["arm"]:
                raise DataError(f"exposure export:{line}: variant conflicts with assignment")
            observed = _date(row["observed_at"], "exposure.observed_at")
            assigned = _date(assignments_rows[unit]["assigned_at"], "assignment.assigned_at")
            if observed < assigned or observed > design.observation_end:
                raise DataError(f"exposure export:{line}: outside assigned observation window")
            if unit in units and units[unit] != arm:
                raise DataError(f"exposure export:{line}: conflicting variants for unit")
            units[unit] = arm
    except csv.Error as exc:
        raise DataError("exposure export: malformed CSV") from exc
    if rows != expected_events:
        raise DataError("exposure export: row count differs from declared source control")
    by_arm = Counter(units.values())
    missing = set(assignments_rows) - set(units)
    return {
        "protocol": "growth-decision-exposure-audit/v1",
        "claim": "operator_projected_export_unverified",
        "provider": provider,
        "flag_key": flag_key,
        "experiment_id": design.experiment_id,
        "sources_sha256": {"assignments": assignment_hash,
                           "exposures": hashlib.sha256(raw).hexdigest(), "plan": design.sha256},
        "declared_event_rows": expected_events,
        "observed_event_rows": rows,
        "assigned_units": len(assignments_rows),
        "distinct_exposed_units": len(units),
        "exposure_coverage": round(len(units) / len(assignments_rows), 6),
        "exposed_by_arm": {arm: by_arm[arm] for arm in ("control", "treatment")},
        "assigned_units_without_exposure": len(missing),
        "causal_use": "diagnostic_only_keep_original_assignment_census",
        "limits": ["Rows are a customer-projected contract, not an authenticated native provider export.",
                   "The declared event count and stable unit mapping require source-system validation.",
                   "Flag evaluation or exposure does not replace randomized assignment or prove treatment receipt."],
    }


def cloudflare_audit(logs: str | Path, job_manifest: str | Path) -> dict:
    """Summarize allowlisted HTTP Logpush NDJSON without inferring user lift."""
    manifest_raw = _bytes(job_manifest, "Cloudflare job manifest")
    if len(manifest_raw) > 64 * 1024:
        raise DataError("Cloudflare job manifest exceeds 64 KiB")
    try:
        manifest = json.loads(manifest_raw, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, PlanError) as exc:
        raise DataError("Cloudflare job manifest must be UTF-8 JSON with unique keys") from exc
    if not isinstance(manifest, dict) or set(manifest) != EDGE_MANIFEST or (
        manifest["schema_version"] != "growth-decision-cloudflare-logpush/v1"
        or manifest["dataset"] != "http_requests"
        or manifest["output_format"] != "ndjson"
        or manifest["timestamp_format"] != "rfc3339"
    ):
        raise DataError("Cloudflare job manifest has unsupported fields or format")
    rate = manifest["sample_rate"]
    expected = manifest["expected_records"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 < rate <= 1:
        raise DataError("Cloudflare sample_rate must be > 0 and <= 1")
    if isinstance(expected, bool) or not isinstance(expected, int) or not 1 <= expected <= MAX_EVENTS:
        raise DataError("Cloudflare expected_records must be between 1 and 100000")
    raw = _bytes(logs, "Cloudflare logs")
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise DataError("Cloudflare logs must be UTF-8 NDJSON") from exc
    seen = set()
    classes = Counter()
    upstream_sampled = False
    upstream_unknown = False
    for line_number, line in enumerate(lines, 1):
        if not line:
            raise DataError(f"Cloudflare logs:{line_number}: blank record")
        if len(seen) >= MAX_EVENTS:
            raise DataError("Cloudflare logs exceed 100000 records")
        try:
            row = json.loads(line, object_pairs_hook=_unique_keys)
        except (json.JSONDecodeError, PlanError) as exc:
            raise DataError(f"Cloudflare logs:{line_number}: invalid JSON or duplicate key") from exc
        if not isinstance(row, dict) or not EDGE_FIELDS <= set(row) or set(row) - EDGE_FIELDS - EDGE_OPTIONAL:
            raise DataError(f"Cloudflare logs:{line_number}: only allowlisted Logpush fields are accepted")
        ray = row["RayID"]
        status = row["EdgeResponseStatus"]
        if not isinstance(ray, str) or not ray or not ray.isprintable() or ray in seen:
            raise DataError(f"Cloudflare logs:{line_number}: blank or duplicate RayID")
        seen.add(ray)
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise DataError(f"Cloudflare logs:{line_number}: invalid EdgeResponseStatus")
        if not isinstance(row["EdgeStartTimestamp"], str):
            raise DataError(f"Cloudflare logs:{line_number}: EdgeStartTimestamp must be an RFC3339 string")
        _date(row["EdgeStartTimestamp"], "Cloudflare.EdgeStartTimestamp")
        classes[f"{status // 100}xx"] += 1
        if "SampleInterval" not in row:
            upstream_unknown = True
        else:
            interval = row["SampleInterval"]
            if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
                raise DataError(f"Cloudflare logs:{line_number}: invalid SampleInterval")
            upstream_sampled |= interval > 1
    if len(seen) != expected:
        raise DataError("Cloudflare logs count differs from declared source control")
    sampling = ("known_sampled" if rate < 1 or upstream_sampled else
                "upstream_sampling_unknown" if upstream_unknown else "operator_declared_unsampled_unverified")
    return {
        "protocol": "growth-decision-cloudflare-audit/v1",
        "claim": "operator_exported_logpush_unverified",
        "dataset": "http_requests",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "observed_requests": len(seen),
        "observed_status_classes": {key: classes[key] for key in ("1xx", "2xx", "3xx", "4xx", "5xx")},
        "observed_5xx_fraction": round(classes["5xx"] / len(seen), 6),
        "sampling": {"job_sample_rate_declared": rate, "status": sampling},
        "causal_use": "descriptive_only_no_unit_assignment_join",
        "limits": ["The job manifest and record count are operator declarations, not authenticated provider attestations.",
                   "Do not extrapolate sampled counts or feed request logs into the unit-level causal estimator.",
                   "No IPs, user IDs or request URIs are accepted by this allowlisted parser."],
    }
