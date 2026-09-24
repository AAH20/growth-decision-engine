"""Statistical evaluation tests focus on determinism and honest boundaries."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.calibration import calibrate
from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError
from growth_decision_engine.stats import contribution_interval, wilson_interval


class CalibrationTests(unittest.TestCase):
    def test_bootstrap_constant_effect(self):
        self.assertEqual(contribution_interval([100] * 12, [350] * 12,
                                               seed=7, resamples=100), (2.5, 2.5))

    def test_wilson_bounds(self):
        self.assertEqual(wilson_interval(0, 100)[0], 0)
        self.assertEqual(wilson_interval(100, 100)[1], 1)
        self.assertLess(wilson_interval(50, 100)[0], 0.5)
        self.assertGreater(wilson_interval(50, 100)[1], 0.5)

    def test_calibration_repeatability_and_claim(self):
        args = {"replications": 20, "units_per_arm": 10, "resamples": 100,
                "effect_cents": 300, "seed": 81}
        report = calibrate(**args)
        self.assertEqual(report, calibrate(**args))
        self.assertEqual(report["claim"], "synthetic_single_distribution_diagnostic")
        self.assertEqual(report["parameters"]["total_bootstrap_unit_draws"], 80000)
        for name in ("null_false_positive", "known_effect_interval_coverage",
                     "known_effect_positive_detection"):
            metric = report[name]
            self.assertEqual(metric["rate"], metric["count"] / 20)
            self.assertTrue(0 <= metric["monte_carlo_95pct_wilson_interval"][0]
                            <= metric["monte_carlo_95pct_wilson_interval"][1] <= 1)

    def test_rejects_excessive_work_and_invalid_effect(self):
        with self.assertRaisesRegex(DataError, "20 million unit draws"):
            calibrate(replications=200, units_per_arm=1000, resamples=100)
        with self.assertRaisesRegex(DataError, "effect-cents"):
            calibrate(effect_cents=0)

    def test_cli_writes_same_json_it_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["calibrate", "--replications", "20",
                                       "--units-per-arm", "10", "--resamples", "100",
                                       "--output", str(path)]), 0)
            self.assertEqual(path.read_text(), stdout.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["protocol"],
                             "growth-decision-calibration/v1")


if __name__ == "__main__":
    unittest.main()
