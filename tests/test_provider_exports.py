import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError
from growth_decision_engine.provider_exports import cloudflare_audit, exposure_audit


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"
ASSIGNMENTS = FIXTURES / "assignments.synthetic.csv"
PLAN = FIXTURES / "plan.synthetic.json"
POSTHOG = FIXTURES / "posthog-exposures.synthetic.csv"
VERCEL = FIXTURES / "vercel-exposures.synthetic.csv"
LOGS = FIXTURES / "cloudflare-http.synthetic.ndjson"
JOB = FIXTURES / "cloudflare-job.synthetic.json"


def audit(path=POSTHOG, provider="posthog", expected=7):
    return exposure_audit(ASSIGNMENTS, path, PLAN, provider=provider,
                          flag_key="checkout-experiment", expected_events=expected)


class ProviderExportTests(unittest.TestCase):
    def test_posthog_projection_is_diagnostic_only(self):
        report = audit()
        self.assertEqual(report["observed_event_rows"], 7)
        self.assertEqual(report["distinct_exposed_units"], 6)
        self.assertEqual(report["assigned_units_without_exposure"], 2)
        self.assertEqual(report["exposure_coverage"], 0.75)
        self.assertIn("diagnostic_only", report["causal_use"])

    def test_vercel_projection_keeps_assignment_census(self):
        report = audit(VERCEL, "vercel", 8)
        self.assertEqual(report["distinct_exposed_units"], 8)
        self.assertEqual(report["assigned_units_without_exposure"], 0)
        self.assertEqual(report["exposed_by_arm"], {"control": 4, "treatment": 4})

    def test_exposure_rejects_conflict_duplicate_unknown_and_incomplete_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.csv"
            source = POSTHOG.read_text()
            path.write_text(source.replace("P001,S001", "P001,OTHER", 1))
            with self.assertRaisesRegex(DataError, "unknown unit_id"):
                audit(path)
            path.write_text(source + source.splitlines()[1] + "\n")
            with self.assertRaisesRegex(DataError, "duplicate event_id"):
                audit(path, expected=8)
            path.write_text(source.replace("S001,checkout-experiment,control", "S001,checkout-experiment,treatment", 1))
            with self.assertRaisesRegex(DataError, "variant conflicts"):
                audit(path)
            with self.assertRaisesRegex(DataError, "row count differs"):
                audit(expected=6)

    def test_exposure_rejects_preassignment_and_unexpected_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.csv"
            source = POSTHOG.read_text()
            path.write_text(source.replace("2026-01-02T00:00:00Z", "2025-12-01T00:00:00Z", 1))
            with self.assertRaisesRegex(DataError, "outside assigned observation window"):
                audit(path)
            path.write_text(source.replace("checkout-experiment", "other-flag", 1))
            with self.assertRaisesRegex(DataError, "unexpected flag_key"):
                audit(path)

    def test_exposure_rejects_assignment_outside_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            assignments = Path(tmp) / "assignments.csv"
            assignments.write_text(ASSIGNMENTS.read_text().replace("2026-01-01T00:00:00Z",
                                                                    "2026-01-04T00:00:00Z", 1))
            with self.assertRaisesRegex(DataError, "outside declared plan window"):
                exposure_audit(assignments, POSTHOG, PLAN, provider="posthog",
                               flag_key="checkout-experiment", expected_events=7)

    def test_cloudflare_sampled_context_is_not_causal(self):
        report = cloudflare_audit(LOGS, JOB)
        self.assertEqual(report["observed_requests"], 5)
        self.assertEqual(report["observed_status_classes"]["5xx"], 1)
        self.assertEqual(report["sampling"]["status"], "known_sampled")
        self.assertEqual(report["observed_5xx_fraction"], 0.2)
        self.assertIn("descriptive_only", report["causal_use"])

    def test_cloudflare_rejects_sensitive_fields_duplicates_and_bad_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs.ndjson"
            content = LOGS.read_text()
            path.write_text(content.replace('"RayID": "R001"', '"RayID": "R001", "ClientIP": "192.0.2.1"', 1))
            with self.assertRaisesRegex(DataError, "allowlisted"):
                cloudflare_audit(path, JOB)
            path.write_text(content + content.splitlines()[0] + "\n")
            with self.assertRaisesRegex(DataError, "duplicate RayID"):
                cloudflare_audit(path, JOB)
            manifest = Path(tmp) / "job.json"
            data = json.loads(JOB.read_text())
            data["expected_records"] = 4
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(DataError, "count differs"):
                cloudflare_audit(LOGS, manifest)

    def test_cli_writes_both_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            exposure_output = Path(tmp) / "exposure.json"
            edge_output = Path(tmp) / "edge.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["exposure-audit", "--provider", "posthog",
                                       "--assignments", str(ASSIGNMENTS), "--exposures", str(POSTHOG),
                                       "--plan", str(PLAN), "--flag-key", "checkout-experiment",
                                       "--expected-events", "7", "--output", str(exposure_output)]), 0)
                self.assertEqual(main(["cloudflare-audit", "--logs", str(LOGS),
                                       "--job-manifest", str(JOB), "--output", str(edge_output)]), 0)
            self.assertEqual(json.loads(exposure_output.read_text())["provider"], "posthog")
            self.assertEqual(json.loads(edge_output.read_text())["observed_requests"], 5)


if __name__ == "__main__":
    unittest.main()
