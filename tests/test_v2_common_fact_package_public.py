"""Source and authority boundaries in the public common fact package."""

import unittest

from sales_agent.v2_pipeline.context_projection import (
    build_common_fact_package, project_tool_planning_context,
)


def _event(event_id, event_type, payload, *, actor="SYSTEM"):
    return {"event_id": event_id, "event_type": event_type,
            "student_id": "student-1", "actor": actor, "payload": payload}


def _context():
    return {
        "student_id": "student-1", "context_id": "context-1",
        "latest_student_message_event_id": "inbound-2",
        "latest_observation_event_ids": ["inbound-2", "review-1"],
        "latest_student_raw_text": "Can we review the payment terms?",
        "student_record_field_provenance": {"education.grade": {"source": "synthetic-record"}},
        "current_state": {"contact_permission": "ALLOWED"},
        "working_memory": {"previous_objective": {"goal": "Internal prior goal"},
                           "previous_customer_message_sent_event_ids": ["sent-1"]},
    }


class CommonFactPackagePublicTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            _event("inbound-1", "INBOUND_RECEIVED", {"raw_text": "My budget is flexible."}),
            _event("sent-1", "HUMAN_SENT", {"actual_sent_text": "We can review the scope."}, actor="SALESPERSON"),
            _event("inbound-2", "INBOUND_RECEIVED", {"raw_text": "Can we review the payment terms?"}),
            _event("review-1", "REVIEW", {"feedback_types": ["FACTUAL"],
                                           "comment": "Internal review only", "route_to": "DECISION_AGENT"}),
            _event("decision-1", "DECISION_READY", {"selected_strategy": {"purchase_blocker": "Unverified"},
                                                     "current_objective": {"goal": "Internal prior goal"}}),
            _event("gold-1", "GOLD_STAGE_RECORDED", {"output": {"secret": "example-only"}}),
            _event("holdout-1", "HOLDOUT_CASE", {"text": "hidden evaluation"}),
        ]

    def test_tool_planning_sees_observations_not_internal_judgment_or_gold(self):
        memories = [
            {"memory_id": "m1", "active": True, "category": "BUDGET", "key": "budget",
             "value": "flexible", "epistemic_status": "CUSTOMER_STATED", "source_event_ids": ["inbound-1"]},
            {"memory_id": "m2", "active": True, "category": "GOAL", "key": "unverified",
             "value": "maybe ready", "epistemic_status": "AGENT_HYPOTHESIS", "source_event_ids": ["decision-1"]},
            {"memory_id": "m3", "active": True, "category": "BUDGET", "key": "example",
             "value": "example-only", "epistemic_status": "CUSTOMER_STATED", "source_event_ids": ["gold-1"]},
            {"memory_id": "m4", "active": True, "category": "BUDGET", "key": "holdout",
             "value": "hidden evaluation", "epistemic_status": "CUSTOMER_STATED", "source_event_ids": ["holdout-1"]},
        ]
        package = build_common_fact_package(context=_context(), all_events=self.events,
                                            memory_items=memories, student_record={"education": {"grade": "Final year"}})
        planning = project_tool_planning_context(package)
        self.assertEqual([item["memory_id"] for item in planning["long_term_memory"]], ["m1"])
        self.assertEqual(planning["customer_visible_history"][-1]["text"],
                         "Can we review the payment terms?")
        self.assertEqual(planning["customer_visible_history"][-2]["text"], "We can review the scope.")
        self.assertNotIn("observations", planning)
        self.assertNotIn("working_memory", planning)
        self.assertNotIn("commercial_decision_view", planning)
        self.assertNotIn("example-only", str(planning))
        self.assertNotIn("hidden evaluation", str(planning))
        self.assertEqual(package["turn_context"]["long_term_memory"]["unverified_hypotheses"][0]["memory_id"], "m2")

    def test_claimed_customer_fact_without_inbound_source_is_unverified(self):
        memories = [{"memory_id": "m1", "active": True, "category": "BUDGET", "key": "claimed",
                     "value": "confirmed budget", "epistemic_status": "CUSTOMER_STATED",
                     "source_event_ids": ["sent-1"]}]
        package = build_common_fact_package(context=_context(), all_events=self.events, memory_items=memories)
        self.assertEqual(project_tool_planning_context(package)["long_term_memory"], [])
        item = package["turn_context"]["long_term_memory"]["unverified_hypotheses"][0]
        self.assertEqual(item["source_status"], "SOURCE_TYPE_MISMATCH")

    def test_rejects_stale_latest_or_cross_student_event(self):
        with self.assertRaisesRegex(ValueError, "stale_latest_student_event"):
            build_common_fact_package(context=_context(), all_events=self.events + [
                _event("inbound-3", "INBOUND_RECEIVED", {"raw_text": "New question"})])
        foreign = [dict(event) for event in self.events]
        foreign[0]["student_id"] = "student-2"
        with self.assertRaisesRegex(ValueError, "cross_student_context_event"):
            build_common_fact_package(context=_context(), all_events=foreign)


if __name__ == "__main__":
    unittest.main()
