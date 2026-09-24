# Evaluation protocol

The benchmark must measure **statistical correctness, data quality, runtime cost, and decision utility** separately. A model or agent cannot compensate for invalid assignment or missing outcomes.

## Measurement design

1. Register a fixed-horizon plan outside the CLI before any exposure. Preserve an independent timestamp or signed commit if pre-registration must be demonstrated. The CLI's `registered_at` is only a self-declaration.
2. Randomize on a stable unit, then retain every assigned unit in the analysis, including non-converters. Define the conversion and refund windows before launch.
3. Reconcile billing and cost allocation to source totals. Do not allocate account-wide spend to individual treatment units without a documented allocation rule.
4. Calculate treatment-minus-control accepted-conversion, revenue, cost, and contribution effects. Inspect balance, sample ratio, missingness, interference, and late data.
5. Preserve inconclusive outcomes. The bootstrap interval is descriptive under the design assumptions; a positive point estimate alone is not a win.

## Benchmark suites and production evidence gaps

| Suite | Cases | Pass condition |
| --- | --- | --- |
| Parser and contract | Duplicates, missing rows, padded fields, invalid dates, NaN, negative costs, extra columns, huge files | Fail closed with precise error; no partial scorecard |
| Economic arithmetic | Refund-adjusted revenue, per-unit costs, zero converters, high-spend treatment | Cent-level agreement with independently computed examples |
| Pilot ledger reconciliation | Signed billing/refund transactions and five cost categories against scored units | Exact per-unit agreement; reject unknown/duplicate/late transactions and altered packets |
| A/A calibration | One shipped skewed-profit distribution; additional sample sizes and distributions remain | False-positive count and Wilson band reported; characterize sensitivity before production claims |
| A/B power | One shipped known-effect scenario; variance and allocation grid remains | Detection and coverage measured, not inferred from one fixture |
| Assignment integrity | Sample-ratio mismatch, late assignment, unit reuse, spillover scenarios | Diagnostics block or downgrade claims |
| Provider projections | Duplicate/unknown exposure, wrong variant or flag, missing assigned units, sampled edge logs, extra sensitive fields | Reject conflicting rows; retain original assignment census and descriptive-only edge context |
| BI restatement | Late refunds, source backfill, cost corrections, plan change | Diff identifies source and date/arm changes |
| Agent grounding | Unsupported metric, fabricated citation, contradictory recommendation | Reject proposal or require human review |
| Runtime | Shipped 1k and 10k local synthetic scorer; 100k requires a scale-out design | Time and traced allocations measured; process RSS and cost per verified decision remain |

The current repository has unit and CLI tests for the parser, economics, plan window, cost guardrail, snapshot diff, verification, pilot-ledger reconciliation, synthetic provider-projection contracts, replay-verified offline BI monitoring, two-reviewer alert-label binding, structural agent-proposal grounding, a labeled synthetic proposal contract suite, and calibration harness. It does **not** yet have multi-distribution calibration, 100k-unit runtime evidence, LLM grounding accuracy benchmarks, contract tests against permitted native provider exports, or real pilot evidence. The in-memory Python implementation and 64 MiB per-input limit make that scope explicit.

Run `python3 -m growth_decision_engine calibrate --output outputs/calibration-synthetic.json` to reproduce the default synthetic simulation. On 2026-09-25 with seed 1729, 100 replications per scenario, 80 units per arm and 200 resamples, it observed 4% null false positives (Wilson 95% band 1.57%-9.84%), 89% known-effect coverage (81.37%-93.75%), and 8% positive detection (4.11%-15.00%) for an additive $3/unit effect. The latter two results show that this small-sample scenario is poorly powered and its percentile interval undercovers the declared 95% target in this simulation. Treat this as a defect-finding diagnostic, not a validated operating characteristic for customer data. Expand scenarios and repair interval behavior before stronger claims.

Run `python3 -m benchmarks.score_runtime --units 1000 --resamples 200` and repeat with `--units 10000`. On a macOS 26.5.1 arm64 host with Python 3.14.7, single runs measured 0.2396/2.3669 seconds and 1,642,295/15,961,514 peak traced Python bytes. This includes CSV parsing through scorecard creation but excludes fixture generation. Other hosts and data shapes will differ; traced allocations are not process RSS.

## Agentic analysis contract

An analyst agent should consume a **read-only scorecard**, not raw credentials or platform write access. The shipped [proposal validator](agent-proposals.md) pins a report hash, resolves cited JSON paths, and rejects unapproved next steps. An independent reviewer must still assess whether the proposed interpretation actually follows from the numbers; path existence alone cannot prove that. Model choice is replaceable; the evidence contract is the durable interface.

Evaluate agent proposals on factual support, calibration, operator acceptance, time saved, and incremental contribution after an approved test. Do not use clicks, generated hypothesis count, or pleasing prose as the north-star metric.
