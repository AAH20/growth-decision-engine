# Evaluation protocol

The benchmark must measure **statistical correctness, data quality, runtime cost, and decision utility** separately. A model or agent cannot compensate for invalid assignment or missing outcomes.

## Measurement design

1. Register a fixed-horizon plan outside the CLI before any exposure. Preserve an independent timestamp or signed commit if pre-registration must be demonstrated. The CLI's `registered_at` is only a self-declaration.
2. Randomize on a stable unit, then retain every assigned unit in the analysis, including non-converters. Define the conversion and refund windows before launch.
3. Reconcile billing and cost allocation to source totals. Do not allocate account-wide spend to individual treatment units without a documented allocation rule.
4. Calculate treatment-minus-control accepted-conversion, revenue, cost, and contribution effects. Inspect balance, sample ratio, missingness, interference, and late data.
5. Preserve inconclusive outcomes. The bootstrap interval is descriptive under the design assumptions; a positive point estimate alone is not a win.

## Benchmark suites to implement before production claims

| Suite | Cases | Pass condition |
| --- | --- | --- |
| Parser and contract | Duplicates, missing rows, padded fields, invalid dates, NaN, negative costs, extra columns, huge files | Fail closed with precise error; no partial scorecard |
| Economic arithmetic | Refund-adjusted revenue, per-unit costs, zero converters, high-spend treatment | Cent-level agreement with independently computed examples |
| A/A calibration | Null-effect randomized replications across sample sizes and skewed profit distributions | Observed false-positive rate and interval coverage reported with Monte Carlo uncertainty |
| A/B power | Known-effect simulated data with varying variance and allocation | Detection and interval coverage measured, not inferred from one fixture |
| Assignment integrity | Sample-ratio mismatch, late assignment, unit reuse, spillover scenarios | Diagnostics block or downgrade claims |
| BI restatement | Late refunds, source backfill, cost corrections, plan change | Diff identifies source and date/arm changes |
| Agent grounding | Unsupported metric, fabricated citation, contradictory recommendation | Reject proposal or require human review |
| Runtime | 1k, 10k, 100k units with fixed hardware and resample counts | Time, peak memory, and dollars per verified decision published |

The current repository has unit and CLI tests for the parser, economics, plan window, cost guardrail, snapshot diff, verification, and structural agent-proposal grounding. It does **not** yet have Monte Carlo calibration, 100k-unit runtime evidence, LLM grounding accuracy benchmarks, provider contract tests, or real pilot evidence. The in-memory Python implementation and 64 MiB per-input limit make that scope explicit.

## Agentic analysis contract

An analyst agent should consume a **read-only scorecard**, not raw credentials or platform write access. The shipped [proposal validator](agent-proposals.md) pins a report hash, resolves cited JSON paths, and rejects unapproved next steps. An independent reviewer must still assess whether the proposed interpretation actually follows from the numbers; path existence alone cannot prove that. Model choice is replaceable; the evidence contract is the durable interface.

Evaluate agent proposals on factual support, calibration, operator acceptance, time saved, and incremental contribution after an approved test. Do not use clicks, generated hypothesis count, or pleasing prose as the north-star metric.
