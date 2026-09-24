import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError, canonical_json
from growth_decision_engine.pilot import pilot_packet, verify_pilot


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"
FILES = tuple(FIXTURES / f"{name}.synthetic.{extension}" for name, extension in (
    ("assignments", "csv"), ("outcomes", "csv"), ("costs", "csv"),
    ("billing", "csv"), ("spend", "csv"), ("plan", "json"), ("pilot", "json")))


class PilotTests(unittest.TestCase):
    def test_reconciles_refund_and_all_cost_categories(self):
        packet = pilot_packet(*FILES, resamples=100)
        self.assertEqual(packet["claim"], "bundled_synthetic_fixture")
        self.assertEqual(packet["ledger_reconciliation"]["status"], "exact_per_unit")
        self.assertEqual(packet["ledger_reconciliation"]["billing_transactions"], 6)
        self.assertEqual(packet["ledger_reconciliation"]["net_revenue_usd"], 500.0)
        self.assertEqual(packet["ledger_reconciliation"]["total_cost_usd"], 237.0)
        self.assertEqual(packet["source_control_reconciliation"]["observed_rows"]["spend"], 37)
        self.assertEqual(packet["scorecard"]["counts"], {"control": 4, "treatment": 4})
        self.assertEqual(packet["human_review_status"], "pending")
        self.assertEqual(packet, pilot_packet(*FILES, resamples=100))

    def test_rejects_revenue_mismatch_and_unknown_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            billing = Path(tmp) / "billing.csv"
            source = FILES[3].read_text()
            billing.write_text(source.replace("-20.00", "-19.00"))
            with self.assertRaisesRegex(DataError, "net revenue mismatch"):
                pilot_packet(*FILES[:3], billing, *FILES[4:], resamples=100)
            billing.write_text(source.replace("S001", "OTHER", 1))
            with self.assertRaisesRegex(DataError, "unknown unit_id"):
                pilot_packet(*FILES[:3], billing, *FILES[4:], resamples=100)

    def test_rejects_cost_mismatch_duplicate_and_late_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            spend = Path(tmp) / "spend.csv"
            source = FILES[4].read_text()
            spend.write_text(source.replace("C001-media,S001,media,2026-01-07T00:00:00Z,10.00",
                                            "C001-media,S001,media,2026-01-07T00:00:00Z,11.00"))
            with self.assertRaisesRegex(DataError, "media cost mismatch"):
                pilot_packet(*FILES[:4], spend, *FILES[5:], resamples=100)
            spend.write_text(source + source.splitlines()[1] + "\n")
            with self.assertRaisesRegex(DataError, "duplicate transaction_id"):
                pilot_packet(*FILES[:4], spend, *FILES[5:], resamples=100)
            spend.write_text(source.replace("2026-01-07T00:00:00Z", "2026-01-11T00:00:00Z", 1))
            with self.assertRaisesRegex(DataError, "transaction posted after"):
                pilot_packet(*FILES[:4], spend, *FILES[5:], resamples=100)

    def test_modified_manifest_is_not_bundled_and_cutoff_must_cover_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "pilot.json"
            data = json.loads(FILES[6].read_text())
            data["permission_reference"] = "other declaration"
            manifest.write_text(json.dumps(data))
            packet = pilot_packet(*FILES[:6], manifest, resamples=100)
            self.assertEqual(packet["claim"], "operator_supplied_unverified")
            data["export_cutoff"] = "2026-01-08T00:00:00Z"
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(DataError, "precedes plan observation_end"):
                pilot_packet(*FILES[:6], manifest, resamples=100)

    def test_modified_plan_cannot_retain_bundled_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            data = json.loads(FILES[5].read_text())
            data["minimum_contribution_effect_usd"] = "2.00"
            plan.write_text(json.dumps(data))
            packet = pilot_packet(*FILES[:5], plan, FILES[6], resamples=100)
            self.assertEqual(packet["claim"], "operator_supplied_unverified")

    def test_rejects_declared_source_control_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "pilot.json"
            data = json.loads(FILES[6].read_text())
            data["expected_rows"]["billing"] = 5
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(DataError, "row counts differ"):
                pilot_packet(*FILES[:6], manifest, resamples=100)
            data["expected_rows"]["billing"] = 6
            data["expected_net_revenue_usd"] = "501.00"
            manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(DataError, "net revenue differs"):
                pilot_packet(*FILES[:6], manifest, resamples=100)

    def test_cli_packet_replays_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "packet.json"
            args = [item for name, path in zip(("assignments", "outcomes", "costs", "billing",
                                                 "spend", "plan", "manifest"), FILES)
                    for item in (f"--{name}", str(path))]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["pilot", *args, "--resamples", "100", "--output", str(output)]), 0)
                self.assertEqual(main(["verify-pilot", *args, "--resamples", "100", "--packet", str(output)]), 0)
            content = json.loads(output.read_text())
            content["human_review_status"] = "approved"
            output.write_text(canonical_json(content))
            with self.assertRaisesRegex(DataError, "packet differs"):
                verify_pilot(output, *FILES, resamples=100)


if __name__ == "__main__":
    unittest.main()
