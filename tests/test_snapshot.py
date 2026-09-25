import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError
from growth_decision_engine.monitor import monitor_snapshots
from growth_decision_engine.snapshot import snapshot_pilot, verify_snapshot


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"
NAMES = ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest")
FILES = tuple(FIXTURES / ("pilot.synthetic.json" if name == "manifest" else
                          f"{name}.synthetic.{'json' if name == 'plan' else 'csv'}") for name in NAMES)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_replays_and_detects_changed_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "private-pilot"
            created = snapshot_pilot(directory, *FILES, resamples=100)
            self.assertEqual(created["snapshot"]["claim"], "bundled_synthetic_fixture")
            self.assertEqual(verify_snapshot(directory)["status"], "verified_against_local_copy")
            self.assertEqual(verify_snapshot(directory, expected_inventory_sha256=created["inventory_sha256"])["inventory_sha256"],
                             created["inventory_sha256"])
            with self.assertRaisesRegex(DataError, "external expected digest"):
                verify_snapshot(directory, expected_inventory_sha256="0" * 64)
            self.assertEqual(set(item.name for item in directory.iterdir()),
                             {"assignments.csv", "outcomes.csv", "costs.csv", "billing.csv", "spend.csv",
                              "plan.json", "manifest.json", "packet.json", "snapshot.json"})
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertTrue(all((file.stat().st_mode & 0o777) == 0o600 for file in directory.iterdir()))
            with self.assertRaisesRegex(DataError, "already exists"):
                snapshot_pilot(directory, *FILES, resamples=100)
            cost = directory / "costs.csv"
            cost.write_text(cost.read_text().replace("10.00", "11.00", 1))
            with self.assertRaisesRegex(DataError, "costs differs from inventory"):
                verify_snapshot(directory)

    def test_cli_and_packet_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "bundle"
            args = [item for name, path in zip(NAMES, FILES) for item in (f"--{name}", str(path))]
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["snapshot-pilot", *args, "--resamples", "100", "--directory", str(directory)]), 0)
            self.assertEqual(json.loads(output.getvalue())["directory"], str(directory.resolve()))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["verify-snapshot", "--directory", str(directory)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "verified_against_local_copy")
            packet = directory / "packet.json"
            packet.write_text(packet.read_text() + " ")
            with self.assertRaisesRegex(DataError, "packet differs from inventory"):
                verify_snapshot(directory)

    def test_monitor_snapshot_directories_with_external_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = snapshot_pilot(folder / "first", *FILES, resamples=100)
            manifest = folder / "later-manifest.json"
            data = json.loads(FILES[-1].read_text())
            data["export_cutoff"] = "2026-01-11T00:00:00Z"
            manifest.write_text(json.dumps(data))
            second = snapshot_pilot(folder / "second", *FILES[:-1], manifest, resamples=100)
            roots = [first["directory"], second["directory"]]
            receipts = [first["inventory_sha256"], second["inventory_sha256"]]
            result = monitor_snapshots(roots, as_of="2026-01-13T00:00:00Z",
                                       expected_inventory_sha256s=receipts)
            self.assertEqual(result["verified_snapshots"], 2)
            self.assertEqual(result["alerts"], ["stale_export"])
            self.assertEqual(result["snapshot_inventory_sha256s"], receipts)
            output = folder / "monitor.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["monitor-snapshots", "--snapshot", roots[0], "--snapshot", roots[1],
                                       "--expected-inventory-sha256", receipts[0],
                                       "--expected-inventory-sha256", receipts[1],
                                       "--as-of", "2026-01-13T00:00:00Z", "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text()), result)
            with self.assertRaisesRegex(DataError, "digest count"):
                monitor_snapshots(roots, as_of="2026-01-13T00:00:00Z",
                                  expected_inventory_sha256s=receipts[:1])
            with self.assertRaisesRegex(DataError, "repeats a snapshot"):
                monitor_snapshots([roots[0], roots[0]], as_of="2026-01-13T00:00:00Z")

    def test_rejects_git_destination_and_cleans_partial_on_invalid_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            repository = folder / "repo"
            repository.mkdir()
            (repository / ".git").mkdir()
            with self.assertRaisesRegex(DataError, "outside a Git repository"):
                snapshot_pilot(repository / "bundle", *FILES, resamples=100)
            bad = folder / "bad.csv"
            bad.write_text("broken\n")
            with self.assertRaises(DataError):
                snapshot_pilot(folder / "bad-bundle", bad, *FILES[1:], resamples=100)
            self.assertFalse((folder / "bad-bundle").exists())
            self.assertEqual(list(folder.glob(".bad-bundle.partial-*")), [])
            if hasattr(os, "symlink"):
                link = folder / "link.csv"
                link.symlink_to(FILES[0])
                with self.assertRaises(DataError):
                    snapshot_pilot(folder / "link-bundle", link, *FILES[1:], resamples=100)


if __name__ == "__main__":
    unittest.main()
