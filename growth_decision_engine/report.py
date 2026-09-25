"""Self-contained, aggregate-only HTML review of a verified local pilot snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from html import escape
from pathlib import Path

from .core import DataError
from .snapshot import NAMES, verify_snapshot

METRICS = (
    ("Accepted conversion", "accepted", "percent"),
    ("Net revenue / unit", "revenue", "money"),
    ("Variable cost / unit", "variable_cost", "money"),
    ("Media cost / unit", "media_cost", "money"),
    ("Inference cost / unit", "inference_cost", "money"),
    ("Infrastructure cost / unit", "infrastructure_cost", "money"),
    ("Experiment cost / unit", "experiment_cost", "money"),
    ("Total cost / unit", "cost", "money"),
    ("Contribution / unit", "contribution", "money"),
)


def _e(value: object) -> str:
    return escape(str(value), quote=True)


def _number(value: int | float | None, kind: str = "money") -> str:
    if value is None:
        return "—"
    if kind == "percent":
        return f"{value * 100:,.1f}%"
    if kind == "delta_percent":
        return f"{value * 100:+,.1f} pp"
    return f"${value:,.2f}"


def render_snapshot_report(directory: str | Path, *, expected_inventory_sha256: str | None = None) -> str:
    """Verify a bundle before rendering its aggregate scorecard; no source rows escape."""
    root = Path(directory)
    receipt = verify_snapshot(root, expected_inventory_sha256=expected_inventory_sha256)
    packet_raw = (root / "packet.json").read_bytes()
    if hashlib.sha256(packet_raw).hexdigest() != receipt["packet_sha256"]:
        raise DataError("snapshot packet changed before rendering")
    packet = json.loads(packet_raw)
    score = packet["scorecard"]
    plan = score["plan"]
    inventory = json.loads((root / "snapshot.json").read_bytes())
    if hashlib.sha256((root / "snapshot.json").read_bytes()).hexdigest() != receipt["inventory_sha256"]:
        raise DataError("snapshot inventory changed before rendering")

    synthetic = packet["claim"] == "bundled_synthetic_fixture"
    claim = "Bundled synthetic example" if synthetic else "Operator-supplied data · source unverified"
    decision = plan["decision_status"].replace("_", " ").capitalize()
    interval = score["contribution_effect_95pct_bootstrap_interval"]
    metric_rows = "\n".join(
        f"<tr><th scope='row'>{_e(label)}</th><td>{_e(_number(score['arm_means']['control'][key], kind))}</td>"
        f"<td>{_e(_number(score['arm_means']['treatment'][key], kind))}</td>"
        f"<td>{_e(_number(score['treatment_minus_control_per_unit'][key], 'delta_percent' if kind == 'percent' else kind))}</td></tr>"
        for label, key, kind in METRICS
    )
    gate_names = {"minimum_sample_met": "Minimum sample", "sample_ratio_ok": "Assignment ratio",
                  "incremental_cost_guardrail_met": "Cost guardrail"}
    gates = "\n".join(
        f"<li class='gate {'pass' if passed else 'fail'}'><span>{_e(gate_names[key])}</span>"
        f"<strong>{'Pass' if passed else 'Review'}</strong></li>"
        for key, passed in plan["gates"].items()
    )
    source_rows = "\n".join(
        f"<tr><th scope='row'>{_e(name)}</th><td>{inventory['inputs'][name]['bytes']:,}</td>"
        f"<td><code>{_e(inventory['inputs'][name]['sha256'])}</code></td></tr>"
        for name in NAMES
    )
    caveats = "\n".join(f"<li>{_e(limit)}</li>" for limit in packet["limits"] + score["limits"])
    counts = score["counts"]
    accepted = sum(row["accepted"] for row in score["daily_business_intelligence"])
    ledger = packet["ledger_reconciliation"]
    exported_at = packet["manifest"]["export_cutoff"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Growth Decision Engine · Pilot review</title>
<style>
:root {{ color-scheme: light; font: 16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:#f5f7fb; color:#152033; }}
* {{ box-sizing:border-box; }} body {{ margin:0; }} main {{ max-width:1120px; margin:0 auto; padding:40px 24px 96px; }}
.mast {{ background:#10233d; color:white; padding:38px 24px 42px; }} .mast-inner {{ max-width:1120px; margin:auto; }}
.eyebrow {{ text-transform:uppercase; letter-spacing:.16em; font-size:.72rem; font-weight:800; color:#76dbca; margin:0 0 10px; }}
h1 {{ font-size:clamp(2rem,5vw,3.3rem); line-height:1.1; margin:0 0 15px; letter-spacing:-.035em; }}
.lede {{ max-width:780px; color:#d3e0f1; margin:0; }} .badge {{ display:inline-block; padding:7px 11px; border-radius:99px; font-size:.8rem; font-weight:800; background:#e6d4ff; color:#341462; margin-top:22px; }}
.badge.operator {{ background:#ffe7ba; color:#583600; }} .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:0 0 30px; }}
.card,.panel {{ background:white; border:1px solid #d9e1ed; border-radius:16px; box-shadow:0 7px 24px rgba(16,35,61,.04); }}
.card {{ padding:18px; }} .card .label {{ display:block; color:#536177; font-size:.8rem; font-weight:700; margin-bottom:9px; }} .card strong {{ font-size:1.7rem; letter-spacing:-.04em; }}
.card small {{ display:block; color:#5e6d82; margin-top:5px; }} .panel {{ padding:24px; margin:0 0 22px; }}
h2 {{ font-size:1.25rem; margin:0 0 8px; letter-spacing:-.015em; }} h3 {{ margin:22px 0 8px; font-size:1rem; }} p {{ margin:8px 0 14px; }}
.muted {{ color:#536177; }} .decision {{ font-weight:800; color:#593c05; background:#fff5d8; border-left:4px solid #ca8b17; padding:12px 15px; border-radius:6px; }}
.table-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }} th,td {{ padding:11px 12px; border-bottom:1px solid #e3e9f2; text-align:right; }}
th:first-child,td:first-child {{ text-align:left; }} thead th {{ color:#41516a; font-size:.78rem; text-transform:uppercase; letter-spacing:.04em; }} tbody th {{ font-weight:600; }}
.gates {{ display:flex; gap:10px; flex-wrap:wrap; list-style:none; padding:0; }} .gate {{ border:1px solid #d9e1ed; border-radius:10px; padding:10px 13px; min-width:175px; display:flex; justify-content:space-between; gap:16px; }}
.gate.pass strong {{ color:#126650; }} .gate.fail strong {{ color:#9a3c2c; }} code {{ font-size:.78rem; overflow-wrap:anywhere; }}
.sources td:last-child {{ text-align:left; max-width:550px; }} .sources td:nth-child(2) {{ text-align:right; }} .limits {{ color:#44536b; padding-left:20px; }} .limits li {{ margin:5px 0; }}
.footer {{ color:#657289; font-size:.85rem; margin-top:32px; }}
@media(max-width:760px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} main {{ padding:24px 14px 60px; }} .panel {{ padding:18px; }} }}
@media(max-width:440px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style></head><body>
<header class="mast"><div class="mast-inner"><p class="eyebrow">Growth Decision Engine / Evidence review</p><h1>Pilot economics, with the evidence attached.</h1>
<p class="lede">A read-only view of one replay-verified local snapshot. This report compares randomized arms under a declared plan and preserves the limits of that claim.</p>
<span class="badge{' operator' if not synthetic else ''}">{_e(claim)}</span></div></header>
<main><section class="grid" aria-label="Key results">
<div class="card"><span class="label">Treatment contribution / unit</span><strong>{_e(_number(score['arm_means']['treatment']['contribution']))}</strong><small>Control: {_e(_number(score['arm_means']['control']['contribution']))}</small></div>
<div class="card"><span class="label">Treatment − control / unit</span><strong>{_e(_number(score['treatment_minus_control_per_unit']['contribution']))}</strong><small>95% bootstrap interval {_e(_number(interval[0]))} to {_e(_number(interval[1]))}</small></div>
<div class="card"><span class="label">Accepted conversions</span><strong>{accepted}</strong><small>{counts['control']} control · {counts['treatment']} treatment assigned units</small></div>
<div class="card"><span class="label">Reconciled spend</span><strong>{_e(_number(ledger['total_cost_usd']))}</strong><small>Net revenue {_e(_number(ledger['net_revenue_usd']))}</small></div>
</section>
<section class="panel"><h2>Decision boundary</h2><p class="decision">{_e(decision)}</p><p class="muted">Every status requires a human reviewer. The plan is self-declared; this report cannot prove preregistration, source completeness, or causal validity.</p>
<ul class="gates">{gates}</ul></section>
<section class="panel"><h2>Unit economics</h2><p class="muted">Arm means and intent-to-treat differences are per assigned unit. Accepted conversion is a share; its difference is in percentage points.</p>
<div class="table-wrap"><table><thead><tr><th scope="col">Metric</th><th scope="col">Control</th><th scope="col">Treatment</th><th scope="col">Difference</th></tr></thead><tbody>{metric_rows}</tbody></table></div>
<h3>Cost per accepted conversion</h3><p>Control {_e(_number(score['descriptive_unit_economics']['control']['total_cost_per_accepted_usd']))} · Treatment {_e(_number(score['descriptive_unit_economics']['treatment']['total_cost_per_accepted_usd']))}. This descriptive ratio includes costs on non-converting units.</p></section>
<section class="panel"><h2>Replay and provenance</h2><p><strong>Experiment:</strong> {_e(packet['experiment_id'])} · <strong>Export cutoff:</strong> {_e(exported_at)} · <strong>Review:</strong> {_e(packet['human_review_status'])}</p>
<p><strong>Packet SHA-256:</strong> <code>{_e(receipt['packet_sha256'])}</code><br><strong>Inventory SHA-256:</strong> <code>{_e(receipt['inventory_sha256'])}</code></p>
<div class="table-wrap"><table class="sources"><thead><tr><th scope="col">Copied input</th><th scope="col">Bytes</th><th scope="col">SHA-256</th></tr></thead><tbody>{source_rows}</tbody></table></div></section>
<section class="panel"><h2>Claim limits</h2><ul class="limits">{caveats}</ul><p class="muted">No platform changes, payments, or customer records are embedded in this file. Keep the report under the same handling rules as the pilot packet.</p></section>
<p class="footer">Generated offline from a verified local snapshot. No scripts, remote fonts, trackers, or network assets.</p></main></body></html>"""


def write_snapshot_report(directory: str | Path, output: str | Path, *,
                          expected_inventory_sha256: str | None = None) -> Path:
    """Write a new private report outside Git and outside the source bundle."""
    target = Path(output)
    parent = target.parent.resolve()
    bundle = Path(directory).resolve()
    if not parent.is_dir():
        raise DataError("report output parent must already exist")
    if parent == bundle or bundle in parent.parents:
        raise DataError("report output must be outside snapshot directory")
    if any((ancestor / ".git").exists() for ancestor in (parent, *parent.parents)):
        raise DataError("report output must be outside a Git repository")
    html = render_snapshot_report(directory, expected_inventory_sha256=expected_inventory_sha256)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise DataError("report output already exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(html.encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target.resolve()
