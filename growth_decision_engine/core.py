"""Strict CSV reconciliation and deterministic intent-to-treat scorecards."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .plan import PlanError, load_plan, sample_ratio_diagnostic
from .stats import contribution_interval

PROTOCOL = "growth-decision/v2"
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_UNITS = 25_000
MAX_BOOTSTRAP_PICKS = 20_000_000
SCHEMAS = {
    "assignments": ("unit_id", "arm", "assigned_at"),
    "outcomes": ("unit_id", "observed_at", "accepted", "net_revenue_usd", "variable_cost_usd"),
    "costs": ("unit_id", "media_cost_usd", "inference_cost_usd", "infrastructure_cost_usd", "experiment_cost_usd"),
}
MONEY = Decimal("0.01")


class DataError(ValueError):
    """Input cannot support the requested scorecard."""


def _read(path: str | Path, name: str) -> tuple[dict[str, dict[str, str]], str]:
    file = Path(path)
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise DataError(f"{name}: input exceeds 64 MiB local safety limit")
    raw = file.read_bytes()
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
            if not unit or not unit.isprintable() or unit != row["unit_id"] or unit in rows:
                raise DataError(f"{name}:{line}: blank, padded or duplicate unit_id")
            if any(value != value.strip() for value in row.values()):
                raise DataError(f"{name}:{line}: padded field")
            rows[unit] = row
            if len(rows) > MAX_UNITS:
                raise DataError(f"{name}: exceeds {MAX_UNITS:,} unit local safety limit")
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
        valid = number.is_finite() and 0 <= number <= Decimal("1000000000000") and number == number.quantize(MONEY)
    except (InvalidOperation, ValueError) as exc:
        raise DataError(f"{field}: invalid amount") from exc
    if not valid:
        raise DataError(f"{field}: expected nonnegative USD amount <= 1 trillion with at most two decimals")
    return number


def _mean(values: list[int]) -> float:
    return sum(values) / len(values)


def _round(value: float) -> float:
    return round(value, 4)


def _cents(amount: Decimal) -> int:
    return int(amount * 100)


def score(assignments: str | Path, outcomes: str | Path, costs: str | Path, *, plan: str | Path | None = None, seed: int = 1729, resamples: int = 2000) -> dict:
    if resamples < 100 or resamples > 10000:
        raise DataError("resamples must be between 100 and 10000")
    a, a_hash = _read(assignments, "assignments")
    o, o_hash = _read(outcomes, "outcomes")
    c, c_hash = _read(costs, "costs")
    if set(a) != set(o) or set(a) != set(c):
        raise DataError("unit IDs must match exactly across assignments, outcomes and costs")
    if len(a) * resamples > MAX_BOOTSTRAP_PICKS:
        raise DataError("requested bootstrap work exceeds 20 million unit draws; reduce resamples or use a scale-out evaluator")

    try:
        design = load_plan(plan) if plan is not None else None
    except PlanError as exc:
        raise DataError(str(exc)) from exc

    arms: dict[str, list[dict[str, int]]] = {"control": [], "treatment": []}
    daily: dict[tuple[str, str], dict[str, Decimal | int]] = {}
    for unit in sorted(a):
        assignment, outcome, cost = a[unit], o[unit], c[unit]
        arm = assignment["arm"]
        if arm not in arms:
            raise DataError(f"{unit}: arm must be control or treatment")
        assignment_time = _date(assignment["assigned_at"], f"{unit}.assigned_at")
        observation_time = _date(outcome["observed_at"], f"{unit}.observed_at")
        if observation_time <= assignment_time:
            raise DataError(f"{unit}: outcome must follow assignment")
        if design and not (design.assignment_start <= assignment_time <= design.assignment_end and observation_time <= design.observation_end):
            raise DataError(f"{unit}: timestamp outside declared plan windows")
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
        arms[arm].append({"accepted": accepted, "contribution": _cents(contribution), "revenue": _cents(revenue),
                          "cost": _cents(variable + media + inference + infra + experiment),
                          "variable_cost": _cents(variable), "media_cost": _cents(media), "inference_cost": _cents(inference),
                          "infrastructure_cost": _cents(infra), "experiment_cost": _cents(experiment)})
        key = (observation_time.date().isoformat(), arm)
        bucket = daily.setdefault(key, {"units": 0, "accepted": 0, "net_revenue_usd": Decimal(0),
                                        "variable_cost_usd": Decimal(0), "media_cost_usd": Decimal(0),
                                        "inference_cost_usd": Decimal(0), "infrastructure_cost_usd": Decimal(0),
                                        "experiment_cost_usd": Decimal(0), "total_cost_usd": Decimal(0),
                                        "contribution_usd": Decimal(0)})
        bucket["units"] += 1
        bucket["accepted"] += accepted
        bucket["net_revenue_usd"] += revenue
        bucket["variable_cost_usd"] += variable
        bucket["media_cost_usd"] += media
        bucket["inference_cost_usd"] += inference
        bucket["infrastructure_cost_usd"] += infra
        bucket["experiment_cost_usd"] += experiment
        bucket["total_cost_usd"] += variable + media + inference + infra + experiment
        bucket["contribution_usd"] += contribution
    if any(len(rows) < 2 for rows in arms.values()):
        raise DataError("at least two units per arm are required")

    def effect(field: str) -> float:
        difference = _mean([row[field] for row in arms["treatment"]]) - _mean([row[field] for row in arms["control"]])
        return difference if field == "accepted" else difference / 100

    lower, upper = contribution_interval(
        [row["contribution"] for row in arms["control"]],
        [row["contribution"] for row in arms["treatment"]],
        seed=seed, resamples=resamples,
    )

    fixtures = Path(__file__).resolve().parent / "fixtures"
    bundled = {
        name: hashlib.sha256((fixtures / f"{name}.synthetic.csv").read_bytes()).hexdigest()
        for name in SCHEMAS
    }
    synthetic = {"assignments": a_hash, "outcomes": o_hash, "costs": c_hash} == bundled
    metrics = ("accepted", "revenue", "variable_cost", "media_cost", "inference_cost", "infrastructure_cost", "experiment_cost", "cost", "contribution")
    descriptive_unit_economics = {}
    for arm, rows in arms.items():
        accepted_count = sum(row["accepted"] for row in rows)
        descriptive_unit_economics[arm] = {
            "net_revenue_per_accepted_usd": _round(sum(row["revenue"] for row in rows) / (100 * accepted_count)) if accepted_count else None,
            "media_cost_per_accepted_usd": _round(sum(row["media_cost"] for row in rows) / (100 * accepted_count)) if accepted_count else None,
            "total_cost_per_accepted_usd": _round(sum(row["cost"] for row in rows) / (100 * accepted_count)) if accepted_count else None,
            "contribution_per_accepted_usd": _round(sum(row["contribution"] for row in rows) / (100 * accepted_count)) if accepted_count else None,
        }
    report = {
        "protocol": PROTOCOL,
        "claim": "bundled_synthetic_fixture" if synthetic else "operator_supplied_unverified",
        "design": "intent_to_treat_if_assignment_was_random_and_complete",
        "sources_sha256": {"assignments": a_hash, "outcomes": o_hash, "costs": c_hash},
        "counts": {arm: len(rows) for arm, rows in arms.items()},
        "arm_means": {arm: {field: _round(_mean([row[field] for row in rows]) / (1 if field == "accepted" else 100)) for field in metrics} for arm, rows in arms.items()},
        "treatment_minus_control_per_unit": {field: _round(effect(field)) for field in metrics},
        "descriptive_unit_economics": descriptive_unit_economics,
        "contribution_effect_95pct_bootstrap_interval": [_round(lower), _round(upper)],
        "bootstrap": {"seed": seed, "resamples": resamples, "method": "within_arm_percentile"},
        "daily_business_intelligence": [
            {"date": date, "arm": arm, **{field: (value if isinstance(value, int) else float(value)) for field, value in daily[(date, arm)].items()}}
            for date, arm in sorted(daily)
        ],
        "limits": ["Source hashes detect later file changes but do not prove source authenticity or pre-outcome assignment.", "Bootstrap intervals do not correct bias, peeking, spillover, or low power.", "No platform campaign changes or budget recommendations are executed."],
    }
    if design:
        srm = sample_ratio_diagnostic(len(arms["control"]), len(arms["treatment"]), design.control_allocation)
        sample_sufficient = all(len(rows) >= design.minimum_units_per_arm for rows in arms.values())
        cost_effect = effect("cost")
        cost_guardrail = cost_effect <= float(design.maximum_incremental_cost_usd_per_unit)
        gates = {"minimum_sample_met": sample_sufficient, "sample_ratio_ok": not srm["investigate"], "incremental_cost_guardrail_met": cost_guardrail}
        if not all(gates.values()):
            decision = "blocked_for_review"
        elif lower > float(design.minimum_contribution_effect_usd):
            decision = "positive_signal_requires_human_review"
        else:
            decision = "inconclusive_requires_human_review"
        report["plan"] = {"experiment_id": design.experiment_id, "unit_type": design.unit_type, "sha256": design.sha256,
                          "self_declared_registration_only": True, "sample_ratio_diagnostic": srm,
                          "gates": gates, "decision_status": decision,
                          "minimum_contribution_effect_usd": float(design.minimum_contribution_effect_usd),
                          "maximum_incremental_cost_usd_per_unit": float(design.maximum_incremental_cost_usd_per_unit)}
        report["limits"].append("Plan registration is self-declared; this CLI cannot attest that a plan was locked before exposure.")
    else:
        report["plan"] = None
        report["limits"].append("No experiment plan supplied; output is exploratory and must not authorize rollout.")
    return report


def canonical_json(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
