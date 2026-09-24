"""Bind two reviewer labels per alert to an immutable offline monitor report."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .core import DataError
from .proposals import _unique_keys

MONITOR_PROTOCOL = "growth-decision-bi-monitor/v1"
REVIEW_PROTOCOL = "growth-decision-alert-reviews/v1"
VERDICTS = {"actionable", "false_alarm", "uncertain"}
REVIEWER_ID = re.compile(r"[A-Za-z0-9_-]{2,40}\Z")


def _object(path: str | Path, limit: int, label: str) -> tuple[dict, bytes]:
    file = Path(path)
    if file.stat().st_size > limit:
        raise DataError(f"{label} exceeds size limit")
    raw = file.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataError(f"{label} must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise DataError(f"{label} must be a JSON object")
    return value, raw


def _monitor(path: str | Path) -> tuple[dict, bytes]:
    report, raw = _object(path, 64 * 1024 * 1024, "monitor report")
    alerts = report.get("alerts")
    if (report.get("protocol") != MONITOR_PROTOCOL or not isinstance(alerts, list)
            or len(alerts) > 20 or any(not isinstance(item, str) or not item for item in alerts)
            or len(set(alerts)) != len(alerts)):
        raise DataError("unsupported or malformed monitor report")
    return report, raw


def review_template(monitor_path: str | Path, reviewer_a: str, reviewer_b: str) -> dict:
    _, raw = _monitor(monitor_path)
    if any(not isinstance(value, str) or not REVIEWER_ID.fullmatch(value) for value in (reviewer_a, reviewer_b)) or reviewer_a == reviewer_b:
        raise DataError("supply two distinct pseudonymous reviewer IDs (2-40 letters, numbers, _ or -)")
    report = json.loads(raw)
    return {"schema_version": REVIEW_PROTOCOL,
            "monitor_sha256": hashlib.sha256(raw).hexdigest(),
            "reviews": [{"alert": alert, "reviewer_id": reviewer, "verdict": "unreviewed"}
                        for alert in report["alerts"] for reviewer in (reviewer_a, reviewer_b)]}


def evaluate_reviews(monitor_path: str | Path, reviews_path: str | Path) -> dict:
    report, monitor_raw = _monitor(monitor_path)
    reviews, review_raw = _object(reviews_path, 64 * 1024, "alert reviews")
    if set(reviews) != {"schema_version", "monitor_sha256", "reviews"} or reviews["schema_version"] != REVIEW_PROTOCOL:
        raise DataError("unsupported alert review schema")
    if reviews["monitor_sha256"] != hashlib.sha256(monitor_raw).hexdigest():
        raise DataError("alert reviews are not bound to this exact monitor report")
    entries = reviews["reviews"]
    if not isinstance(entries, list) or len(entries) != 2 * len(report["alerts"]):
        raise DataError("exactly two review labels are required for each alert")
    by_alert = {alert: {} for alert in report["alerts"]}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"alert", "reviewer_id", "verdict"}:
            raise DataError("invalid review entry")
        alert, reviewer, verdict = entry["alert"], entry["reviewer_id"], entry["verdict"]
        if (not isinstance(alert, str) or alert not in by_alert or not isinstance(reviewer, str)
                or not REVIEWER_ID.fullmatch(reviewer) or not isinstance(verdict, str) or verdict not in VERDICTS):
            raise DataError("review entry has unknown alert, reviewer or verdict")
        if reviewer in by_alert[alert]:
            raise DataError("same reviewer cannot label an alert twice")
        by_alert[alert][reviewer] = verdict
    if any(len(labels) != 2 for labels in by_alert.values()):
        raise DataError("each alert requires two distinct reviewers")
    outcomes = []
    for alert in report["alerts"]:
        labels = list(by_alert[alert].values())
        consensus = labels[0] if labels[0] == labels[1] and labels[0] != "uncertain" else "unresolved"
        outcomes.append({"alert": alert, "consensus": consensus})
    actionable = sum(row["consensus"] == "actionable" for row in outcomes)
    false_alarms = sum(row["consensus"] == "false_alarm" for row in outcomes)
    resolved = actionable + false_alarms
    return {"protocol": "growth-decision-alert-review-result/v1",
            "claim": "reviewer_labels_not_independent_ground_truth",
            "monitor_sha256": hashlib.sha256(monitor_raw).hexdigest(),
            "reviews_sha256": hashlib.sha256(review_raw).hexdigest(),
            "alert_count": len(outcomes),
            "consensus_actionable": actionable,
            "consensus_false_alarm": false_alarms,
            "unresolved": len(outcomes) - resolved,
            "reviewer_confirmed_precision": round(actionable / resolved, 6) if resolved else None,
            "outcomes": outcomes,
            "limits": ["Two reviewer labels do not establish independent ground truth or real-world impact.",
                       "Uncertain or conflicting labels are unresolved and excluded from precision denominator.",
                       "This evaluator checks report binding and label completeness; source replay must be performed separately."]}
