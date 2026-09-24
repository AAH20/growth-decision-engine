import hashlib
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from growth_decision_engine.bi import diff_snapshots
from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError, canonical_json, score
from growth_decision_engine.proposals import check_proposal


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"


def paths():
    return [FIXTURES / f"{name}.synthetic.csv" for name in ("assignments", "outcomes", "costs")]


PLAN = FIXTURES / "plan.synthetic.json"


class ScoreTests(unittest.TestCase):
    def test_demo_economics_and_repeatability(self):
        first = score(*paths())
        self.assertEqual(first, score(*paths()))
        self.assertEqual(first["claim"], "bundled_synthetic_fixture")
        self.assertEqual(first["counts"], {"control": 4, "treatment": 4})
        self.assertEqual(first["treatment_minus_control_per_unit"]["accepted"], 0.25)
        self.assertEqual(first["treatment_minus_control_per_unit"]["contribution"], 15.75)
        self.assertEqual(first["arm_means"]["control"]["contribution"], 25.0)
        self.assertEqual(first["arm_means"]["treatment"]["contribution"], 40.75)
        self.assertEqual(first["arm_means"]["control"]["media_cost"], 10.0)
        self.assertEqual(first["arm_means"]["treatment"]["inference_cost"], 2.0)
        self.assertEqual(first["descriptive_unit_economics"]["control"]["media_cost_per_accepted_usd"], 20.0)
        self.assertTrue(canonical_json(first).endswith("\n"))

    def test_rejects_mismatched_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outcomes.csv"
            path.write_text((paths()[1]).read_text().replace("S008", "UNKNOWN"))
            with self.assertRaisesRegex(DataError, "unit IDs must match"):
                score(paths()[0], path, paths()[2])

    def test_rejects_outcome_before_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outcomes.csv"
            path.write_text((paths()[1]).read_text().replace("2026-01-08", "2025-12-31"))
            with self.assertRaisesRegex(DataError, "outcome must follow assignment"):
                score(paths()[0], path, paths()[2])

    def test_rejects_duplicate_and_bad_money(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.csv"
            content = paths()[2].read_text()
            path.write_text(content + content.splitlines()[1] + "\n")
            with self.assertRaisesRegex(DataError, "duplicate"):
                score(paths()[0], paths()[1], path)
            path.write_text(content.replace("12.00", "NaN", 1))
            with self.assertRaisesRegex(DataError, "expected nonnegative"):
                score(paths()[0], paths()[1], path)

    def test_modified_fixture_loses_synthetic_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.synthetic.csv"
            path.write_text(paths()[2].read_text().replace("12.00", "13.00", 1))
            self.assertEqual(score(paths()[0], paths()[1], path)["claim"], "operator_supplied_unverified")

    def test_plan_and_daily_business_intelligence(self):
        report = score(*paths(), plan=PLAN)
        self.assertEqual(report["plan"]["experiment_id"], "synthetic-signup-to-paid-001")
        self.assertTrue(all(report["plan"]["gates"].values()))
        self.assertEqual(report["plan"]["sample_ratio_diagnostic"]["observed_control_share"], 0.5)
        self.assertEqual(report["plan"]["decision_status"], "inconclusive_requires_human_review")
        self.assertEqual(sum(row["contribution_usd"] for row in report["daily_business_intelligence"]), 263.0)
        self.assertEqual(sum(row["accepted"] for row in report["daily_business_intelligence"]), 5)
        self.assertEqual(sum(row["media_cost_usd"] for row in report["daily_business_intelligence"]), 88.0)

    def test_plan_rejects_window_and_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            data = json.loads(PLAN.read_text())
            data["observation_end"] = "2026-01-03T00:00:00Z"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(DataError, "outside declared plan windows"):
                score(*paths(), plan=path)
            data["observation_end"] = "2026-01-09T00:00:00Z"
            data["extra"] = "unreviewed change"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(DataError, "fields must be exactly"):
                score(*paths(), plan=path)

    def test_cli_reproduces_and_rejects_changed_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "score.json"
            args = ["--assignments", str(paths()[0]), "--outcomes", str(paths()[1]),
                    "--costs", str(paths()[2]), "--plan", str(PLAN)]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["score", *args, "--output", str(output)]), 0)
                self.assertEqual(main(["verify", *args, "--scorecard", str(output)]), 0)
            changed = Path(tmp) / "changed.json"
            data = json.loads(PLAN.read_text())
            data["minimum_contribution_effect_usd"] = "2.00"
            changed.write_text(json.dumps(data))
            args[-1] = str(changed)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["verify", *args, "--scorecard", str(output)]), 2)

    def test_bi_diff_reports_restatement(self):
        with tempfile.TemporaryDirectory() as tmp:
            before, after = Path(tmp) / "before.json", Path(tmp) / "after.json"
            baseline = score(*paths(), plan=PLAN)
            before.write_text(canonical_json(baseline))
            baseline["daily_business_intelligence"][0]["contribution_usd"] += 1
            after.write_text(canonical_json(baseline))
            difference = diff_snapshots(before, after)
            self.assertEqual(len(difference["restated_date_arm_rows"]), 1)
            self.assertEqual(difference["source_files_changed"], [])
            self.assertFalse(difference["plan_changed"])

    def test_plan_cost_guardrail_blocks_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            data = json.loads(PLAN.read_text())
            data["maximum_incremental_cost_usd_per_unit"] = "1.00"
            path.write_text(json.dumps(data))
            report = score(*paths(), plan=path)
            self.assertFalse(report["plan"]["gates"]["incremental_cost_guardrail_met"])
            self.assertEqual(report["plan"]["decision_status"], "blocked_for_review")

    def test_plan_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(PLAN.read_text().replace('"experiment_id": "synthetic-signup-to-paid-001",',
                                                     '"experiment_id": "one", "experiment_id": "two",'))
            with self.assertRaisesRegex(DataError, "duplicate JSON key"):
                score(*paths(), plan=path)

    def test_local_resource_limit_fails_closed(self):
        with patch("growth_decision_engine.core.MAX_UNITS", 2):
            with self.assertRaisesRegex(DataError, "unit local safety limit"):
                score(*paths())
        with patch("growth_decision_engine.core.MAX_BOOTSTRAP_PICKS", 100):
            with self.assertRaisesRegex(DataError, "bootstrap work exceeds"):
                score(*paths())

    def test_external_agent_proposal_requires_real_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_file, proposal_file = Path(tmp) / "report.json", Path(tmp) / "proposal.json"
            report_file.write_text(canonical_json(score(*paths(), plan=PLAN)))
            proposal = {"schema_version": "growth-decision-proposal/v1",
                        "report_sha256": hashlib.sha256(report_file.read_bytes()).hexdigest(),
                        "finding": "The observed treatment effect needs further review.",
                        "evidence_paths": ["/treatment_minus_control_per_unit/contribution", "/plan/decision_status"],
                        "next_step": "human_review", "assumptions": ["Synthetic data only"]}
            proposal_file.write_text(json.dumps(proposal))
            self.assertEqual(check_proposal(report_file, proposal_file)["status"], "structurally_grounded_for_human_review")
            proposal["evidence_paths"] = ["/treatment_minus_control_per_unit/invented_metric"]
            proposal_file.write_text(json.dumps(proposal))
            with self.assertRaisesRegex(DataError, "does not resolve"):
                check_proposal(report_file, proposal_file)
            proposal["evidence_paths"] = ["/plan/decision_status"]
            proposal["next_step"] = "change_ad_budget"
            proposal_file.write_text(json.dumps(proposal))
            with self.assertRaisesRegex(DataError, "read-only review action"):
                check_proposal(report_file, proposal_file)


if __name__ == "__main__":
    unittest.main()
