import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.alert_reviews import evaluate_reviews, review_template
from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError, canonical_json
from growth_decision_engine.monitor import monitor_series
from growth_decision_engine.pilot import pilot_packet


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"
SOURCE_NAMES = ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest")
SOURCES = {name: FIXTURES / ("pilot.synthetic.json" if name == "manifest" else
                             f"{name}.synthetic.{'json' if name == 'plan' else 'csv'}")
           for name in SOURCE_NAMES}


class AlertReviewTests(unittest.TestCase):
    def _monitor(self, folder: Path, as_of: str = "2026-01-12T00:00:00Z") -> Path:
        packet = folder / "packet.json"
        packet.write_text(canonical_json(pilot_packet(*SOURCES.values(), resamples=100)))
        series = folder / "series.json"
        entry = {"packet": str(packet), **{key: str(value) for key, value in SOURCES.items()},
                 "seed": 1729, "resamples": 100}
        series.write_text(canonical_json({"schema_version": "growth-decision-monitor-series/v1",
                                          "snapshots": [entry]}))
        monitor = folder / "monitor.json"
        monitor.write_text(canonical_json(monitor_series(series, as_of=as_of, freshness_hours=24)))
        return monitor

    def test_two_reviewers_bind_to_exact_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = self._monitor(folder)
            template = review_template(monitor, "reviewer_a", "reviewer_b")
            self.assertEqual(len(template["reviews"]), 2)
            template["reviews"][0]["verdict"] = "actionable"
            template["reviews"][1]["verdict"] = "actionable"
            reviews = folder / "reviews.json"
            reviews.write_text(canonical_json(template))
            result = evaluate_reviews(monitor, reviews)
            self.assertEqual(result["reviewer_confirmed_precision"], 1)
            self.assertEqual(result["unresolved"], 0)
            output = folder / "result.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["alert-review-eval", "--monitor", str(monitor),
                                       "--reviews", str(reviews), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["consensus_actionable"], 1)
            tampered = json.loads(monitor.read_text())
            tampered["as_of"] = "2026-01-13T00:00:00Z"
            monitor.write_text(canonical_json(tampered))
            with self.assertRaisesRegex(DataError, "not bound"):
                evaluate_reviews(monitor, reviews)

    def test_disagreement_and_invalid_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = self._monitor(folder)
            labels = review_template(monitor, "reviewer_a", "reviewer_b")
            labels["reviews"][0]["verdict"] = "actionable"
            labels["reviews"][1]["verdict"] = "false_alarm"
            reviews = folder / "reviews.json"
            reviews.write_text(canonical_json(labels))
            result = evaluate_reviews(monitor, reviews)
            self.assertEqual(result["unresolved"], 1)
            self.assertIsNone(result["reviewer_confirmed_precision"])
            labels["reviews"][1]["reviewer_id"] = "reviewer_a"
            reviews.write_text(canonical_json(labels))
            with self.assertRaisesRegex(DataError, "same reviewer"):
                evaluate_reviews(monitor, reviews)
            labels["reviews"][1]["reviewer_id"] = "reviewer_b"
            labels["reviews"][1]["verdict"] = ["bad"]
            reviews.write_text(canonical_json(labels))
            with self.assertRaisesRegex(DataError, "unknown alert, reviewer or verdict"):
                evaluate_reviews(monitor, reviews)

    def test_template_cli_and_no_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            monitor = self._monitor(folder, as_of="2026-01-10T00:00:00Z")
            reviews = folder / "reviews.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["alert-review-template", "--monitor", str(monitor),
                                       "--reviewer-a", "reviewer_a", "--reviewer-b", "reviewer_b",
                                       "--output", str(reviews)]), 0)
            self.assertEqual(json.loads(reviews.read_text())["reviews"], [])
            self.assertEqual(evaluate_reviews(monitor, reviews)["alert_count"], 0)


if __name__ == "__main__":
    unittest.main()
