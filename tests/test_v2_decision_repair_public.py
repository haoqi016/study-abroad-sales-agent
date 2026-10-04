"""Synthetic checks for one bounded live Decision repair before human review."""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from sales_agent.v2_offline_api import OfflineWorkspaceApi
from sales_agent.v2_pipeline.orchestrator import V2SalesPipeline
from sales_agent.v2_pipeline.providers import LocalOllamaJSONProvider, OpenAICompatibleProvider
from scripts.run_v2_offline_trace_demo import FIRST_RAW, _script


class _StrictScriptedModel:
    """A test double for the strict live-provider path; it performs no I/O."""

    require_complete_output = True

    def __init__(self) -> None:
        self.responses: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, dict]] = []

    def generate_json(self, role: str, payload: dict) -> dict:
        self.calls.append((role, deepcopy(payload)))
        return deepcopy(self.responses[role].pop(0))


def _complete_decision(inbound_id: str) -> dict:
    decision = _script(inbound_id)["decision"]
    decision["selected_strategy"].update({
        "purchase_blocker": "The full package feels too expensive",
        "blocker_evidence_spans": ["the full package feels too expensive"],
        "customer_benefit": "",
        "next_milestone": "Identify the service component behind the concern",
    })
    return decision


def _run_with_decisions(decisions: list[dict]) -> tuple[dict, _StrictScriptedModel, dict]:
    provider = _StrictScriptedModel()
    with OfflineWorkspaceApi(local_provider_factory=lambda: provider) as api:
        student_id = api.createStudent({"demo_only": True, "display_name": "Demo Student",
                                        "sales": {"contact_permission": "ALLOWED"}})["student_id"]
        workspace = api.recordInbound(student_id, {"demo_only": True, "raw_text": FIRST_RAW})
        inbound_id = next(event["event_id"] for event in reversed(workspace["events"])
                          if event["event_type"] == "INBOUND_RECEIVED")
        fixture = _script(inbound_id)

        def bind_inbound(value):
            if isinstance(value, dict):
                return {key: bind_inbound(item) for key, item in value.items()}
            if isinstance(value, list):
                return [bind_inbound(item) for item in value]
            return inbound_id if value == "placeholder" else value

        provider.responses = {"tool_plan": [fixture["tool_plan"]],
                              "decision": [bind_inbound(item) for item in decisions],
                              "conversation": [fixture["conversation"]]}
        result = api.requestDecision(student_id)
        return result, provider, result["workspace"]


class DecisionRepairPublicTests(unittest.TestCase):
    def test_live_provider_schema_requires_explicit_strategy(self) -> None:
        schema = LocalOllamaJSONProvider._decision_format({"available_evidence_ids": []})
        strategy = schema["properties"]["selected_strategy"]
        self.assertEqual(set(strategy["required"]), {
            "strategy_code", "reason", "purchase_blocker", "blocker_evidence_spans",
            "customer_benefit", "next_milestone",
        })
        self.assertEqual(strategy["properties"]["blocker_evidence_spans"]["type"], "array")
        remote = OpenAICompatibleProvider(base_url="https://model.example.test/v1",
                                          model="synthetic-model", api_key="synthetic-key")
        self.assertTrue(remote.require_complete_output)

    def test_remote_repair_prompt_keeps_previous_candidate_unverified(self) -> None:
        remote = OpenAICompatibleProvider(base_url="https://model.example.test/v1",
                                          model="synthetic-model", api_key="synthetic-key")
        reply = BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
        with patch("sales_agent.v2_pipeline.providers.request.urlopen", return_value=reply) as send:
            remote.generate_json("decision", {
                "available_evidence_ids": [],
                "revision_feedback": {"reason_codes": ["MODEL_BLOCKER_EVIDENCE_NOT_VERBATIM"],
                                      "previous_decision": {"selected_strategy": {
                                          "blocker_evidence_spans": ["invented words"]}}},
            })
        request_body = json.loads(send.call_args.args[0].data)
        self.assertIn("single internal repair", request_body["messages"][0]["content"])
        self.assertIn("unverified model output", request_body["messages"][0]["content"])
        self.assertIn("MODEL_BLOCKER_EVIDENCE_NOT_VERBATIM", request_body["messages"][1]["content"])

    def test_unknown_blocker_does_not_require_invented_student_quote(self) -> None:
        decision = _complete_decision("synthetic-inbound")
        decision["selected_strategy"].update({
            "purchase_blocker": "UNKNOWN", "blocker_evidence_spans": []})
        self.assertEqual(V2SalesPipeline._commercial_plan_gaps(decision, FIRST_RAW), [])
        decision["selected_strategy"]["purchase_blocker"] = "Assumed uncertainty"
        self.assertIn("MODEL_BLOCKER_EVIDENCE_MISSING",
                      V2SalesPipeline._commercial_plan_gaps(decision, FIRST_RAW))
        decision["selected_strategy"]["blocker_evidence_spans"] = ["invented student words"]
        self.assertIn("MODEL_BLOCKER_EVIDENCE_NOT_VERBATIM",
                      V2SalesPipeline._commercial_plan_gaps(decision, FIRST_RAW))

    def test_optional_benefit_must_be_realized_when_supplied(self) -> None:
        decision = _complete_decision("synthetic-inbound")
        decision["selected_strategy"]["customer_benefit"] = "A clearer view of the service"
        self.assertIn("MODEL_CUSTOMER_BENEFIT_NOT_REALIZED",
                      V2SalesPipeline._commercial_plan_gaps(decision, FIRST_RAW))
        decision["content_contract"]["semantic_draft"] = (
            "A clearer view of the service. Which part makes the price feel high?")
        decision["content_contract"]["must_include"] = ["A clearer view of the service"]
        self.assertEqual(V2SalesPipeline._commercial_plan_gaps(decision, FIRST_RAW), [])

    def test_complete_live_decision_does_not_repair(self) -> None:
        decision = _complete_decision("placeholder")
        result, provider, _ = _run_with_decisions([decision])
        self.assertEqual(result["pipeline"]["status"], "REVIEW_REQUIRED")
        self.assertEqual([role for role, _ in provider.calls],
                         ["tool_plan", "decision", "conversation"])

    def test_one_repair_can_reach_human_review_without_tool_rerun(self) -> None:
        first = _complete_decision("placeholder")
        first["selected_strategy"]["blocker_evidence_spans"] = ["unsupported quote"]
        repaired = _complete_decision("placeholder")
        result, provider, workspace = _run_with_decisions([first, repaired])
        self.assertEqual(result["pipeline"]["status"], "REVIEW_REQUIRED",
                         result["pipeline"]["reason_codes"])
        self.assertEqual([role for role, _ in provider.calls],
                         ["tool_plan", "decision", "decision", "conversation"])
        feedback = provider.calls[2][1]["revision_feedback"]
        self.assertEqual(feedback["reason_codes"], ["MODEL_BLOCKER_EVIDENCE_NOT_VERBATIM"])
        self.assertEqual(feedback["previous_decision"]["selected_strategy"]
                         ["blocker_evidence_spans"], ["unsupported quote"])
        self.assertEqual(len([event for event in workspace["events"]
                              if event["event_type"] == "DECISION_READY"]), 1)
        self.assertFalse(any(event["event_type"] == "HUMAN_SENT" for event in workspace["events"]))

    def test_second_invalid_decision_handoffs_without_draft(self) -> None:
        first = _complete_decision("placeholder")
        first["selected_strategy"].pop("next_milestone")
        second = _complete_decision("placeholder")
        second["selected_strategy"]["blocker_evidence_spans"] = ["unsupported quote"]
        result, provider, workspace = _run_with_decisions([first, second])
        self.assertEqual(result["pipeline"]["status"], "HANDOFF")
        self.assertIn("MODEL_BLOCKER_EVIDENCE_NOT_VERBATIM", result["pipeline"]["reason_codes"])
        self.assertEqual([role for role, _ in provider.calls],
                         ["tool_plan", "decision", "decision"])
        self.assertFalse(any(event["event_type"] in {"DECISION_READY", "DRAFTED", "HUMAN_SENT"}
                             for event in workspace["events"]))

    def test_repair_cannot_override_human_interpretation(self) -> None:
        provider = _StrictScriptedModel()
        with OfflineWorkspaceApi(local_provider_factory=lambda: provider) as api:
            student_id = api.createStudent({"demo_only": True, "display_name": "Demo Student",
                                            "sales": {"contact_permission": "ALLOWED"}})["student_id"]
            workspace = api.recordInbound(student_id, {"demo_only": True, "raw_text": FIRST_RAW})
            inbound_id = next(event["event_id"] for event in reversed(workspace["events"])
                              if event["event_type"] == "INBOUND_RECEIVED")
            first = _complete_decision(inbound_id)
            api._store.record_inbound_interpretation(
                inbound_event_id=inbound_id,
                normalized_meaning=first["normalized_meaning"],
                normalized_meaning_spans=first["normalized_meaning_spans"],
                hypotheses=first["hypotheses"], unknowns=first["unknowns"],
                actor="SALESPERSON", idempotency_key="synthetic-human-reading")
            first["selected_strategy"].pop("next_milestone")
            second = _complete_decision(inbound_id)
            second["normalized_meaning"] = "A model-only reinterpretation"
            provider.responses = {"tool_plan": [{"requests": []}],
                                  "decision": [first, second]}
            result = api.requestDecision(student_id)
            self.assertEqual(result["pipeline"]["status"], "HANDOFF")
            self.assertIn("DECISION_CONFLICTS_WITH_HUMAN_INTERPRETATION",
                          result["pipeline"]["reason_codes"])
            self.assertEqual([role for role, _ in provider.calls],
                             ["tool_plan", "decision", "decision"])
            self.assertFalse(any(event["event_type"] in {"DECISION_READY", "DRAFTED"}
                                 for event in result["workspace"]["events"]))


if __name__ == "__main__":
    unittest.main()
