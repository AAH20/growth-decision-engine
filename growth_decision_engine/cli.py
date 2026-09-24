"""Dependency-free command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import DataError, canonical_json, score


def _fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="growth-decision")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run the explicitly synthetic example")
    demo.add_argument("--output", default="outputs/demo-scorecard.json")
    diff = commands.add_parser("bi-diff", help="identify additions, removals and restatements between scorecards")
    diff.add_argument("before")
    diff.add_argument("after")
    monitor = commands.add_parser("bi-monitor", help="replay a local pilot series and emit review-only BI alerts")
    monitor.add_argument("--series", required=True)
    monitor.add_argument("--as-of", required=True)
    monitor.add_argument("--freshness-hours", type=int, default=24)
    monitor.add_argument("--output", required=True)
    review_init = commands.add_parser("alert-review-template", help="create a two-reviewer alert label template")
    review_init.add_argument("--monitor", required=True)
    review_init.add_argument("--reviewer-a", required=True)
    review_init.add_argument("--reviewer-b", required=True)
    review_init.add_argument("--output", required=True)
    review_eval = commands.add_parser("alert-review-eval", help="evaluate two reviewer labels per monitor alert")
    review_eval.add_argument("--monitor", required=True)
    review_eval.add_argument("--reviews", required=True)
    review_eval.add_argument("--output", required=True)
    benchmark = commands.add_parser("proposal-bench", help="measure structural proposal checks on synthetic labeled cases")
    benchmark.add_argument("--scorecard", required=True)
    benchmark.add_argument("--suite", required=True)
    benchmark.add_argument("--output", required=True)
    proposal = commands.add_parser("proposal-check", help="validate an external analyst proposal against a scorecard")
    proposal.add_argument("--scorecard", required=True)
    proposal.add_argument("--proposal", required=True)
    calibration = commands.add_parser("calibrate", help="reproducible synthetic A/A and known-effect stress test")
    calibration.add_argument("--replications", type=int, default=100)
    calibration.add_argument("--units-per-arm", type=int, default=80)
    calibration.add_argument("--resamples", type=int, default=200)
    calibration.add_argument("--effect-cents", type=int, default=300)
    calibration.add_argument("--seed", type=int, default=1729)
    calibration.add_argument("--output", help="optional JSON output path; stdout is always printed")
    exposure = commands.add_parser("exposure-audit", help="audit projected PostHog or Vercel flag events against assignments")
    exposure.add_argument("--provider", choices=("posthog", "vercel"), required=True)
    exposure.add_argument("--assignments", required=True)
    exposure.add_argument("--exposures", required=True)
    exposure.add_argument("--plan", required=True)
    exposure.add_argument("--flag-key", required=True)
    exposure.add_argument("--expected-events", type=int, required=True)
    exposure.add_argument("--output", required=True)
    edge = commands.add_parser("cloudflare-audit", help="descriptive audit of allowlisted HTTP Logpush NDJSON")
    edge.add_argument("--logs", required=True)
    edge.add_argument("--job-manifest", required=True)
    edge.add_argument("--output", required=True)
    for name in ("pilot", "verify-pilot"):
        cmd = commands.add_parser(name, help="reconcile read-only pilot exports" if name == "pilot" else "recompute a pilot packet")
        for field in ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest"):
            cmd.add_argument(f"--{field}", required=True)
        cmd.add_argument("--seed", type=int, default=1729)
        cmd.add_argument("--resamples", type=int, default=2000)
        if name == "pilot":
            cmd.add_argument("--output", required=True)
        else:
            cmd.add_argument("--packet", required=True)
    for name in ("score", "verify"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--assignments", required=True)
        cmd.add_argument("--outcomes", required=True)
        cmd.add_argument("--costs", required=True)
        cmd.add_argument("--plan", help="fixed-horizon experiment plan JSON; strongly recommended for real pilots")
        cmd.add_argument("--seed", type=int, default=1729)
        cmd.add_argument("--resamples", type=int, default=2000)
        if name == "score":
            cmd.add_argument("--output", required=True)
        else:
            cmd.add_argument("--scorecard", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("bi-monitor", "proposal-bench", "alert-review-template", "alert-review-eval"):
            if args.command == "bi-monitor":
                from .monitor import monitor_series
                result = monitor_series(args.series, as_of=args.as_of, freshness_hours=args.freshness_hours)
            elif args.command == "proposal-bench":
                from .proposal_benchmark import benchmark_proposals
                result = benchmark_proposals(args.scorecard, args.suite)
            elif args.command == "alert-review-template":
                from .alert_reviews import review_template
                result = review_template(args.monitor, args.reviewer_a, args.reviewer_b)
            else:
                from .alert_reviews import evaluate_reviews
                result = evaluate_reviews(args.monitor, args.reviews)
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical_json(result), encoding="utf-8")
            print(path)
            return 0
        if args.command in ("exposure-audit", "cloudflare-audit"):
            from .provider_exports import cloudflare_audit, exposure_audit
            result = (exposure_audit(args.assignments, args.exposures, args.plan,
                                     provider=args.provider, flag_key=args.flag_key,
                                     expected_events=args.expected_events)
                      if args.command == "exposure-audit" else
                      cloudflare_audit(args.logs, args.job_manifest))
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical_json(result), encoding="utf-8")
            print(path)
            return 0
        if args.command in ("pilot", "verify-pilot"):
            from .pilot import pilot_packet, verify_pilot
            sources = (args.assignments, args.outcomes, args.costs, args.billing,
                       args.spend, args.plan, args.manifest)
            if args.command == "pilot":
                result = pilot_packet(*sources, seed=args.seed, resamples=args.resamples)
                path = Path(args.output)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(canonical_json(result), encoding="utf-8")
                print(path)
            else:
                verify_pilot(args.packet, *sources, seed=args.seed, resamples=args.resamples)
                print("VERIFIED: pilot packet and ledgers reproduce exactly")
            return 0
        if args.command == "calibrate":
            from .calibration import calibrate
            result = canonical_json(calibrate(replications=args.replications,
                                              units_per_arm=args.units_per_arm,
                                              resamples=args.resamples,
                                              effect_cents=args.effect_cents,
                                              seed=args.seed))
            if args.output:
                path = Path(args.output)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(result, encoding="utf-8")
            print(result, end="")
            return 0
        if args.command == "bi-diff":
            from .bi import diff_snapshots
            print(canonical_json(diff_snapshots(args.before, args.after)), end="")
            return 0
        if args.command == "proposal-check":
            from .proposals import check_proposal
            print(canonical_json(check_proposal(args.scorecard, args.proposal)), end="")
            return 0
        if args.command == "demo":
            directory = _fixture_dir()
            result = score(directory / "assignments.synthetic.csv", directory / "outcomes.synthetic.csv", directory / "costs.synthetic.csv", plan=directory / "plan.synthetic.json")
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical_json(result), encoding="utf-8")
            print(path)
            return 0
        result = score(args.assignments, args.outcomes, args.costs, plan=args.plan, seed=args.seed, resamples=args.resamples)
        if args.command == "score":
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical_json(result), encoding="utf-8")
            print(path)
            return 0
        expected = json.loads(Path(args.scorecard).read_text(encoding="utf-8"))
        if result != expected:
            raise DataError("verification failed: scorecard differs from recomputed result")
        print("VERIFIED: sources and scorecard reproduce exactly")
        return 0
    except (DataError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
