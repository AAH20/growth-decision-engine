"""Inspectable, fixed-horizon experiment plan and diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


class PlanError(ValueError):
    """The declared experiment design is invalid or contradicted by the data."""


FIELDS = {
    "schema_version", "experiment_id", "unit_type", "registered_at", "assignment_start",
    "assignment_end", "observation_end", "control_allocation", "minimum_units_per_arm",
    "minimum_contribution_effect_usd", "maximum_incremental_cost_usd_per_unit",
}


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlanError(f"plan: duplicate JSON key {key}")
        result[key] = value
    return result


def timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise PlanError(f"{field}: expected UTC ISO-8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanError(f"{field}: expected UTC ISO-8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() != timezone.utc.utcoffset(result):
        raise PlanError(f"{field}: expected UTC timestamp")
    return result


def _amount(value: str, field: str) -> Decimal:
    if not isinstance(value, str):
        raise PlanError(f"{field}: expected amount encoded as a string")
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0 or result > Decimal("1000000000000") or result != result.quantize(Decimal("0.01")):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PlanError(f"{field}: expected nonnegative USD amount with at most two decimals") from exc


@dataclass(frozen=True)
class Plan:
    experiment_id: str
    unit_type: str
    registered_at: datetime
    assignment_start: datetime
    assignment_end: datetime
    observation_end: datetime
    control_allocation: float
    minimum_units_per_arm: int
    minimum_contribution_effect_usd: Decimal
    maximum_incremental_cost_usd_per_unit: Decimal
    sha256: str


def load_plan(path: str | Path) -> Plan:
    file = Path(path)
    if file.stat().st_size > 64 * 1024:
        raise PlanError("plan: exceeds 64 KiB limit")
    raw = file.read_bytes()
    try:
        data = json.loads(raw, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanError("plan: expected UTF-8 JSON object") from exc
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise PlanError(f"plan: fields must be exactly {', '.join(sorted(FIELDS))}")
    if data["schema_version"] != "growth-decision-plan/v1":
        raise PlanError("plan: unsupported schema_version")
    for field in ("experiment_id", "unit_type"):
        if not isinstance(data[field], str) or not data[field] or len(data[field]) > 100 or data[field] != data[field].strip():
            raise PlanError(f"plan: invalid {field}")
    registered = timestamp(data["registered_at"], "registered_at")
    start = timestamp(data["assignment_start"], "assignment_start")
    end = timestamp(data["assignment_end"], "assignment_end")
    observed = timestamp(data["observation_end"], "observation_end")
    if not registered < start <= end < observed:
        raise PlanError("plan: expected registered_at < assignment_start <= assignment_end < observation_end")
    allocation = data["control_allocation"]
    if isinstance(allocation, bool) or not isinstance(allocation, (int, float)) or not math.isfinite(allocation) or not 0 < allocation < 1:
        raise PlanError("plan: control_allocation must be strictly between 0 and 1")
    minimum = data["minimum_units_per_arm"]
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 2:
        raise PlanError("plan: minimum_units_per_arm must be an integer >= 2")
    return Plan(
        data["experiment_id"], data["unit_type"], registered, start, end, observed,
        float(allocation), minimum,
        _amount(data["minimum_contribution_effect_usd"], "minimum_contribution_effect_usd"),
        _amount(data["maximum_incremental_cost_usd_per_unit"], "maximum_incremental_cost_usd_per_unit"),
        hashlib.sha256(raw).hexdigest(),
    )


def sample_ratio_diagnostic(control: int, treatment: int, expected_control: float) -> dict:
    """Normal-approximation SRM diagnostic; a flag to investigate, not a causal test."""
    total = control + treatment
    expected = total * expected_control
    z = (control - expected) / math.sqrt(total * expected_control * (1 - expected_control))
    p = math.erfc(abs(z) / math.sqrt(2))
    return {"expected_control_share": expected_control, "observed_control_share": round(control / total, 6),
            "approximate_two_sided_p_value": round(p, 8), "investigate": p < 0.001}
