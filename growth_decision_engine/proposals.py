"""Read-only evidence contract for external analyst agents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .core import DataError, PROTOCOL

PROPOSAL_FIELDS = {"schema_version", "report_sha256", "finding", "evidence_paths", "next_step", "assumptions"}
ALLOWED_TOP_LEVEL = {"counts", "arm_means", "treatment_minus_control_per_unit", "contribution_effect_95pct_bootstrap_interval",
                     "descriptive_unit_economics", "daily_business_intelligence", "plan"}
NEXT_STEPS = {"audit_sources", "extend_experiment", "design_followup", "human_review"}


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_limited(path: str | Path, limit: int) -> tuple[dict, bytes]:
    file = Path(path)
    if file.stat().st_size > limit:
        raise DataError(f"{file.name}: file exceeds size limit")
    raw = file.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError(f"{file.name}: expected JSON object") from exc
    if not isinstance(value, dict):
        raise DataError(f"{file.name}: expected JSON object")
    return value, raw


def _resolve_pointer(document: dict, pointer: str) -> object:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise DataError("evidence_paths: expected JSON Pointers")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    if len(parts) < 2 or parts[0] not in ALLOWED_TOP_LEVEL:
        raise DataError(f"evidence path not allowed: {pointer}")
    current: object = document
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise DataError(f"evidence path does not resolve: {pointer}")
    if current is None:
        raise DataError(f"evidence path resolves to null: {pointer}")
    return current


def check_proposal(report_path: str | Path, proposal_path: str | Path) -> dict:
    report, raw = _load_limited(report_path, 64 * 1024 * 1024)
    proposal, _ = _load_limited(proposal_path, 64 * 1024)
    if report.get("protocol") != PROTOCOL:
        raise DataError("proposal: unsupported scorecard protocol")
    if set(proposal) != PROPOSAL_FIELDS or proposal["schema_version"] != "growth-decision-proposal/v1":
        raise DataError("proposal: invalid or unrecognized fields")
    if proposal["report_sha256"] != hashlib.sha256(raw).hexdigest():
        raise DataError("proposal: report_sha256 does not match scorecard")
    if not isinstance(proposal["finding"], str) or not 1 <= len(proposal["finding"]) <= 500:
        raise DataError("proposal: finding must be 1-500 characters")
    if not isinstance(proposal["next_step"], str) or proposal["next_step"] not in NEXT_STEPS:
        raise DataError("proposal: next_step is not a read-only review action")
    paths = proposal["evidence_paths"]
    if not isinstance(paths, list) or not 1 <= len(paths) <= 10 or any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths):
        raise DataError("proposal: supply 1-10 distinct evidence paths")
    if not isinstance(proposal["assumptions"], list) or len(proposal["assumptions"]) > 10 or any(not isinstance(x, str) or not x or len(x) > 300 for x in proposal["assumptions"]):
        raise DataError("proposal: assumptions must be a list of up to 10 short strings")
    for path in paths:
        _resolve_pointer(report, path)
    return {"status": "structurally_grounded_for_human_review", "next_step": proposal["next_step"],
            "resolved_evidence_paths": paths, "limits": "Citation existence does not prove that prose follows from the evidence."}
