import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from growth_decision_engine.cli import main
from growth_decision_engine.core import DataError, canonical_json
from growth_decision_engine.integrations import ax_task_manifest, cognee_context, decision_shadow


FIXTURES = Path(__file__).resolve().parent.parent / "growth_decision_engine" / "fixtures"
DIGEST = "a" * 64


class FakeRouter:
    def predict(self, state, questions):
        assert "unit_id" not in str(state)
        assert "source_owner" not in str(state)
        assert set(questions["review_next_step"]["criteria"]) == {
            "audit_sources", "extend_experiment", "design_followup", "human_review"}
        return {"answers": {"review_next_step": {"type": "choice", "choice": "audit_sources", "confidence": .8}},
                "routing": {"model": "local-test-double"}}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["demo", "--output", str(self.directory / "scorecard.json")]), 0)
        self.scorecard = self.directory / "scorecard.json"

    def test_ax_manifest_requires_pinned_image_and_has_no_credentials_or_egress(self):
        image = "example.com/gde@sha256:" + DIGEST
        manifest = ax_task_manifest(image=image, workspace="synthetic-workspace")
        self.assertEqual(manifest["kind"], "Task")
        self.assertEqual(manifest["apiVersion"], "ax.io/v1alpha1")
        self.assertNotIn("gateway", manifest["spec"])
        self.assertNotIn("env", manifest["spec"])
        with self.assertRaisesRegex(DataError, "pinned"):
            ax_task_manifest(image="example.com/gde:latest", workspace="synthetic-workspace")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["ax-task", "--image", image, "--workspace", "synthetic-workspace",
                                   "--output", str(self.directory / "ax.json")]), 0)
        self.assertEqual(json.loads((self.directory / "ax.json").read_text()), manifest)

    def test_fixture_and_laya_shadow_leave_scorecard_unchanged(self):
        original = self.scorecard.read_bytes()
        fixture = decision_shadow(self.scorecard, "fixture")
        self.assertEqual(fixture["suggested_review_step"], "human_review")
        laya = decision_shadow(self.scorecard, "laya", laya_router=FakeRouter())
        self.assertEqual(laya["suggested_review_step"], "audit_sources")
        self.assertEqual(laya["provider_metadata"]["model"], "local-test-double")
        self.assertEqual(laya["scorecard_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(original, self.scorecard.read_bytes())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["decision-shadow", "--scorecard", str(self.scorecard),
                                   "--provider", "fixture", "--output", str(self.directory / "shadow.json")]), 0)
        self.assertEqual(json.loads((self.directory / "shadow.json").read_text()), fixture)

    def test_jev_needs_explicit_egress_and_rejects_provider_actions(self):
        with self.assertRaisesRegex(DataError, "allow-network"):
            decision_shadow(self.scorecard, "jev")
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-key"}):
            with patch("growth_decision_engine.integrations._post_json", return_value={
                "answers": {"review_next_step": {"type": "choice", "choice": "rollout_now"}}}) as post:
                with self.assertRaisesRegex(DataError, "out-of-contract"):
                    decision_shadow(self.scorecard, "jev", allow_network=True)
                self.assertEqual(post.call_args.args[0], "https://api.typesafe.ai/v1/systemone")
                self.assertNotIn("unit_id", canonical_json(post.call_args.args[1]))

    def test_scorecard_free_text_cannot_enter_aggregate_egress(self):
        report = json.loads(self.scorecard.read_text())
        report["claim"] = "private customer name"
        tampered = self.directory / "tampered.json"
        tampered.write_text(canonical_json(report))
        with self.assertRaisesRegex(DataError, "invalid claim"):
            decision_shadow(tampered, "fixture")
        report["claim"] = "bundled_synthetic_fixture"
        report["plan"]["decision_status"] = "launch campaign now"
        tampered.write_text(canonical_json(report))
        with self.assertRaisesRegex(DataError, "invalid decision status"):
            decision_shadow(tampered, "fixture")

    def test_cognee_local_response_is_hash_bound_untrusted_context(self):
        response = self.directory / "cognee.json"
        response.write_text(canonical_json([{"id": "doc-1", "text": "Review refund reconciliation."}]))
        result = cognee_context(self.scorecard, "refund reconciliation", response_file=response)
        self.assertEqual(result["items"][0]["source_ref_unverified"], "doc-1")
        self.assertEqual(result["authority"], "untrusted_reviewer_context_only")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["cognee-context", "--scorecard", str(self.scorecard),
                                   "--query", "refund reconciliation", "--response-file", str(response),
                                   "--output", str(self.directory / "context.json")]), 0)
        self.assertEqual(json.loads((self.directory / "context.json").read_text()), result)

    def test_cognee_live_is_loopback_only_and_opt_in(self):
        with self.assertRaisesRegex(DataError, "local HTTP origin"):
            cognee_context(self.scorecard, "q", base_url="https://example.com")
        with self.assertRaisesRegex(DataError, "allow-network"):
            cognee_context(self.scorecard, "q", base_url="http://127.0.0.1:8000")
        with patch("growth_decision_engine.integrations._post_json", return_value=[{"text": "A"}]) as post:
            result = cognee_context(self.scorecard, "q", base_url="http://127.0.0.1:8000", allow_network=True)
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:8000/api/v1/search")
        self.assertEqual(post.call_args.args[1]["search_type"], "CHUNKS")
        self.assertEqual(len(result["items"]), 1)
        response = self.directory / "bad.json"
        response.write_text(canonical_json([{"text": "A"}] * 11))
        self.assertEqual(len(cognee_context(self.scorecard, "q", response_file=response)["items"]), 10)


if __name__ == "__main__":
    unittest.main()
