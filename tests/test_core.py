import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.core import DataError, canonical_json, score


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"


def paths():
    return [FIXTURES / f"{name}.synthetic.csv" for name in ("assignments", "outcomes", "costs")]


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


if __name__ == "__main__":
    unittest.main()
