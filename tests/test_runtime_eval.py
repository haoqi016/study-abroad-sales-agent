"""Checks for the public scripted runtime evaluation harness."""

from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from evals.evaluate import DEFAULT_DATA, candidate_input
from evals.run_v2_first_turn import canned_script, run_all, run_case
from sales_agent.v2_offline_api import OfflineOnlyError, OfflineWorkspaceApi


class RuntimeEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(DEFAULT_DATA.read_text(encoding="utf-8"))

    def test_gold_and_continuation_never_enter_canned_provider(self) -> None:
        case = copy.deepcopy(self.data["cases"][0])
        case["gold"] = {"acceptable_actions": ["SECRET_GOLD_SENTINEL"]}
        for key in ("continuation", "title", "targeted_failure"):
            case[key] = "SECRET_GOLD_SENTINEL"
        visible = candidate_input(case)
        script = canned_script("inbound-test-id", visible["student_message"])
        self.assertNotIn("SECRET_GOLD_SENTINEL", json.dumps(script))
        captured = []
        original_queue = OfflineWorkspaceApi.queue_script
        def capture_queue(api, student_id, payload):
            captured.append(copy.deepcopy(payload))
            return original_queue(api, student_id, payload)
        with patch.object(OfflineWorkspaceApi, "queue_script", capture_queue):
            row = run_case(case)
        self.assertEqual(len(captured), 1)
        self.assertNotIn("SECRET_GOLD_SENTINEL", json.dumps(captured))
        # The runtime row is allowed to consult Gold only after generation.
        self.assertEqual(row["execution_status"], "REVIEW_REQUIRED")
        self.assertFalse(row["action_label_match"])

    def test_all_cases_retained_with_explicit_gaps_and_denominators(self) -> None:
        report = run_all(self.data)
        summary = report["summary"]["all"]
        self.assertEqual(summary["attempted"], 24)
        self.assertEqual(len(report["per_case"]), 24)
        self.assertEqual(summary["fixture_gap"], 4)
        self.assertEqual(summary["action_label_eligible"], 18)
        expected_matches = sum(
            c["input"]["contact_permission"] == "ALLOWED"
            and not c["input"]["evidence"]
            and "CLARIFY" in c["gold"]["acceptable_actions"]
            for c in self.data["cases"]
        )
        self.assertEqual(summary["action_label_match"], expected_matches)
        self.assertEqual(summary["runtime_decision_recorded"], 22)
        self.assertEqual(summary["draft_created"], 22)
        self.assertEqual(summary["adapter_blocked"], 1)
        self.assertEqual(summary["execution_error"], 0)
        self.assertEqual(summary["human_semantic_reviewed"], 0)
        self.assertEqual(summary["status_counts"]["HANDOFF"], 1)
        self.assertEqual(sum(summary["status_counts"].values()), 24)
        self.assertTrue(all(not row["continuation_executed"] for row in report["per_case"]))
        self.assertTrue(all(row["actual_send_events"] == 0 for row in report["per_case"]))

    def test_gate_subjects_and_pipeline_ids_refer_to_persisted_events(self) -> None:
        row = run_case(self.data["cases"][0])
        lineage = row["event_lineage"]
        self.assertEqual(lineage["INBOUND_RECEIVED"], [row["inbound_event_id"]])
        self.assertEqual(len(lineage["TURN_CONTEXT_BUILT"]), 1)
        self.assertEqual(len(lineage["DECISION_READY"]), 1)
        self.assertEqual(len(lineage["DRAFTED"]), 1)
        self.assertEqual(len(row["gates"]), 2)
        self.assertEqual(row["pipeline_event_ids"]["context_event_id"], lineage["TURN_CONTEXT_BUILT"][0])
        self.assertEqual(row["pipeline_event_ids"]["decision_event_id"], lineage["DECISION_READY"][0])
        self.assertEqual(row["pipeline_event_ids"]["draft_event_id"], lineage["DRAFTED"][0])
        self.assertEqual(row["gates"][0]["subject_event_id"], lineage["DECISION_READY"][0])
        self.assertEqual(row["gates"][1]["subject_event_id"], lineage["DRAFTED"][0])
        self.assertEqual([g["stage"] for g in row["gates"]], ["PRE_CONVERSATION", "POST_CONVERSATION"])

    def test_failure_remains_in_attempted_denominator(self) -> None:
        original = run_case
        def one_failure(case):
            if case["case_id"] == self.data["cases"][0]["case_id"]:
                return {"case_id": case["case_id"], "split": case["split"],
                        "execution_status": "HARNESS_ERROR", "fixture_gap_codes": [],
                        "event_lineage": {}, "draft_created": False,
                        "gold_scoring_eligible": False, "action_label_match": None}
            return original(case)
        with patch("evals.run_v2_first_turn.run_case", side_effect=one_failure):
            summary = run_all(self.data)["summary"]["all"]
        self.assertEqual(summary["attempted"], 24)
        self.assertEqual(summary["harness_error"], 1)
        self.assertEqual(summary["action_label_eligible"], 17)

    def test_unexpected_request_error_is_not_counted_as_permission_block(self) -> None:
        case = self.data["cases"][0]
        for error in (RuntimeError("provider transport failed"),
                      OfflineOnlyError("unexpected_schema_error")):
            with self.subTest(error=type(error).__name__):
                with patch.object(OfflineWorkspaceApi, "requestDecision", side_effect=error):
                    row = run_case(case)
                self.assertEqual(row["execution_status"], "EXECUTION_ERROR")
                self.assertFalse(row["draft_created"])
                self.assertIsNone(row["permission_safety_observation"])
                self.assertEqual(len(row["event_lineage"]["INBOUND_RECEIVED"]), 1)
        stop_case = next(c for c in self.data["cases"] if c["case_id"] == "CHALLENGE-07")
        self.assertEqual(run_case(stop_case)["execution_status"], "ADAPTER_BLOCKED")


if __name__ == "__main__":
    unittest.main()
