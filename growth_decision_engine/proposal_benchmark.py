"""Evaluate the structural proposal contract on a labeled synthetic suite."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from .core import DataError, canonical_json
from .proposals import _load_limited, check_proposal


def benchmark_proposals(report_path: str | Path, suite_path: str | Path) -> dict:
    report, raw = _load_limited(report_path, 64 * 1024 * 1024)
    suite, suite_raw = _load_limited(suite_path, 64 * 1024)
    if report.get("claim") != "bundled_synthetic_fixture":
        raise DataError("proposal benchmark requires bundled synthetic scorecard")
    if set(suite) != {"schema_version", "cases"} or suite["schema_version"] != "growth-decision-proposal-benchmark/v1":
        raise DataError("unsupported proposal benchmark suite")
    cases = suite["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        raise DataError("proposal benchmark requires 1-100 cases")
    seen = set()
    results = []
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary) / "proposal.json"
        for case in cases:
            if not isinstance(case, dict) or set(case) != {"id", "proposal", "expected_structural_accept", "semantic_support_label"}:
                raise DataError("proposal benchmark case has invalid fields")
            case_id = case["id"]
            if not isinstance(case_id, str) or not case_id or len(case_id) > 80 or case_id in seen:
                raise DataError("proposal benchmark case ID is invalid or duplicated")
            seen.add(case_id)
            if type(case["expected_structural_accept"]) is not bool or type(case["semantic_support_label"]) is not bool or not isinstance(case["proposal"], dict):
                raise DataError(f"proposal benchmark case {case_id}: invalid labels or proposal")
            proposal = dict(case["proposal"])
            if proposal.get("report_sha256") == "$REPORT_SHA256":
                proposal["report_sha256"] = hashlib.sha256(raw).hexdigest()
            candidate.write_text(canonical_json(proposal), encoding="utf-8")
            try:
                check_proposal(report_path, candidate)
                accepted = True
            except DataError:
                accepted = False
            results.append({"id": case_id, "expected_structural_accept": case["expected_structural_accept"],
                            "structural_accept": accepted, "semantic_support_label": case["semantic_support_label"]})
    return {"protocol": "growth-decision-proposal-benchmark-result/v1",
            "claim": "synthetic_fixture_labels_not_model_accuracy",
            "report_sha256": hashlib.sha256(raw).hexdigest(),
            "suite_sha256": hashlib.sha256(suite_raw).hexdigest(),
            "cases": results,
            "structural_correct": sum(r["structural_accept"] == r["expected_structural_accept"] for r in results),
            "case_count": len(results),
            "semantically_unsupported_accepted": sum(r["structural_accept"] and not r["semantic_support_label"] for r in results),
            "limits": ["Semantic labels are fixture-authored and are not checked by the validator.",
                       "This benchmark measures a deterministic contract on synthetic examples; it is not LLM factuality, false-alert rate, operator acceptance or field impact."]}
