"""Optional, shadow-only AX, typed decision, and Cognee adapters.

Provider material is untrusted. Nothing in this module changes a scorecard,
approves a proposal, or executes an experiment.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

from .core import DataError, PROTOCOL, canonical_json
from .proposals import _load_limited

JEV_URL = "https://api.typesafe.ai/v1/systemone"
CHOICES = {
    "audit_sources": "Check source completeness, joins, assignment and ledger provenance.",
    "extend_experiment": "Seek a longer predeclared observation window or more units.",
    "design_followup": "Design an independently reviewed follow-up experiment.",
    "human_review": "Escalate ambiguous economics or evidence to a human reviewer.",
}
QUESTION = {"review_next_step": {"type": "choice",
                                  "instructions": "Select only a review activity; never authorize a rollout or spending.",
                                  "criteria": CHOICES}}
MAX_RESPONSE_BYTES = 65536


def _scorecard(path: str | Path) -> tuple[dict, str]:
    report, raw = _load_limited(path, 64 * 1024 * 1024)
    if report.get("protocol") != PROTOCOL:
        raise DataError("integration: unsupported scorecard protocol")
    for key in ("counts", "treatment_minus_control_per_unit", "contribution_effect_95pct_bootstrap_interval", "plan", "claim"):
        if key not in report:
            raise DataError(f"integration: missing scorecard field {key}")
    return report, hashlib.sha256(raw).hexdigest()


def aggregate_state(report: dict) -> dict:
    """Explicit allowlist; unit rows, source labels and free text never egress."""
    effect = report["treatment_minus_control_per_unit"]
    plan = report["plan"]
    counts = report["counts"]
    if not isinstance(effect, dict) or not isinstance(plan, dict) or not isinstance(counts, dict):
        raise DataError("integration: malformed aggregate fields")
    if report["claim"] not in ("bundled_synthetic_fixture", "operator_supplied_unverified"):
        raise DataError("integration: invalid claim")
    if plan.get("decision_status") not in ("blocked_for_review", "positive_signal_requires_human_review",
                                           "inconclusive_requires_human_review"):
        raise DataError("integration: invalid decision status")
    gates = plan.get("gates")
    if not isinstance(gates, dict) or set(gates) != {
            "minimum_sample_met", "sample_ratio_ok", "incremental_cost_guardrail_met"} or any(type(x) is not bool for x in gates.values()):
        raise DataError("integration: invalid plan gates")
    if any(type(counts.get(arm)) is not int or not 2 <= counts[arm] <= 25000 for arm in ("control", "treatment")):
        raise DataError("integration: invalid arm counts")
    interval = report["contribution_effect_95pct_bootstrap_interval"]
    numbers = [effect.get("contribution"), effect.get("cost")]
    if not isinstance(interval, list) or len(interval) != 2:
        raise DataError("integration: invalid contribution interval")
    numbers.extend(interval)
    if any(type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e12 for value in numbers):
        raise DataError("integration: invalid effect or interval")
    state = {"protocol": PROTOCOL, "claim": report["claim"],
             "control_units": counts.get("control"), "treatment_units": counts.get("treatment"),
             "contribution_effect_usd_per_unit": effect.get("contribution"),
             "cost_effect_usd_per_unit": effect.get("cost"),
             "contribution_95pct_interval_usd": report["contribution_effect_95pct_bootstrap_interval"],
             "decision_status": plan["decision_status"], "gates": gates}
    if len(canonical_json(state).encode("utf-8")) > 4096:
        raise DataError("integration: aggregate state exceeds 4 KiB")
    return state


def ax_task_manifest(*, image: str, workspace: str, name: str = "growth-decision-demo") -> dict:
    """Export a Google/AX v1alpha1 Task for the synthetic demo; do not apply it."""
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
        raise DataError("AX task name must be a DNS label")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", workspace):
        raise DataError("AX workspace name must be a DNS label")
    if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", image):
        raise DataError("AX image must be pinned to an OCI sha256 digest")
    return {"apiVersion": "ax.io/v1alpha1", "kind": "Task",
            "metadata": {"name": name, "atespace": "default"},
            "spec": {"image": image,
                     "command": ["python", "-m", "growth_decision_engine", "demo", "--output", "/workspace/outputs/demo-scorecard.json"],
                     "resources": {"requests": {"cpu": "250m", "memory": "512Mi"},
                                   "limits": {"cpu": "1", "memory": "1Gi"}},
                     "workspaces": [{"name": workspace, "path": "/workspace",
                                     "goal": "Provide the pinned Growth Decision Engine source and bundled synthetic fixtures."}]}}


def _post_json(url: str, payload: dict, *, headers: dict[str, str] | None = None) -> dict | list:
    body = canonical_json(payload).encode("utf-8")
    if len(body) > 8192:
        raise DataError("integration request exceeds 8 KiB")
    req = request.Request(url, body, headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                raise DataError(f"integration HTTP status {response.status}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (error.URLError, TimeoutError) as exc:
        raise DataError(f"integration request failed: {type(exc).__name__}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DataError("integration response exceeds 64 KiB")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError("integration response is not JSON") from exc


def _choice_response(response: dict | list) -> tuple[str, dict]:
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise DataError("decision provider omitted answers")
    answer = response["answers"].get("review_next_step")
    if not isinstance(answer, dict) or answer.get("type") != "choice" or not isinstance(answer.get("choice"), str) or answer["choice"] not in CHOICES:
        raise DataError("decision provider returned an out-of-contract choice")
    confidence = answer.get("confidence")
    if confidence is not None and (type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise DataError("decision provider returned invalid confidence")
    return answer["choice"], {"confidence": confidence,
                              "model": str(response.get("routing", {}).get("model", "unreported"))[:80]
                              if isinstance(response.get("routing"), dict) else "unreported"}


def decision_shadow(scorecard: str | Path, provider: str, *, allow_network: bool = False,
                    laya_router=None) -> dict:
    report, digest = _scorecard(scorecard)
    state = aggregate_state(report)
    if provider == "fixture":
        response = {"answers": {"review_next_step": {"type": "choice", "choice": "human_review", "confidence": 1.0}}}
    elif provider == "laya":
        if laya_router is None:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            try:
                from laya import Router
            except ImportError as exc:
                raise DataError("laya is not installed; install it separately and cache model weights") from exc
            laya_router = Router(preload=False)
        response = laya_router.predict(state, QUESTION)
    elif provider == "jev":
        if not allow_network:
            raise DataError("Jev requires explicit --allow-network for aggregate egress")
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise DataError("TYPESAFE_API_KEY is required for Jev")
        response = _post_json(JEV_URL, {"model": "jev-latest", "state": state, "questions": QUESTION},
                              headers={"Authorization": f"Bearer {key}"})
    else:
        raise DataError("unknown decision provider")
    choice, metadata = _choice_response(response)
    return {"schema_version": "growth-decision-shadow/v1", "scorecard_sha256": digest,
            "provider": provider, "suggested_review_step": choice, "provider_metadata": metadata,
            "authority": "advisory_only", "human_review_required": True,
            "scorecard_decision_status": state["decision_status"]}


def _cognee_response(response: dict | list, *, digest: str, query: str, source: str) -> dict:
    if not isinstance(response, list):
        raise DataError("Cognee CHUNKS response must be a list")
    items = []
    for item in response[:10]:
        if not isinstance(item, dict):
            raise DataError("Cognee chunk must be an object")
        content = item.get("text", item.get("content"))
        if not isinstance(content, str) or len(content) > 10000:
            raise DataError("Cognee chunk needs bounded text/content")
        reference = item.get("id", item.get("source_id"))
        if reference is not None and (not isinstance(reference, str) or len(reference) > 200):
            raise DataError("Cognee reference must be a short string")
        items.append({"source_ref_unverified": reference,
                      "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                      "excerpt_untrusted": content[:240]})
    return {"schema_version": "growth-decision-cognee-context/v1", "scorecard_sha256": digest,
            "query": query, "source": source, "retrieval_type": "CHUNKS", "items": items,
            "authority": "untrusted_reviewer_context_only", "scorecard_unchanged": True}


def cognee_context(scorecard: str | Path, query: str, *, response_file: str | Path | None = None,
                   base_url: str | None = None, allow_network: bool = False) -> dict:
    _, digest = _scorecard(scorecard)
    if not isinstance(query, str) or not 1 <= len(query) <= 240:
        raise DataError("Cognee query must be 1-240 characters")
    if (response_file is None) == (base_url is None):
        raise DataError("provide exactly one of response_file or base_url")
    if response_file is not None:
        file = Path(response_file)
        if file.stat().st_size > MAX_RESPONSE_BYTES:
            raise DataError("Cognee response file exceeds 64 KiB")
        response = json.loads(file.read_text(encoding="utf-8"))
        source = "local_response_file"
    else:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1") or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise DataError("Cognee base URL must be a local HTTP origin")
        if not allow_network:
            raise DataError("Cognee HTTP requires explicit --allow-network")
        response = _post_json(base_url.rstrip("/") + "/api/v1/search",
                              {"query": query, "search_type": "CHUNKS", "top_k": 10})
        source = "local_cognee_http"
    return _cognee_response(response, digest=digest, query=query, source=source)
