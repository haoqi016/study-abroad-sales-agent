"""Synthetic checks for exact-batch and withheld stale Program safeguards."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sales_agent.v2_offline_api import OfflineWorkspaceApi
from sales_agent.v2_pipeline.orchestrator import (
    PolicyViolation, ToolPorts, V2SalesPipeline, _STALE_VALUE_WITHHELD,
)
from scripts.run_v2_offline_trace_demo import FIRST_RAW, _script


def _stale_response() -> dict:
    return {
        "status": "OK", "source_type": "official_program_record",
        "source_version": "synthetic-program-fixture-v1", "tool_call_id": "synthetic-program-call",
        "limitations": [],
        "data": [{
            "evidence_id": "program_synthetic", "university": "Example University",
            "program": "Example MSc", "country": "SG", "intake_year": 2027,
            "intake_batch": "AY2027/28", "freshness_status": "STALE",
            "facts": {"tuition": {"value": None, "last_checked": "2025-01-01T00:00:00+00:00"}},
            "official_sources": [{"label": "Synthetic fixture",
                                  "url": "https://university.example.edu/synthetic/fees"}],
        }],
    }


class StaleProgramRefreshPublicTests(unittest.TestCase):
    def test_claim_batch_must_equal_returned_evidence_batch(self) -> None:
        pipeline = V2SalesPipeline.__new__(V2SalesPipeline)
        pipeline.tools = ToolPorts()
        claim = {"claim_id": "synthetic-claim", "program_evidence_id": "program_synthetic",
                 "intake_batch": "START2027-08-02"}
        returned = {"program_synthetic": {"source_type": "official_program_record",
                                          "item": {"intake_batch": "AY2027/28"}}}
        with self.assertRaises(PolicyViolation) as caught:
            pipeline._verify_claims([claim], returned=returned, adopted={"program_synthetic"})
        self.assertIn("PROGRAM_CLAIM_BATCH_MISMATCH", caught.exception.codes)

    def test_withheld_stale_field_retains_exact_batch_for_refresh_only(self) -> None:
        claims = V2SalesPipeline._stale_program_claims([
            ("programs", {"contract_version": "sales-tools.v1.1"}, _stale_response())])
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["intake_batch"], "AY2027/28")
        self.assertEqual(claims[0]["expected_value"], _STALE_VALUE_WITHHELD)
        self.assertNotIn("100 credits", str(claims))

    def test_withheld_stale_field_handoffs_without_customer_draft(self) -> None:
        with OfflineWorkspaceApi() as api:
            student_id = api.createStudent({"demo_only": True, "display_name": "Demo Student",
                                            "sales": {"contact_permission": "ALLOWED"}})["student_id"]
            workspace = api.recordInbound(student_id, {"demo_only": True, "raw_text": FIRST_RAW})
            inbound_id = next(event["event_id"] for event in reversed(workspace["events"])
                              if event["event_type"] == "INBOUND_RECEIVED")
            script = _script(inbound_id)
            script["tool_plan"] = {"requests": [{"name": "programs", "args": {
                "contract_version": "sales-tools.v1", "query": {"country": "SG"}}}]}
            api.queue_script(student_id, script)
            with patch.object(ToolPorts, "call", return_value=_stale_response()) as tool_call:
                result = api.requestDecision(student_id)
            self.assertEqual(tool_call.call_count, 1)
            self.assertEqual(result["pipeline"]["status"], "HANDOFF")
            self.assertIn("STALE_PROGRAM_REAPPROVAL_REQUIRED",
                          result["pipeline"]["reason_codes"])
            events = result["workspace"]["events"]
            self.assertTrue(any(event["event_type"] == "INTERNAL_NOTIFICATION" for event in events))
            self.assertFalse(any(event["event_type"] in {"DRAFTED", "HUMAN_SENT"} for event in events))


if __name__ == "__main__":
    unittest.main()
