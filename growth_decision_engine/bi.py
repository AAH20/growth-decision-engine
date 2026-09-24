"""Compare immutable local scorecard snapshots for BI restatements."""

from __future__ import annotations

import json
from pathlib import Path

from .core import DataError, PROTOCOL


def _load(path: str | Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataError(f"invalid scorecard: {path}") from exc
    if (not isinstance(value, dict) or value.get("protocol") != PROTOCOL
            or not isinstance(value.get("daily_business_intelligence"), list)
            or not isinstance(value.get("sources_sha256"), dict)
            or (value.get("plan") is not None and not isinstance(value.get("plan"), dict))):
        raise DataError(f"unsupported scorecard protocol: {path}")
    return value


def diff_snapshots(before_path: str | Path, after_path: str | Path) -> dict:
    before, after = _load(before_path), _load(after_path)
    old_plan, new_plan = before.get("plan"), after.get("plan")
    if (old_plan or {}).get("experiment_id") != (new_plan or {}).get("experiment_id"):
        raise DataError("cannot diff different experiments")

    def by_key(report: dict) -> dict:
        result = {}
        for row in report["daily_business_intelligence"]:
            if not isinstance(row, dict) or not isinstance(row.get("date"), str) or row.get("arm") not in ("control", "treatment"):
                raise DataError("malformed daily BI row")
            key = (row["date"], row["arm"])
            if key in result:
                raise DataError("duplicate daily BI row")
            result[key] = row
        return result

    old, new = by_key(before), by_key(after)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(key for key in set(old) & set(new) if old[key] != new[key])
    source_changes = sorted(name for name in set(before.get("sources_sha256", {})) | set(after.get("sources_sha256", {}))
                            if before.get("sources_sha256", {}).get(name) != after.get("sources_sha256", {}).get(name))
    return {
        "protocol": "growth-decision-bi-diff/v1",
        "experiment_id": (old_plan or {}).get("experiment_id"),
        "source_files_changed": source_changes,
        "plan_changed": (old_plan or {}).get("sha256") != (new_plan or {}).get("sha256"),
        "added_date_arm_rows": [{"date": date, "arm": arm} for date, arm in added],
        "removed_date_arm_rows": [{"date": date, "arm": arm} for date, arm in removed],
        "restated_date_arm_rows": [{"date": date, "arm": arm, "before": old[(date, arm)], "after": new[(date, arm)]} for date, arm in changed],
    }
