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
    for name in ("score", "verify"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--assignments", required=True)
        cmd.add_argument("--outcomes", required=True)
        cmd.add_argument("--costs", required=True)
        cmd.add_argument("--seed", type=int, default=1729)
        cmd.add_argument("--resamples", type=int, default=2000)
        if name == "score":
            cmd.add_argument("--output", required=True)
        else:
            cmd.add_argument("--scorecard", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            directory = _fixture_dir()
            result = score(directory / "assignments.synthetic.csv", directory / "outcomes.synthetic.csv", directory / "costs.synthetic.csv")
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(canonical_json(result), encoding="utf-8")
            print(path)
            return 0
        result = score(args.assignments, args.outcomes, args.costs, seed=args.seed, resamples=args.resamples)
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
