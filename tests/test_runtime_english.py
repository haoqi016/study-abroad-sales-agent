"""Focused English-language safety and synthetic workflow checks."""

from __future__ import annotations

import re
import unittest

from sales_agent.v2_offline_api.progress import assess_explicit_reply
from sales_agent.v2_pipeline.orchestrator import V2SalesPipeline
from sales_agent.v2_pipeline.policy import PolicyViolation, _has_specific_program_fact, validate_conversation
from sales_agent.v2_pipeline.providers import _SYSTEM_PROMPTS
from sales_agent.v2_runtime.store import _sent_offer_currency, _sent_offer_price, _sent_payment_terms
from scripts.run_v2_offline_trace_demo import render_markdown, run_demo


def _decision(base: str) -> dict:
    return {"content_contract": {"semantic_draft": base, "must_include": [], "must_not_include": []}}


class EnglishRuntimeTests(unittest.TestCase):
    def test_english_do_not_contact_stops(self) -> None:
        signal, spans, confidence, may_continue = assess_explicit_reply("Please don't contact me again.")
        self.assertEqual(signal, "DO_NOT_CONTACT")
        self.assertEqual(spans, ["Please don't contact me again."])
        self.assertEqual(confidence, 1.0)
        self.assertFalse(may_continue)
        self.assertTrue(V2SalesPipeline._explicit_do_not_contact("Please don't contact me again."))

    def test_english_conversation_forbidden_promises(self) -> None:
        base = "What would you like to clarify about the service?"
        for message in (
            "We guarantee admission.",
            "You can pay in installments.",
            "This discount ends today.",
            "You will have a dedicated progress dashboard.",
            "We offer automatic deadline reminders.",
        ):
            with self.subTest(message=message), self.assertRaises(PolicyViolation):
                validate_conversation(_decision(base), [{"type": "text", "content": message}], verified_claims=[])

    def test_english_price_change_is_blocked(self) -> None:
        with self.assertRaises(PolicyViolation) as caught:
            validate_conversation(_decision("Package A costs $1000."),
                                  [{"type": "text", "content": "Package A costs $950."}],
                                  verified_claims=[])
        self.assertIn("MATERIAL_PRICE_CHANGED", caught.exception.codes)

    def test_program_claim_screen_distinguishes_claim_from_question(self) -> None:
        self.assertTrue(_has_specific_program_fact("The NUS program is open for applications."))
        self.assertTrue(_has_specific_program_fact("The NUS program tuition is $25000."))
        self.assertFalse(_has_specific_program_fact("Can you check whether the NUS program is open?"))

    def test_actual_sent_english_terms_remain_source_bound(self) -> None:
        sent = "Package A costs $1000. Pay in full."
        self.assertEqual(_sent_offer_price(sent), 1000)
        self.assertEqual(_sent_offer_currency(sent), "USD")
        self.assertEqual(_sent_payment_terms(sent), ["Pay in full"])
        self.assertIsNone(_sent_offer_price("My budget is at most $930."))
        self.assertEqual(_sent_payment_terms("We do not offer a payment plan."), [])
        self.assertEqual(_sent_payment_terms("You can pay in 2 installments."),
                         ["pay in 2 installments"])

    def test_provider_system_prompts_are_english(self) -> None:
        for role, prompt in _SYSTEM_PROMPTS.items():
            with self.subTest(role=role):
                self.assertFalse(re.search(r"[\u3400-\u9fff]", prompt))

    def test_trace_is_english_and_source_bound(self) -> None:
        trace = run_demo()
        rendered = render_markdown(trace)
        self.assertFalse(re.search(r"[\u3400-\u9fff]", rendered))
        self.assertIn("budget.total_ceiling=930", rendered)
        self.assertIn("approval is not a send event", rendered)
        for turn in trace["turns"]:
            raw = turn["inbound"]["payload"]["raw_text"]
            for item in turn["decision"]["payload"]["memory_candidates"]:
                self.assertIn(item["evidence_span"], raw)


if __name__ == "__main__":
    unittest.main()
