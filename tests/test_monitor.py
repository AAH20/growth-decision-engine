import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.core import DataError, canonical_json, score
from growth_decision_engine.cli import main
from growth_decision_engine.monitor import monitor_series
from growth_decision_engine.pilot import pilot_packet
from growth_decision_engine.proposal_benchmark import benchmark_proposals


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"
NAMES = ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest")
FILES = tuple(FIXTURES / ("pilot.synthetic.json" if name == "manifest" else
                            f"{name}.synthetic.{'json' if name == 'plan' else 'csv'}") for name in NAMES)


class MonitorTests(unittest.TestCase):
    def _entry(self, packet: Path, files=FILES):
        return {**{name: str(path) for name, path in zip(NAMES, files)},
                "packet": str(packet), "seed": 1729, "resamples": 100}

    def test_verified_series_and_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "first.json"
            first.write_text(canonical_json(pilot_packet(*FILES, resamples=100)))
            manifest = folder / "later-manifest.json"
            data = json.loads(FILES[-1].read_text())
            data["export_cutoff"] = "2026-01-11T00:00:00Z"
            manifest.write_text(canonical_json(data))
            second_files = FILES[:-1] + (manifest,)
            second = folder / "second.json"
            second.write_text(canonical_json(pilot_packet(*second_files, resamples=100)))
            series = folder / "series.json"
            series.write_text(canonical_json({"schema_version": "growth-decision-monitor-series/v1",
                                              "snapshots": [self._entry(first), self._entry(second, second_files)]}))
            result = monitor_series(series, as_of="2026-01-13T00:00:00Z", freshness_hours=24)
            self.assertEqual(result["verified_snapshots"], 2)
            self.assertEqual(result["alerts"], ["stale_export"])
            self.assertEqual(result["latest_export_age_hours"], 48)
            self.assertEqual(result["latest_diff"]["restated_date_arm_rows"], [])
            self.assertEqual(result, monitor_series(series, as_of="2026-01-13T00:00:00Z", freshness_hours=24))
            output = folder / "monitor.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["bi-monitor", "--series", str(series), "--as-of", "2026-01-13T00:00:00Z",
                                       "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["alerts"], ["stale_export"])
            altered = json.loads(second.read_text())
            altered["human_review_status"] = "approved"
            second.write_text(canonical_json(altered))
            with self.assertRaisesRegex(DataError, "packet differs"):
                monitor_series(series, as_of="2026-01-13T00:00:00Z")

    def test_rejects_series_without_monotone_cutoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            packet = folder / "packet.json"
            packet.write_text(canonical_json(pilot_packet(*FILES, resamples=100)))
            series = folder / "series.json"
            series.write_text(canonical_json({"schema_version": "growth-decision-monitor-series/v1",
                                              "snapshots": [self._entry(packet), self._entry(packet)]}))
            with self.assertRaisesRegex(DataError, "repeats a packet"):
                monitor_series(series, as_of="2026-01-11T00:00:00Z")

    def test_detects_source_backed_daily_restatement(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = folder / "first.json"
            first.write_text(canonical_json(pilot_packet(*FILES, resamples=100)))
            outcomes = folder / "outcomes.csv"
            outcomes.write_text(FILES[1].read_text().replace(
                "S001,2026-01-08T00:00:00Z", "S001,2026-01-09T00:00:00Z"))
            manifest = folder / "later.json"
            metadata = json.loads(FILES[-1].read_text())
            metadata["export_cutoff"] = "2026-01-11T00:00:00Z"
            manifest.write_text(canonical_json(metadata))
            changed_files = (FILES[0], outcomes, *FILES[2:-1], manifest)
            second = folder / "second.json"
            second.write_text(canonical_json(pilot_packet(*changed_files, resamples=100)))
            series = folder / "series.json"
            series.write_text(canonical_json({"schema_version": "growth-decision-monitor-series/v1",
                                              "snapshots": [self._entry(first), self._entry(second, changed_files)]}))
            result = monitor_series(series, as_of="2026-01-11T00:00:00Z")
            self.assertIn("bi_history_restated_or_removed", result["alerts"])
            self.assertEqual(result["latest_diff"]["source_files_changed"], ["outcomes"])
            self.assertTrue(result["latest_diff"]["restated_date_arm_rows"])

    def test_synthetic_proposal_suite_exposes_semantic_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            report.write_text(canonical_json(score(*FILES[:3], plan=FILES[5], resamples=100)))
            result = benchmark_proposals(report, FIXTURES / "proposal-bench.synthetic.json")
            self.assertEqual(result["structural_correct"], 5)
            self.assertEqual(result["semantically_unsupported_accepted"], 1)
            self.assertEqual(result["case_count"], 5)
            output = Path(tmp) / "benchmark.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["proposal-bench", "--scorecard", str(report), "--suite",
                                       str(FIXTURES / "proposal-bench.synthetic.json"), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["structural_correct"], 5)


if __name__ == "__main__":
    unittest.main()
