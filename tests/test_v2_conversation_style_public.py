"""Synthetic checks for the optional public Conversation style cue."""

from copy import deepcopy
import unittest

from sales_agent.v2_offline_api import OfflineWorkspaceApi
from sales_agent.v2_pipeline import DeterministicOfflineProvider, V2SalesPipeline
from sales_agent.v2_pipeline.conversation_style import STYLE_VERSION, select_style_shape
from sales_agent.v2_pipeline.providers import _SYSTEM_PROMPTS
from scripts.run_v2_offline_trace_demo import FIRST_DRAFT, FIRST_RAW, FIRST_REVISED, _script


class PublicConversationStyleTests(unittest.TestCase):
    def decision(self, *, strategy="CLARIFY", emphasis="BALANCED", draft="A short answer."):
        return {"selected_strategy": {"strategy_code": strategy},
                "content_contract": {"communication_emphasis": emphasis,
                                     "semantic_draft": draft}}

    def test_output_has_only_fixed_english_style_metadata(self):
        decision = self.decision(draft=(
            "Synthetic University charges 987654 credits. "
            "A private case claims a guaranteed admission."))
        before = deepcopy(decision)
        shape = select_style_shape(decision, "My private reference is STUDENT-SECRET-42.")
        self.assertEqual(decision, before)
        self.assertEqual(set(shape), {"style_version", "style_id", "tone",
                                      "bubble_rhythm", "question_pacing", "rapport_move"})
        self.assertEqual(shape["style_version"], STYLE_VERSION)
        output = " ".join(shape.values())
        for forbidden in ("Synthetic University", "987654", "guaranteed admission",
                          "STUDENT-SECRET-42"):
            self.assertNotIn(forbidden, output)
        self.assertTrue(output.isascii())

    def test_explicit_emphasis_precedes_draft_keywords(self):
        relationship = select_style_shape(self.decision(
            emphasis="RELATIONAL_REASSURANCE", draft="The price affects my budget."))
        direct = select_style_shape(self.decision(
            emphasis="TRANSACTIONAL_VALUE", draft="The payment is due later."))
        self.assertEqual(relationship["style_id"], "relationship")
        self.assertEqual(direct["style_id"], "direct")

    def test_budget_and_payment_cues_require_student_words(self):
        budget_decision = self.decision(draft="We can explain the price and service fee.")
        payment_decision = self.decision(draft="We can discuss the payment process.")
        self.assertEqual(select_style_shape(budget_decision)["style_id"], "default")
        self.assertEqual(select_style_shape(payment_decision, "Tell me about the service scope.")["style_id"],
                         "default")
        self.assertEqual(select_style_shape(budget_decision, "My budget is limited.")["style_id"],
                         "budget")
        self.assertEqual(select_style_shape(payment_decision, "How does payment work?")["style_id"],
                         "payment")

    def test_stop_and_evidence_strategies_take_precedence(self):
        stop = select_style_shape(self.decision(strategy="STOP", emphasis="TRANSACTIONAL_VALUE"),
                                  "Do not reassure me or contact me again.")
        evidence = select_style_shape(self.decision(strategy="VERIFY_EVIDENCE",
                                                    emphasis="RELATIONAL_REASSURANCE"))
        self.assertEqual(stop["style_id"], "exit")
        self.assertIn("pause or refusal", stop["rapport_move"])
        self.assertEqual(evidence["style_id"], "evidence")

    def test_no_reassurance_request_changes_only_fixed_cue(self):
        decision = self.decision(emphasis="RELATIONAL_REASSURANCE")
        normal = select_style_shape(decision, "I feel uncertain.")
        direct = select_style_shape(decision, "Please do not reassure me. Give me specifics.")
        self.assertEqual(normal["style_id"], direct["style_id"])
        for key in ("style_version", "style_id", "tone", "bubble_rhythm", "question_pacing"):
            self.assertEqual(normal[key], direct[key])
        self.assertEqual(direct["rapport_move"],
                         "Skip reassurance. Answer the stated question directly using only approved content.")

    def test_fallback_is_bounded_for_missing_or_malformed_optional_fields(self):
        default = select_style_shape({})
        malformed = select_style_shape({"selected_strategy": None,
                                        "content_contract": {"semantic_draft": None}}, None)
        self.assertEqual(default["style_id"], "default")
        self.assertEqual(malformed, default)
        self.assertEqual(default["question_pacing"], "at_most_one_existing_question")

    def _run_synthetic_turn(self, *, style_enabled: bool, conversation_text: str = FIRST_DRAFT):
        api = OfflineWorkspaceApi()
        self.addCleanup(api.close)
        student_id = api.createStudent({"demo_only": True, "display_name": "Demo Student",
                                        "sales": {"contact_permission": "ALLOWED"}})["student_id"]
        workspace = api.recordInbound(student_id, {"demo_only": True, "raw_text": FIRST_RAW})
        inbound = next(event for event in reversed(workspace["events"])
                       if event["event_type"] == "INBOUND_RECEIVED")
        script = _script(inbound["event_id"])
        decision_provider = DeterministicOfflineProvider({
            "tool_plan": [script["tool_plan"]], "decision": [script["decision"]]})
        conversation_provider = DeterministicOfflineProvider({
            "conversation": [{"messages": [{"type": "text", "content": conversation_text}],
                              "style_transformations": []}]})
        pipeline = V2SalesPipeline(
            store=api._store, decision_provider=decision_provider,
            conversation_provider=conversation_provider,
            style_shape_enabled=style_enabled)
        result = pipeline.run_turn(student_id=student_id,
                                   conversation_id=inbound["conversation_id"],
                                   inbound_event_id=inbound["event_id"],
                                   idempotency_key="synthetic-style-turn")
        return api, student_id, inbound, script, conversation_provider, result

    def test_normal_handoff_has_goal_and_style_is_default_off(self):
        _, _, _, script, baseline_provider, baseline = self._run_synthetic_turn(
            style_enabled=False)
        _, _, _, _, styled_provider, styled = self._run_synthetic_turn(style_enabled=True)
        self.assertEqual(baseline.status, "REVIEW_REQUIRED", baseline.reason_codes)
        self.assertEqual(styled.status, "REVIEW_REQUIRED", styled.reason_codes)
        baseline_input = baseline_provider.calls[0][1]
        styled_input = styled_provider.calls[0][1]
        self.assertEqual(baseline_input["conversation_goal"]["current_objective"],
                         script["decision"]["current_objective"]["goal"])
        self.assertEqual(baseline_input["conversation_goal"]["contract_next_step_pre_review"],
                         script["decision"]["content_contract"]["desired_next_step"])
        self.assertEqual(baseline_input["conversation_goal"]["instruction_status"],
                         "PRE_HUMAN_REVIEW_INTERNAL_GUIDANCE_NOT_CUSTOMER_FACT")
        self.assertNotIn("style_shape", baseline_input)
        self.assertEqual(styled_input.pop("style_shape"),
                         select_style_shape(script["decision"], FIRST_RAW))
        self.assertEqual(styled_input, baseline_input)

    def test_review_rewrite_keeps_goal_and_optional_style(self):
        api, student_id, _, script, _, first = self._run_synthetic_turn(style_enabled=True)
        self.assertEqual(first.status, "REVIEW_REQUIRED")
        comment = "The opening sounds stiff; please make it more natural."
        workspace = api.reviewDraft(student_id, {"demo_only": True, "comment": comment,
                                                 "feedback_type": "NATURALNESS"})
        review = next(event for event in reversed(workspace["events"])
                      if event["event_type"] == "REVIEW")
        revision_provider = DeterministicOfflineProvider({
            "review_reflection": [{"failure_type": "NATURALNESS",
                                   "what_was_wrong": "The opening sounds stiff",
                                   "evidence": ["The opening sounds stiff"],
                                   "revision_plan": ["Make the opening more natural"],
                                   "applies_to": "CURRENT_DRAFT"}],
            "conversation": [{"messages": [{"type": "text", "content": FIRST_REVISED}],
                              "style_transformations": []}],
        })
        pipeline = V2SalesPipeline(store=api._store,
                                   decision_provider=DeterministicOfflineProvider({}),
                                   conversation_provider=revision_provider,
                                   style_shape_enabled=True)
        revised = pipeline.revise_draft(review_event_id=review["event_id"],
                                        idempotency_key="synthetic-style-revision")
        self.assertEqual(revised.status, "REVIEW_REQUIRED", revised.reason_codes)
        revision_input = next(payload for role, payload in revision_provider.calls
                              if role == "conversation")
        self.assertEqual(revision_input["conversation_goal"]["current_objective"],
                         script["decision"]["current_objective"]["goal"])
        self.assertEqual(revision_input["style_shape"], select_style_shape(script["decision"], FIRST_RAW))
        self.assertEqual(revision_input["content_contract"], script["decision"]["content_contract"])

    def test_style_cannot_bypass_post_conversation_gate(self):
        _, _, _, _, _, result = self._run_synthetic_turn(
            style_enabled=True, conversation_text="The approved price is $700. " + FIRST_DRAFT)
        self.assertEqual(result.status, "REVISE_REQUIRED")
        self.assertIn("MATERIAL_PRICE_CHANGED", result.reason_codes)

    def test_style_toggle_requires_a_boolean(self):
        with OfflineWorkspaceApi() as api:
            with self.assertRaisesRegex(ValueError, "style_shape_enabled_must_be_boolean"):
                V2SalesPipeline(store=api._store,
                                decision_provider=DeterministicOfflineProvider({}),
                                conversation_provider=DeterministicOfflineProvider({}),
                                style_shape_enabled="yes")

    def test_provider_prompt_keeps_goal_and_style_below_content_contract(self):
        prompt = _SYSTEM_PROMPTS["conversation"]
        self.assertIn("conversation_goal", prompt)
        self.assertIn("pre-review contract step", prompt)
        self.assertIn("style_shape", prompt)
        self.assertIn("If either conflicts with content_contract, follow content_contract", prompt)
        self.assertIn("Neither is a source of facts, authority", prompt)


if __name__ == "__main__":
    unittest.main()
