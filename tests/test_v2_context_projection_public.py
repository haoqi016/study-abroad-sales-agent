"""Synthetic checks for the English, source-linked V2 context projection."""

from __future__ import annotations

import re
import unittest

from sales_agent.v2_offline_api import OfflineWorkspaceApi
from sales_agent.v2_pipeline.context_projection import (
    build_commercial_decision_view, conversation_goal_from_decision,
    project_customer_history,
)
from scripts.run_v2_offline_trace_demo import _script


def _message(index: int, text: str, role: str = "student") -> dict:
    return {"event_id": f"event-{index}", "role": role, "text": text}


class ContextProjectionPublicTests(unittest.TestCase):
    def test_recent_tail_and_older_index_keep_source_identity(self) -> None:
        messages = [_message(0, "If the price works, I can sign today.")]
        messages.extend(_message(index, f"General update {index}.",
                                 "sales" if index % 2 else "student") for index in range(1, 20))
        messages.append(_message(20, "Can you explain the price and contract?"))
        projected = project_customer_history(messages, latest_student_text=messages[-1]["text"])

        self.assertEqual([item["event_id"] for item in projected["customer_visible_history"]],
                         [f"event-{index}" for index in range(5, 21)])
        self.assertEqual(projected["history_projection"]["older_count"], 5)
        self.assertTrue(projected["history_projection"]["latest_message_kept_exactly"])
        self.assertEqual(projected["older_history_index"][0]["source_event_id"], "event-0")
        self.assertEqual(projected["older_history_index"][0]["summary_kind"],
                         "DETERMINISTIC_TOPIC_INDEX_NOT_VERBATIM")
        self.assertEqual(projected["older_key_original_quotes"][0]["source_event_id"], "event-0")
        self.assertIn("sign today", projected["older_key_original_quotes"][0]["exact_text"])
        self.assertNotIn(messages[0], projected["customer_visible_history"])

    def test_long_latest_message_is_exact_and_history_remains_contiguous(self) -> None:
        latest = "I need to understand the service scope. " + "x" * 3100
        messages = [_message(0, "My budget is $900."),
                    _message(1, "We can review the service scope.", "sales"),
                    _message(2, latest)]
        projected = project_customer_history(messages, latest_student_text=latest)

        self.assertEqual(projected["customer_visible_history"], [messages[-1]])
        self.assertEqual(projected["history_projection"]["older_count"], 2)
        self.assertTrue(projected["history_projection"]["latest_message_kept_exactly"])
        self.assertEqual(len(projected["customer_visible_history"][0]["text"]), len(latest))
        with self.assertRaisesRegex(ValueError, "non_customer_visible_history_item"):
            project_customer_history([_message(0, "Internal draft", "draft")],
                                     latest_student_text="Internal draft")

    def test_commercial_view_distinguishes_events_from_model_hypothesis(self) -> None:
        context = {
            "latest_student_message_event_id": "inbound-2",
            "latest_student_raw_text": "Please send the draft contract before I decide.",
            "working_memory": {"previous_customer_message_sent_event_ids": ["sent-1"]},
        }
        events = [
            {"event_id": "sent-1", "event_type": "HUMAN_SENT",
             "payload": {"actual_sent_text": "Package A costs $1000."}},
            {"event_id": "inbound-2", "event_type": "INBOUND_RECEIVED",
             "payload": {"raw_text": context["latest_student_raw_text"]}},
            {"event_id": "assessment-1", "event_type": "PROGRESS_ASSESSMENT",
             "payload": {"inbound_event_id": "inbound-2", "reply_to_sent_event_id": "sent-1",
                         "predicted_signal": "INTERESTED"}},
            {"event_id": "outcome-old", "event_type": "COMMERCIAL_OUTCOME",
             "payload": {"outcome_type": "PAYMENT", "amount": 1000,
                         "currency": "USD", "verification_status": "UNVERIFIED"}},
            {"event_id": "outcome-corrected", "event_type": "COMMERCIAL_OUTCOME",
             "payload": {"outcome_type": "NO_PAYMENT", "amount": None,
                         "currency": "USD", "verification_status": "VERIFIED",
                         "corrects_event_id": "outcome-old"}},
            {"event_id": "decision-1", "event_type": "DECISION_READY",
             "payload": {"selected_strategy": {"purchase_blocker": "Contract not reviewed",
                                                "next_milestone": "Review contract"},
                         "content_contract": {"desired_next_step": "Review contract"},
                         "current_objective": {"goal": "Clarify terms", "why_now": "Asked for contract"}}},
        ]
        memory = [{"active": True, "category": "OFFER_HISTORY",
                   "source_event_ids": ["sent-1"],
                   "value": {"price": 1000, "currency": "USD", "price_status": "QUOTED",
                             "payment_terms": [], "actual_sent_text": "Package A costs $1000."}}]
        view = build_commercial_decision_view(
            context=context, all_events=events, memory_items=memory,
            active_outcome_ids={"outcome-corrected"})

        facts = view["observed_facts"]
        self.assertEqual(facts["latest_actual_sent"]["source_event_id"], "sent-1")
        self.assertEqual(facts["actual_sent_quotes"][0]["price"], 1000)
        self.assertEqual(facts["customer_replies_with_recorded_link"][0]["link_status"],
                         "RECORDED_LINK_NOT_CAUSAL_PROOF")
        self.assertEqual([item["source_event_id"] for item in facts["commercial_outcomes"]],
                         ["outcome-corrected"])
        self.assertEqual(view["previous_working_judgment"]["epistemic_status"],
                         "AGENT_HYPOTHESIS_UNVERIFIED")
        self.assertEqual(view["previous_working_judgment"]["outcome_assessment"], "INTERESTED")
        self.assertNotEqual(view["previous_working_judgment"]["outcome_assessment"],
                            facts["commercial_outcomes"][0]["outcome_type"])

    def test_internal_conversation_goal_does_not_change_approved_step(self) -> None:
        goal = conversation_goal_from_decision({
            "current_objective": {"goal": "Clarify terms", "why_now": "Student asked"},
            "selected_strategy": {"next_milestone": "Sign the contract today"},
            "content_contract": {"desired_next_step": "Ask whether terms should be reviewed"},
        })
        self.assertEqual(goal["desired_customer_action"], "Ask whether terms should be reviewed")
        self.assertEqual(goal["contract_next_step_pre_review"], "Ask whether terms should be reviewed")
        self.assertNotIn("Sign the contract today", str(goal))
        self.assertEqual(goal["instruction_status"],
                         "PRE_HUMAN_REVIEW_INTERNAL_GUIDANCE_NOT_CUSTOMER_FACT")

    def test_pipeline_passes_projection_to_decision_without_draft_leakage(self) -> None:
        class CaptureProvider:
            def __init__(self) -> None:
                self.script: dict = {}
                self.decision_input: dict | None = None

            def generate_json(self, role: str, payload: dict) -> dict:
                if role == "decision":
                    self.decision_input = payload
                return self.script[role]

        provider = CaptureProvider()
        with OfflineWorkspaceApi(local_provider_factory=lambda: provider) as api:
            student_id = api.createStudent({"demo_only": True, "display_name": "Demo Student",
                                            "sales": {"contact_permission": "ALLOWED"}})["student_id"]
            workspace = api.recordInbound(student_id, {"demo_only": True,
                                                        "raw_text": "My total budget is at most $930, and the full package feels too expensive."})
            inbound = next(event for event in reversed(workspace["events"])
                           if event["event_type"] == "INBOUND_RECEIVED")
            provider.script = _script(inbound["event_id"])
            result = api.requestDecision(student_id)
            self.assertEqual(result["pipeline"]["status"], "REVIEW_REQUIRED")
            self.assertIsNotNone(provider.decision_input)
            model_input = provider.decision_input
            self.assertEqual(model_input["commercial_decision_view"]["observed_facts"]
                             ["latest_student_message"]["source_event_id"], inbound["event_id"])
            self.assertEqual(model_input["turn_context"]["history_window_limit"], 16)
            self.assertEqual(model_input["turn_context"]["history_projection"]["recent_count"], 1)
            self.assertIsNone(model_input["commercial_decision_view"]["observed_facts"]
                              ["latest_actual_sent"])
            self.assertNotIn("DRAFTED", str(model_input))

    def test_projection_source_has_no_cjk_literals(self) -> None:
        from pathlib import Path
        from sales_agent.v2_pipeline import context_projection

        source = Path(context_projection.__file__).read_text()
        self.assertIsNone(re.search(r"[\u3400-\u9fff]", source))


if __name__ == "__main__":
    unittest.main()
