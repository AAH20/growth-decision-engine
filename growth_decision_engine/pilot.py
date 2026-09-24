"""Read-only pilot reconciliation against independently exported ledgers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .core import DataError, MAX_INPUT_BYTES, _date, _read, score
from .plan import PlanError, _unique_keys, load_plan, timestamp

LEDGER_LIMIT = 100_000
MONEY = Decimal("0.01")
CATEGORIES = ("variable", "media", "inference", "infrastructure", "experiment")
BILLING_FIELDS = ("transaction_id", "unit_id", "posted_at", "signed_amount_usd")
SPEND_FIELDS = ("transaction_id", "unit_id", "category", "posted_at", "amount_usd")
MANIFEST_FIELDS = {"schema_version", "experiment_id", "source_owner", "permission_reference",
                   "export_cutoff", "source_labels", "expected_rows", "expected_net_revenue_usd",
                   "expected_total_cost_usd"}
SOURCE_NAMES = {"assignments", "outcomes", "costs", "billing", "spend"}


def _amount(value: str, field: str, *, signed: bool = False) -> int:
    try:
        amount = Decimal(value)
        valid = amount.is_finite() and abs(amount) <= Decimal("1000000000000") and amount == amount.quantize(MONEY)
    except (InvalidOperation, ValueError) as exc:
        raise DataError(f"{field}: invalid USD amount") from exc
    if not valid or (not signed and amount < 0):
        raise DataError(f"{field}: expected {'signed' if signed else 'nonnegative'} USD amount with at most two decimals")
    return int(amount * 100)


def _ledger(path: str | Path, name: str, expected_fields: tuple[str, ...]) -> tuple[list[dict[str, str]], str]:
    file = Path(path)
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise DataError(f"{name}: input exceeds 64 MiB local safety limit")
    raw = file.read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError(f"{name}: expected UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
    if reader.fieldnames is None or set(reader.fieldnames) != set(expected_fields) or len(reader.fieldnames) != len(expected_fields):
        raise DataError(f"{name}: columns must be exactly {', '.join(expected_fields)}")
    rows = []
    seen = set()
    try:
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None or value != value.strip() for value in row.values()):
                raise DataError(f"{name}:{line}: malformed or padded field")
            transaction_id = row["transaction_id"]
            if not transaction_id or not transaction_id.isprintable() or transaction_id in seen:
                raise DataError(f"{name}:{line}: blank or duplicate transaction_id")
            if not row["unit_id"] or not row["unit_id"].isprintable():
                raise DataError(f"{name}:{line}: blank unit_id")
            seen.add(transaction_id)
            rows.append(row)
            if len(rows) > LEDGER_LIMIT:
                raise DataError(f"{name}: exceeds {LEDGER_LIMIT:,} transaction limit")
    except csv.Error as exc:
        raise DataError(f"{name}: malformed CSV") from exc
    if not rows:
        raise DataError(f"{name}: no transactions")
    return rows, hashlib.sha256(raw).hexdigest()


def _manifest(path: str | Path, experiment_id: str) -> tuple[dict, str, datetime]:
    file = Path(path)
    if file.stat().st_size > 64 * 1024:
        raise DataError("pilot manifest exceeds 64 KiB")
    raw = file.read_bytes()
    try:
        data = json.loads(raw, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, PlanError) as exc:
        raise DataError("pilot manifest must be UTF-8 JSON with unique keys") from exc
    if not isinstance(data, dict) or set(data) != MANIFEST_FIELDS or data["schema_version"] != "growth-decision-pilot/v1":
        raise DataError("pilot manifest has incorrect fields or schema_version")
    if data["experiment_id"] != experiment_id:
        raise DataError("pilot manifest experiment_id differs from plan")
    for name in ("experiment_id", "source_owner", "permission_reference"):
        value = data[name]
        if not isinstance(value, str) or not value or len(value) > 200 or value != value.strip() or not value.isprintable():
            raise DataError(f"pilot manifest: invalid {name}")
    labels = data["source_labels"]
    if not isinstance(labels, dict) or set(labels) != SOURCE_NAMES or any(
        not isinstance(value, str) or not value or len(value) > 200 or value != value.strip() or not value.isprintable()
        for value in labels.values()
    ):
        raise DataError("pilot manifest: source_labels must identify all five exports")
    counts = data["expected_rows"]
    if not isinstance(counts, dict) or set(counts) != SOURCE_NAMES or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > LEDGER_LIMIT
        for value in counts.values()
    ):
        raise DataError("pilot manifest: expected_rows must give positive bounded counts for all five exports")
    for name in ("expected_net_revenue_usd", "expected_total_cost_usd"):
        if not isinstance(data[name], str):
            raise DataError(f"pilot manifest: {name} must be a USD string")
        _amount(data[name], f"pilot manifest.{name}")
    try:
        cutoff = timestamp(data["export_cutoff"], "export_cutoff")
    except PlanError as exc:
        raise DataError(str(exc)) from exc
    return data, hashlib.sha256(raw).hexdigest(), cutoff


def pilot_packet(assignments: str | Path, outcomes: str | Path, costs: str | Path,
                 billing: str | Path, spend: str | Path, plan: str | Path,
                 manifest: str | Path, *, seed: int = 1729, resamples: int = 2000) -> dict:
    """Reconcile every expected unit and cost category before emitting a packet."""
    report = score(assignments, outcomes, costs, plan=plan, seed=seed, resamples=resamples)
    try:
        design = load_plan(plan)
    except PlanError as exc:
        raise DataError(str(exc)) from exc
    metadata, manifest_hash, cutoff = _manifest(manifest, design.experiment_id)
    if cutoff < design.observation_end:
        raise DataError("pilot manifest export_cutoff precedes plan observation_end")
    assignments_rows, _ = _read(assignments, "assignments")
    outcomes_rows, _ = _read(outcomes, "outcomes")
    costs_rows, _ = _read(costs, "costs")
    billing_rows, billing_hash = _ledger(billing, "billing", BILLING_FIELDS)
    spend_rows, spend_hash = _ledger(spend, "spend", SPEND_FIELDS)
    observed_rows = {"assignments": len(assignments_rows), "outcomes": len(outcomes_rows),
                     "costs": len(costs_rows), "billing": len(billing_rows), "spend": len(spend_rows)}
    if observed_rows != metadata["expected_rows"]:
        raise DataError("pilot export row counts differ from declared source control counts")
    revenue_cents: dict[str, int] = defaultdict(int)
    cost_cents: dict[tuple[str, str], int] = defaultdict(int)
    for name, rows in (("billing", billing_rows), ("spend", spend_rows)):
        for row in rows:
            unit = row["unit_id"]
            if unit not in assignments_rows:
                raise DataError(f"{name}: transaction for unknown unit_id {unit}")
            posted = _date(row["posted_at"], f"{name}.posted_at")
            if posted > cutoff or posted > design.observation_end:
                raise DataError(f"{name}: transaction posted after export_cutoff or observation_end")
            if name == "billing":
                if posted < _date(assignments_rows[unit]["assigned_at"], f"{unit}.assigned_at"):
                    raise DataError(f"billing: transaction predates assignment for unit_id {unit}")
                revenue_cents[unit] += _amount(row["signed_amount_usd"], "billing.signed_amount_usd", signed=True)
            else:
                category = row["category"]
                if category not in CATEGORIES:
                    raise DataError(f"spend: unsupported category {category}")
                cost_cents[(unit, category)] += _amount(row["amount_usd"], "spend.amount_usd")
    cost_columns = {"variable": "variable_cost_usd", "media": "media_cost_usd",
                    "inference": "inference_cost_usd", "infrastructure": "infrastructure_cost_usd",
                    "experiment": "experiment_cost_usd"}
    for unit in sorted(assignments_rows):
        expected_revenue = _amount(outcomes_rows[unit]["net_revenue_usd"], f"{unit}.net_revenue_usd")
        if revenue_cents[unit] != expected_revenue:
            raise DataError(f"billing: net revenue mismatch for unit_id {unit}")
        for category, column in cost_columns.items():
            source = outcomes_rows[unit] if category == "variable" else costs_rows[unit]
            expected_cost = _amount(source[column], f"{unit}.{column}")
            if cost_cents[(unit, category)] != expected_cost:
                raise DataError(f"spend: {category} cost mismatch for unit_id {unit}")
    net_revenue_total = sum(revenue_cents.values())
    total_cost = sum(cost_cents.values())
    if net_revenue_total != _amount(metadata["expected_net_revenue_usd"], "pilot manifest.expected_net_revenue_usd"):
        raise DataError("pilot net revenue differs from declared source control total")
    if total_cost != _amount(metadata["expected_total_cost_usd"], "pilot manifest.expected_total_cost_usd"):
        raise DataError("pilot total cost differs from declared source control total")
    fixtures = Path(__file__).resolve().parent / "fixtures"
    bundled = (
        report["claim"] == "bundled_synthetic_fixture"
        and design.sha256 == hashlib.sha256((fixtures / "plan.synthetic.json").read_bytes()).hexdigest()
        and all(
            digest == hashlib.sha256((fixtures / f"{name}.synthetic.{extension}").read_bytes()).hexdigest()
            for name, extension, digest in (("billing", "csv", billing_hash), ("spend", "csv", spend_hash),
                                            ("pilot", "json", manifest_hash))
        )
    )
    return {
        "protocol": "growth-decision-pilot-packet/v1",
        "claim": "bundled_synthetic_fixture" if bundled else "operator_supplied_unverified",
        "experiment_id": design.experiment_id,
        "manifest_sha256": manifest_hash,
        "manifest": metadata,
        "permission_status": "operator_reference_unverified",
        "human_review_status": "pending",
        "source_control_reconciliation": {"status": "exact_to_operator_declared_controls",
                                          "observed_rows": observed_rows},
        "ledger_reconciliation": {"status": "exact_per_unit", "billing_transactions": len(billing_rows),
                                  "spend_transactions": len(spend_rows),
                                  "net_revenue_usd": round(net_revenue_total / 100, 2),
                                  "total_cost_usd": round(total_cost / 100, 2),
                                  "billing_sha256": billing_hash, "spend_sha256": spend_hash},
        "scorecard": report,
        "limits": ["Manifest permission and source labels are operator declarations, not independently authenticated.",
                   "Exact ledger reconciliation does not prove transaction completeness, random assignment or causal validity.",
                   "This packet does not approve deployment, spend changes or customer-data publication."],
    }


def verify_pilot(packet: str | Path, *args: str | Path, seed: int = 1729, resamples: int = 2000) -> None:
    try:
        expected = json.loads(Path(packet).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError("pilot packet must be UTF-8 JSON") from exc
    if expected != pilot_packet(*args, seed=seed, resamples=resamples):
        raise DataError("pilot verification failed: packet differs from recomputed sources")
