"""Offline, non-authorizing preflight for a bounded feature-flag change."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .core import DataError
from .pilot import verify_pilot
from .plan import PlanError, _unique_keys, timestamp

SOURCES = ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest")
EVIDENCE_FIELDS = set(SOURCES) | {"packet", "seed", "resamples"}
CHANGE_FIELDS = {"request_id", "kind", "current_percent", "target_percent", "rollback_percent",
                 "max_additional_spend_usd", "spend_window_hours", "expires_at", "ticket_reference", "owner_id"}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{2,80}\Z")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 160 or value != value.strip() or not value.isprintable():
        raise DataError(f"change request: invalid {field}")
    return value


def _percent(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise DataError(f"change request: {field} must be an integer from 0 to 100")
    return value


def _money(value: object) -> str:
    if not isinstance(value, str):
        raise DataError("change request: max_additional_spend_usd must be a USD string")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or not Decimal("0") < amount <= Decimal("1000000") or amount != amount.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError) as exc:
        raise DataError("change request: invalid positive spend cap") from exc
    return f"{amount:.2f}"


def preflight_change(request_path: str | Path) -> dict:
    file = Path(request_path)
    if file.stat().st_size > 64 * 1024:
        raise DataError("change request exceeds 64 KiB")
    raw = file.read_bytes()
    try:
        request = json.loads(raw, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, PlanError) as exc:
        raise DataError("change request must be UTF-8 JSON with unique keys") from exc
    if not isinstance(request, dict) or set(request) != {"schema_version", "as_of", "evidence", "change"} or request["schema_version"] != "growth-decision-change-request/v1":
        raise DataError("unsupported change request schema")
    try:
        now = timestamp(request["as_of"], "as_of")
    except PlanError as exc:
        raise DataError(str(exc)) from exc
    evidence = request["evidence"]
    if not isinstance(evidence, list) or len(evidence) != 2:
        raise DataError("change request requires exactly two evidence packets")
    packets = []
    packet_hashes = []
    for index, entry in enumerate(evidence):
        if not isinstance(entry, dict) or set(entry) != EVIDENCE_FIELDS:
            raise DataError(f"evidence {index}: invalid fields")
        if any(not isinstance(entry[name], str) or not entry[name] for name in SOURCES + ("packet",)):
            raise DataError(f"evidence {index}: paths must be nonempty strings")
        seed, resamples = entry["seed"], entry["resamples"]
        if type(seed) is not int or type(resamples) is not int or not 100 <= resamples <= 10000:
            raise DataError(f"evidence {index}: invalid seed or resamples")
        packet_path = (file.parent / entry["packet"]).resolve()
        source_paths = [(file.parent / entry[name]).resolve() for name in SOURCES]
        packet_raw = packet_path.read_bytes()
        packet = verify_pilot(packet_path, *source_paths, seed=seed, resamples=resamples)
        if packet_path.read_bytes() != packet_raw:
            raise DataError(f"evidence {index}: packet changed during verification")
        packet_hashes.append(hashlib.sha256(packet_raw).hexdigest())
        try:
            cutoff = timestamp(packet["manifest"]["export_cutoff"], "export_cutoff")
        except PlanError as exc:
            raise DataError(str(exc)) from exc
        if cutoff > now:
            raise DataError(f"evidence {index}: export cutoff exceeds as_of")
        packets.append(packet)
    change = request["change"]
    if not isinstance(change, dict) or set(change) != CHANGE_FIELDS:
        raise DataError("change request: invalid change fields")
    request_id = _text(change["request_id"], "request_id")
    owner_id = _text(change["owner_id"], "owner_id")
    if not IDENTIFIER.fullmatch(request_id) or not IDENTIFIER.fullmatch(owner_id):
        raise DataError("change request: request_id and owner_id require short pseudonymous identifiers")
    _text(change["ticket_reference"], "ticket_reference")
    if change["kind"] != "feature_flag_rollout":
        raise DataError("change request: only feature_flag_rollout is supported")
    current = _percent(change["current_percent"], "current_percent")
    target = _percent(change["target_percent"], "target_percent")
    rollback = _percent(change["rollback_percent"], "rollback_percent")
    if target <= current or target - current > 10 or rollback != current:
        raise DataError("change request: require an increase of at most 10 percentage points and rollback to current_percent")
    spend_cap = _money(change["max_additional_spend_usd"])
    window = change["spend_window_hours"]
    if type(window) is not int or not 1 <= window <= 168:
        raise DataError("change request: spend_window_hours must be 1-168")
    try:
        expiry = timestamp(change["expires_at"], "expires_at")
    except PlanError as exc:
        raise DataError(str(exc)) from exc
    if not now < expiry <= now + timedelta(hours=window):
        raise DataError("change request: expiry must fall within the spend window")

    reasons = []
    plans = [packet["scorecard"]["plan"] for packet in packets]
    if packets[0]["experiment_id"] == packets[1]["experiment_id"]:
        reasons.append("replication_experiment_id_not_distinct")
    if plans[0]["unit_type"] != plans[1]["unit_type"]:
        reasons.append("replication_unit_type_mismatch")
    if any(packets[0]["scorecard"]["sources_sha256"][name] == packets[1]["scorecard"]["sources_sha256"][name]
           for name in ("assignments", "outcomes", "costs")):
        reasons.append("replication_reuses_exact_source_file")
    for index, plan in enumerate(plans):
        if not all(plan["gates"].values()) or plan["decision_status"] != "positive_signal_requires_human_review":
            reasons.append(f"evidence_{index}_not_positive_with_all_gates")
    if any(packet["claim"] == "bundled_synthetic_fixture" for packet in packets):
        reasons.append("bundled_synthetic_evidence")
    return {"protocol": "growth-decision-change-preflight/v1",
            "request_sha256": hashlib.sha256(raw).hexdigest(),
            "request_id": request_id,
            "evidence": [{"experiment_id": packet["experiment_id"], "packet_sha256": digest,
                          "claim": packet["claim"], "decision_status": packet["scorecard"]["plan"]["decision_status"]}
                         for packet, digest in zip(packets, packet_hashes)],
            "change": {"kind": "feature_flag_rollout", "current_percent": current,
                       "target_percent": target, "rollback_percent": rollback,
                       "max_additional_spend_usd": spend_cap, "spend_window_hours": window,
                       "expires_at": change["expires_at"]},
            "status": "eligible_for_external_review" if not reasons else "blocked_for_review",
            "blocking_reasons": reasons,
            "source_authentication_status": "unverified",
            "operator_approval_status": "not_verified",
            "execution_enabled": False,
            "limits": ["This offline preflight never authenticates an operator, approves a change or calls a platform API.",
                       "A declared spend cap is not enforced against platform spend by this package.",
                       "Distinct experiment IDs and file hashes do not prove independent replication, source authenticity or causal validity.",
                       "External approval, scoped credentials, live cap enforcement, monitoring and rollback testing are required before any execution."]}
