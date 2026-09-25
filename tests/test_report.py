import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError, canonical_json
from growth_decision_engine.report import render_snapshot_report, write_snapshot_report
from growth_decision_engine.snapshot import snapshot_pilot


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"
FILES = tuple(FIXTURES / ("pilot.synthetic.json" if name == "manifest" else
                          f"{name}.synthetic.{'json' if name == 'plan' else 'csv'}")
              for name in ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest"))


class ReportTests(unittest.TestCase):
    def test_private_report_has_aggregate_economics_and_no_unit_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            created = snapshot_pilot(folder / "bundle", *FILES, resamples=100)
            output = folder / "pilot-review.html"
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["render-report", "--snapshot", created["directory"],
                                       "--expected-inventory-sha256", created["inventory_sha256"],
                                       "--output", str(output)]), 0)
            self.assertEqual(printed.getvalue().strip(), str(output.resolve()))
            html = output.read_text()
            self.assertIn("Bundled synthetic example", html)
            self.assertIn("$15.75", html)
            self.assertIn("Inconclusive requires human review", html)
            self.assertIn("Content-Security-Policy", html)
            self.assertNotIn("<script", html)
            self.assertNotIn("S001", html)
            self.assertNotIn("B001", html)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(DataError, "already exists"):
                write_snapshot_report(created["directory"], output)
            with self.assertRaisesRegex(DataError, "outside snapshot directory"):
                write_snapshot_report(created["directory"], folder / "bundle" / "inside.html")
            with self.assertRaisesRegex(DataError, "external expected digest"):
                render_snapshot_report(created["directory"], expected_inventory_sha256="0" * 64)

    def test_operator_metadata_is_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            plan_path = folder / "plan.json"
            manifest_path = folder / "manifest.json"
            plan = json.loads(FILES[5].read_text())
            manifest = json.loads(FILES[6].read_text())
            dangerous = "<script>alert(1)</script>"
            plan["experiment_id"] = dangerous
            manifest["experiment_id"] = dangerous
            plan_path.write_text(canonical_json(plan))
            manifest_path.write_text(canonical_json(manifest))
            created = snapshot_pilot(folder / "bundle", *FILES[:5], plan_path, manifest_path, resamples=100)
            html = render_snapshot_report(created["directory"])
            self.assertIn("Operator-supplied data", html)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
            self.assertNotIn(dangerous, html)


if __name__ == "__main__":
    unittest.main()
