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
