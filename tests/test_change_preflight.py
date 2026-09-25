import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from growth_decision_engine.change_preflight import preflight_change
from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError, canonical_json
from growth_decision_engine.pilot import pilot_packet


NAMES = ("assignments", "outcomes", "costs", "billing", "spend", "plan", "manifest")
FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"


def synthetic_evidence(folder: Path, experiment_id: str, prefix: str) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    assignments = ["unit_id,arm,assigned_at"]
    outcomes = ["unit_id,observed_at,accepted,net_revenue_usd,variable_cost_usd"]
    costs = ["unit_id,media_cost_usd,inference_cost_usd,infrastructure_cost_usd,experiment_cost_usd"]
    billing = ["transaction_id,unit_id,posted_at,signed_amount_usd"]
    spend = ["transaction_id,unit_id,category,posted_at,amount_usd"]
    for number in range(20):
        unit = f"{prefix}{number:03d}"
        treated = number >= 10
        assignments.append(f"{unit},{'treatment' if treated else 'control'},2026-01-01T00:00:00Z")
        outcomes.append(f"{unit},2026-01-08T00:00:00Z,{int(treated)},{'100.00' if treated else '0.00'},0.00")
        costs.append(f"{unit},1.00,0.00,0.00,0.00")
        spend.append(f"S-{unit},{unit},media,2026-01-07T00:00:00Z,1.00")
        if treated:
            billing.append(f"B-{unit},{unit},2026-01-07T00:00:00Z,100.00")
    rows = {"assignments": assignments, "outcomes": outcomes, "costs": costs,
            "billing": billing, "spend": spend}
    for name, lines in rows.items():
        (folder / f"{name}.csv").write_text("\n".join(lines) + "\n")
    plan = {"schema_version": "growth-decision-plan/v1", "experiment_id": experiment_id,
            "unit_type": "synthetic_account", "registered_at": "2025-12-30T00:00:00Z",
            "assignment_start": "2025-12-31T00:00:00Z", "assignment_end": "2026-01-02T00:00:00Z",
            "observation_end": "2026-01-09T00:00:00Z", "control_allocation": 0.5,
            "minimum_units_per_arm": 10, "minimum_contribution_effect_usd": "1.00",
            "maximum_incremental_cost_usd_per_unit": "10.00"}
    (folder / "plan.json").write_text(canonical_json(plan))
    manifest = {"schema_version": "growth-decision-pilot/v1", "experiment_id": experiment_id,
                "source_owner": "synthetic test owner", "permission_reference": "synthetic-test-only",
                "export_cutoff": "2026-01-10T00:00:00Z",
                "source_labels": {name: f"synthetic {name}" for name in rows},
                "expected_rows": {name: len(lines) - 1 for name, lines in rows.items()},
                "expected_net_revenue_usd": "1000.00", "expected_total_cost_usd": "20.00"}
    (folder / "manifest.json").write_text(canonical_json(manifest))
    paths = {name: str(folder / (f"{name}.csv" if name in rows else f"{name}.json")) for name in NAMES}
    packet = folder / "packet.json"
    packet.write_text(canonical_json(pilot_packet(*(paths[name] for name in NAMES), resamples=100)))
    return {"packet": str(packet), **paths, "seed": 1729, "resamples": 100}


class ChangePreflightTests(unittest.TestCase):
    def _request(self, folder: Path) -> tuple[Path, dict]:
        request = {"schema_version": "growth-decision-change-request/v1",
                   "as_of": "2026-01-11T00:00:00Z",
                   "evidence": [synthetic_evidence(folder / "one", "experiment-one", "A"),
                                synthetic_evidence(folder / "two", "experiment-two", "B")],
                   "change": {"request_id": "change_01", "kind": "feature_flag_rollout",
                              "current_percent": 5, "target_percent": 10, "rollback_percent": 5,
                              "max_additional_spend_usd": "100.00", "spend_window_hours": 24,
                              "expires_at": "2026-01-12T00:00:00Z", "ticket_reference": "synthetic-ticket",
                              "owner_id": "operator_01"}}
        path = folder / "request.json"
        path.write_text(canonical_json(request))
        return path, request

    def test_positive_synthetic_replication_is_only_eligible_for_external_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path, request = self._request(folder)
            result = preflight_change(path)
            self.assertEqual(result["status"], "eligible_for_external_review")
            self.assertFalse(result["execution_enabled"])
            self.assertEqual(result["blocking_reasons"], [])
            self.assertEqual(len(result["evidence"]), 2)
            output = folder / "preflight.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["change-preflight", "--request", str(path), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text()), result)
            request["change"]["rollback_percent"] = 6
            path.write_text(canonical_json(request))
            with self.assertRaisesRegex(DataError, "rollback"):
                preflight_change(path)

    def test_rejects_tampered_packet_and_flags_reused_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path, request = self._request(folder)
            second_plan = Path(request["evidence"][1]["plan"])
            second_manifest = Path(request["evidence"][1]["manifest"])
            plan = json.loads(second_plan.read_text())
            manifest = json.loads(second_manifest.read_text())
            plan["experiment_id"] = "experiment-one"
            manifest["experiment_id"] = "experiment-one"
            second_plan.write_text(canonical_json(plan))
            second_manifest.write_text(canonical_json(manifest))
            second = request["evidence"][1]
            Path(second["packet"]).write_text(canonical_json(pilot_packet(*(second[name] for name in NAMES), resamples=100)))
            result = preflight_change(path)
            self.assertIn("replication_experiment_id_not_distinct", result["blocking_reasons"])
            packet = json.loads(Path(second["packet"]).read_text())
            packet["human_review_status"] = "approved"
            Path(second["packet"]).write_text(canonical_json(packet))
            with self.assertRaisesRegex(DataError, "packet differs"):
                preflight_change(path)

    def test_bundled_inconclusive_fixture_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path, request = self._request(folder)
            paths = {name: FIXTURES / ("pilot.synthetic.json" if name == "manifest" else
                                     f"{name}.synthetic.{'json' if name == 'plan' else 'csv'}")
                     for name in NAMES}
            packet = folder / "bundled-packet.json"
            packet.write_text(canonical_json(pilot_packet(*(paths[name] for name in NAMES), resamples=100)))
            request["evidence"][0] = {"packet": str(packet), **{name: str(paths[name]) for name in NAMES},
                                      "seed": 1729, "resamples": 100}
            path.write_text(canonical_json(request))
            result = preflight_change(path)
            self.assertEqual(result["status"], "blocked_for_review")
            self.assertIn("evidence_0_not_positive_with_all_gates", result["blocking_reasons"])
            self.assertIn("bundled_synthetic_evidence", result["blocking_reasons"])
            self.assertFalse(result["execution_enabled"])

    def test_rejects_invalid_cap_expiry_and_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path, request = self._request(folder)
            request["change"]["max_additional_spend_usd"] = "1000000.01"
            path.write_text(canonical_json(request))
            with self.assertRaisesRegex(DataError, "spend cap"):
                preflight_change(path)
            request["change"]["max_additional_spend_usd"] = "100.00"
            request["change"]["expires_at"] = "2026-01-13T00:00:00Z"
            path.write_text(canonical_json(request))
            with self.assertRaisesRegex(DataError, "expiry"):
                preflight_change(path)
            request["change"]["expires_at"] = "2026-01-12T00:00:00Z"
            request["as_of"] = "2026-01-09T00:00:00Z"
            path.write_text(canonical_json(request))
            with self.assertRaisesRegex(DataError, "export cutoff exceeds as_of"):
                preflight_change(path)


if __name__ == "__main__":
    unittest.main()
