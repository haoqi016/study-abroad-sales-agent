"""English synthetic checks for external reference and conversation boundaries."""

from __future__ import annotations

from copy import deepcopy
import unittest

from sales_agent.v2_pipeline.policy import PolicyViolation, validate_conversation, validate_decision
from scripts.run_v2_offline_trace_demo import _script


EXTERNAL = "External public reference reports an offer; the outcome is unverified."
RETURNED = {"ev-external": {"source_type": "external_public_reference_unverified", "item": {}},
            "ev-historical": {"source_type": "anonymized_historical_case", "item": {}}}
CONTEXT = {"current_state": {"contact_permission": "ALLOWED"},
           "customer_visible_history": []}


def decision_with(text: str, evidence_ids: list[str] | None = None) -> dict:
    decision = deepcopy(_script("synthetic-inbound")["decision"])
    decision["content_contract"]["semantic_draft"] = text
    decision["evidence_ids"] = list(evidence_ids or [])
    return decision


def conversation(decision: dict, text: str) -> str:
    return validate_conversation(decision, [{"type": "text", "content": text}], verified_claims=[])


class ExternalCasePolicyPublicTests(unittest.TestCase):
    def assert_decision_code(self, decision: dict, code: str) -> None:
        with self.assertRaises(PolicyViolation) as caught:
            validate_decision(CONTEXT, decision, RETURNED)
        self.assertIn(code, caught.exception.codes)

    def assert_conversation_code(self, decision: dict, text: str, code: str) -> None:
        with self.assertRaises(PolicyViolation) as caught:
            conversation(decision, text)
        self.assertIn(code, caught.exception.codes)

    def test_external_outcome_requires_evidence_attribution_and_caveat(self) -> None:
        self.assertEqual(validate_decision(CONTEXT, decision_with(EXTERNAL, ["ev-external"]), RETURNED), [])
        self.assertEqual(validate_decision(CONTEXT,
                                           decision_with("We can review an external program report."),
                                           RETURNED), [])
        self.assert_decision_code(decision_with(EXTERNAL), "EXTERNAL_CASE_EVIDENCE_REQUIRED")
        self.assert_decision_code(decision_with("A case reports an offer; unverified.", ["ev-external"]),
                                  "EXTERNAL_CASE_ATTRIBUTION_REQUIRED")
        self.assert_decision_code(decision_with("An external public reference reports an offer.",
                                                ["ev-external"]), "EXTERNAL_CASE_OUTCOME_UNVERIFIED")
        self.assert_decision_code(decision_with("An external public reference has a similar profile.",
                                                ["ev-external"]), "EXTERNAL_CASE_UNVERIFIED_CAVEAT_REQUIRED")

    def test_external_case_cannot_be_owned_mixed_or_predictive(self) -> None:
        self.assert_decision_code(decision_with(EXTERNAL, ["ev-external", "ev-historical"]),
                                  "MIXED_CASE_PROVENANCE_UNSUPPORTED")
        self.assert_decision_code(decision_with(
            EXTERNAL + " We helped similar students with these cases.", ["ev-external"]),
            "EXTERNAL_CASE_NOT_INSTITUTION_SERVED")
        self.assert_decision_code(decision_with(EXTERNAL + " One of our students got an offer.",
                                                ["ev-external"]),
                                  "EXTERNAL_CASE_NOT_INSTITUTION_SERVED")
        self.assert_decision_code(decision_with(
            EXTERNAL + " Your admission chances are high.", ["ev-external"]),
            "EXTERNAL_CASE_PREDICTION_UNSUPPORTED")

    def test_conversation_preserves_external_source_and_uncertainty(self) -> None:
        decision = decision_with(EXTERNAL, ["ev-external"])
        self.assertEqual(conversation(decision, EXTERNAL), EXTERNAL)
        self.assert_conversation_code(decision, "A case reports an offer; unverified.",
                                      "CONVERSATION_DROPPED_EXTERNAL_ATTRIBUTION")
        self.assert_conversation_code(decision, "An external public reference reports an offer.",
                                      "CONVERSATION_UNVERIFIED_EXTERNAL_OUTCOME")
        self.assert_conversation_code(decision, "An external public reference has a similar profile.",
                                      "CONVERSATION_DROPPED_EXTERNAL_CAVEAT")
        self.assert_conversation_code(decision, EXTERNAL + " We served students in these cases.",
                                      "CONVERSATION_CHANGED_EXTERNAL_CASE_SOURCE")
        self.assert_conversation_code(decision, EXTERNAL + " Your admission chances are high.",
                                      "CONVERSATION_ADDED_EXTERNAL_PREDICTION")
        self.assert_conversation_code(decision_with("What would you like to clarify?"), EXTERNAL,
                                      "CONVERSATION_ADDED_EXTERNAL_CASE")

    def test_conversation_cannot_expand_scope_or_drop_student_participation(self) -> None:
        neutral = decision_with("We can review the application steps together.")
        self.assert_conversation_code(neutral, "We handle every step of the application.",
                                      "CONVERSATION_OVERSTATED_SCOPE")
        participation = decision_with("You still need to provide your application materials.")
        self.assert_conversation_code(participation, "We can review the application materials.",
                                      "CONVERSATION_DROPPED_STUDENT_PARTICIPATION")
        self.assertEqual(conversation(participation,
                                      "Please provide your application materials for review."),
                         "Please provide your application materials for review.")


if __name__ == "__main__":
    unittest.main()
