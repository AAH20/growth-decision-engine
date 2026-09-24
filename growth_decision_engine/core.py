"""Strict CSV reconciliation and deterministic intent-to-treat scorecards."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROTOCOL = "growth-decision/v1"
SCHEMAS = {
    "assignments": ("unit_id", "arm", "assigned_at"),
    "outcomes": ("unit_id", "observed_at", "accepted", "net_revenue_usd", "variable_cost_usd"),
    "costs": ("unit_id", "media_cost_usd", "inference_cost_usd", "infrastructure_cost_usd", "experiment_cost_usd"),
}
MONEY = Decimal("0.01")


class DataError(ValueError):
    """Input cannot support the requested scorecard."""


def _read(path: str | Path, name: str) -> tuple[dict[str, dict[str, str]], str]:
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError(f"{name}: expected UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    if reader.fieldnames is None or set(reader.fieldnames) != set(SCHEMAS[name]) or len(reader.fieldnames) != len(SCHEMAS[name]):
        raise DataError(f"{name}: columns must be exactly {', '.join(SCHEMAS[name])}")
    rows: dict[str, dict[str, str]] = {}
    try:
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise DataError(f"{name}:{line}: malformed row")
            unit = row["unit_id"].strip()
            if not unit or unit != row["unit_id"] or unit in rows:
                raise DataError(f"{name}:{line}: blank, padded or duplicate unit_id")
            if any(value != value.strip() for value in row.values()):
                raise DataError(f"{name}:{line}: padded field")
            rows[unit] = row
    except csv.Error as exc:
        raise DataError(f"{name}: malformed CSV") from exc
    if not rows:
        raise DataError(f"{name}: no rows")
    return rows, digest


def _date(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError(f"{field}: expected ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise DataError(f"{field}: expected UTC timestamp")
    return parsed


def _money(value: str, field: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise DataError(f"{field}: invalid amount") from exc
    if not number.is_finite() or number < 0 or number != number.quantize(MONEY):
        raise DataError(f"{field}: expected nonnegative USD amount with at most two decimals")
    return number


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _round(value: float) -> float:
    return round(value, 4)


def score(assignments: str | Path, outcomes: str | Path, costs: str | Path, *, seed: int = 1729, resamples: int = 2000) -> dict:
    if resamples < 100 or resamples > 10000:
        raise DataError("resamples must be between 100 and 10000")
    a, a_hash = _read(assignments, "assignments")
    o, o_hash = _read(outcomes, "outcomes")
    c, c_hash = _read(costs, "costs")
    if set(a) != set(o) or set(a) != set(c):
        raise DataError("unit IDs must match exactly across assignments, outcomes and costs")

    arms: dict[str, list[dict[str, float]]] = {"control": [], "treatment": []}
    for unit in sorted(a):
        assignment, outcome, cost = a[unit], o[unit], c[unit]
        arm = assignment["arm"]
        if arm not in arms:
            raise DataError(f"{unit}: arm must be control or treatment")
        if _date(outcome["observed_at"], f"{unit}.observed_at") <= _date(assignment["assigned_at"], f"{unit}.assigned_at"):
            raise DataError(f"{unit}: outcome must follow assignment")
        if outcome["accepted"] not in ("0", "1"):
            raise DataError(f"{unit}: accepted must be 0 or 1")
        accepted = int(outcome["accepted"])
        revenue = _money(outcome["net_revenue_usd"], f"{unit}.net_revenue_usd")
        variable = _money(outcome["variable_cost_usd"], f"{unit}.variable_cost_usd")
        media = _money(cost["media_cost_usd"], f"{unit}.media_cost_usd")
        inference = _money(cost["inference_cost_usd"], f"{unit}.inference_cost_usd")
        infra = _money(cost["infrastructure_cost_usd"], f"{unit}.infrastructure_cost_usd")
        experiment = _money(cost["experiment_cost_usd"], f"{unit}.experiment_cost_usd")
        if not accepted and revenue != 0:
            raise DataError(f"{unit}: unaccepted outcome cannot have revenue")
        contribution = revenue - variable - media - inference - infra - experiment
        arms[arm].append({"accepted": float(accepted), "contribution": float(contribution), "revenue": float(revenue), "cost": float(variable + media + inference + infra + experiment)})
    if any(len(rows) < 2 for rows in arms.values()):
        raise DataError("at least two units per arm are required")

    def effect(field: str) -> float:
        return _mean([row[field] for row in arms["treatment"]]) - _mean([row[field] for row in arms["control"]])

    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        sampled = {}
        for arm, rows in arms.items():
            sampled[arm] = _mean([rng.choice(rows)["contribution"] for _ in rows])
        draws.append(sampled["treatment"] - sampled["control"])
    draws.sort()
    lower = draws[math.floor(0.025 * (resamples - 1))]
    upper = draws[math.ceil(0.975 * (resamples - 1))]

    fixtures = Path(__file__).resolve().parent / "fixtures"
    bundled = {
        name: hashlib.sha256((fixtures / f"{name}.synthetic.csv").read_bytes()).hexdigest()
        for name in SCHEMAS
    }
    synthetic = {"assignments": a_hash, "outcomes": o_hash, "costs": c_hash} == bundled
    return {
        "protocol": PROTOCOL,
        "claim": "bundled_synthetic_fixture" if synthetic else "operator_supplied_unverified",
        "design": "intent_to_treat_if_assignment_was_random_and_complete",
        "sources_sha256": {"assignments": a_hash, "outcomes": o_hash, "costs": c_hash},
        "counts": {arm: len(rows) for arm, rows in arms.items()},
        "arm_means": {arm: {field: _round(_mean([row[field] for row in rows])) for field in ("accepted", "revenue", "cost", "contribution")} for arm, rows in arms.items()},
        "treatment_minus_control_per_unit": {field: _round(effect(field)) for field in ("accepted", "revenue", "cost", "contribution")},
        "contribution_effect_95pct_bootstrap_interval": [_round(lower), _round(upper)],
        "bootstrap": {"seed": seed, "resamples": resamples, "method": "within_arm_percentile"},
        "limits": ["Source hashes detect later file changes but do not prove source authenticity or pre-outcome assignment.", "Bootstrap intervals do not correct bias, peeking, spillover, or low power.", "No platform campaign changes or budget recommendations are executed."],
    }


def canonical_json(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
