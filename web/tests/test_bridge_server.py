"""Loopback bridge contract tests using only synthetic records."""

import http.client
import json
import threading
import unittest
from http.server import HTTPServer
from unittest.mock import Mock

from sales_agent.v2_offline_api import OfflineOnlyError, OfflineWorkspaceApi
from sales_agent.v2_pipeline import DeterministicOfflineProvider, ProviderError
from web.bridge_server import dispatch, make_handler, scripted_turn


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.api = OfflineWorkspaceApi()

    def tearDown(self):
        self.api.close()

    def create(self):
        return dispatch(self.api, "POST", "/api/students", {
            "demo_only": True, "display_name": "DEMO Student",
            "sales": {"contact_permission": "ALLOWED"}})

    def test_sales_stage_is_validated_on_create_and_update(self):
        for stage in ("CLOSED", "", None, 42):
            with self.subTest(stage=stage), self.assertRaisesRegex(
                OfflineOnlyError, "invalid_sales_stage"
            ):
                dispatch(self.api, "POST", "/api/students", {
                    "demo_only": True, "display_name": "DEMO Student",
                    "sales": {"stage": stage, "contact_permission": "ALLOWED"},
                })
        student = self.create()
        sid = student["student_id"]
        self.assertEqual(student["sales"]["stage"], "NEW")
        with self.assertRaisesRegex(OfflineOnlyError, "invalid_sales_stage"):
            dispatch(self.api, "PUT", f"/api/students/{sid}", {
                "demo_only": True, "expected_revision": student["revision"],
                "sales": {"stage": "CLOSED"},
            })
        self.assertEqual(self.api.getWorkspace(sid)["sales"]["stage"], "NEW")
        updated = dispatch(self.api, "PUT", f"/api/students/{sid}", {
            "demo_only": True, "expected_revision": student["revision"],
            "sales": {"stage": "QUALIFIED"},
        })
        self.assertEqual(updated["sales"]["stage"], "QUALIFIED")

    def test_contact_permission_blocks_exposing_an_approved_draft(self):
        student = self.create()
        sid = student["student_id"]
        path = f"/api/students/{sid}"
        dispatch(self.api, "POST", path + "/inbound", {
            "demo_only": True, "raw_text": "DEMO: I need help with the application.",
        })
        dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        approved = dispatch(self.api, "POST", path + "/approve", {"demo_only": True})
        self.assertEqual(approved["draft_status"], "APPROVED")
        self.assertEqual(approved["approved_draft_id"], approved["drafts"][-1]["draft_id"])

        limited = dispatch(self.api, "PUT", path, {
            "demo_only": True, "expected_revision": approved["revision"],
            "sales": {"contact_permission": "LIMITED"},
        })
        self.assertEqual(limited["draft_status"], "APPROVED")
        self.assertIsNone(limited["approved_draft_id"])
        self.assertEqual(limited["sent"], [])
        with self.assertRaisesRegex(OfflineOnlyError, "explicit_contact_permission_required"):
            dispatch(self.api, "POST", path + "/actual-sent", {
                "demo_only": True, "confirmed_external_send": True,
                "actual_sent_text": "DEMO: sent externally",
            })

    def test_routing_and_synthetic_boundary(self):
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", "/api/students", {"display_name": "DEMO Student"})
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", "/api/students", {
                "demo_only": True, "display_name": "DEMO Student", "email": "no@example.test"})
        student = self.create()
        sid = student["student_id"]
        self.assertEqual(dispatch(self.api, "GET", "/api/students")[0]["student_id"], sid)
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", f"/api/students/{sid}/inbound", {"raw_text": "hello"})
        with self.assertRaises(KeyError):
            dispatch(self.api, "POST", f"/api/students/{sid}/send", {"demo_only": True})
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", f"/api/students/{sid}/approve", {
                "demo_only": True, "passed": True})

    def test_offer_review_route_preserves_synthetic_contract_and_each_action(self):
        sid = self.create()["student_id"]
        route = f"/api/students/{sid}/offer-review"
        bridge_api = Mock()
        bridge_api.reviewCustomOffer.return_value = {"has_pending_offer": False}
        with self.assertRaises(OfflineOnlyError):
            dispatch(bridge_api, "POST", route, {"action": "REJECT"})
        for action in ("APPROVE", "REQUEST_CHANGES", "REJECT"):
            body = {"demo_only": True, "action": action, "offer_id": "demo-offer"}
            self.assertEqual(dispatch(bridge_api, "POST", route, body),
                             {"has_pending_offer": False})
            bridge_api.reviewCustomOffer.assert_called_with(sid, body)

    def test_progress_label_route_preserves_prediction_and_requires_synthetic_assertion(self):
        sid = self.create()["student_id"]
        path = f"/api/students/{sid}"
        dispatch(self.api, "POST", path + "/inbound", {"demo_only": True, "raw_text": "DEMO: I want to learn about application services"})
        dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        dispatch(self.api, "POST", path + "/approve", {"demo_only": True})
        dispatch(self.api, "POST", path + "/actual-sent", {
            "demo_only": True, "confirmed_external_send": True, "actual_sent_text": "DEMO: sent externally"})
        reply = dispatch(self.api, "POST", path + "/inbound", {
            "demo_only": True, "raw_text": "DEMO: it is a scope question, not a budget question"})
        assessment = reply["progress_assessments"][-1]
        route = path + "/progress-label"
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", route, {"assessment_event_id": assessment["event_id"],
                "human_label": "OBJECTION_CLARIFIED", "reason": "The student clarified an objection"})
        labeled = dispatch(self.api, "POST", route, {
            "demo_only": True, "assessment_event_id": assessment["event_id"],
            "human_label": "OBJECTION_CLARIFIED", "reason": "The student clarified an objection"})
        self.assertEqual(labeled["progress_assessments"][-1]["payload"], assessment["payload"])
        self.assertEqual(labeled["effective_progress_labels"][-1]["human_label"], "OBJECTION_CLARIFIED")
        self.assertEqual(labeled["progress_labels"][-1]["payload"]["reason"], "The student clarified an objection")
        self.assertEqual([event for event in labeled["events"] if event["event_type"] == "INBOUND_RECEIVED"][-1]
                         ["payload"]["raw_text"], "DEMO: it is a scope question, not a budget question")

    def test_full_review_and_recorded_send_remain_distinct(self):
        sid = self.create()["student_id"]
        path = f"/api/students/{sid}"
        inbound = dispatch(self.api, "POST", path + "/inbound", {
            "demo_only": True, "raw_text": "DEMO: I want to learn about application services"})
        inbound_id = inbound["events"][-1]["event_id"]
        self.assertEqual(inbound["events"][-1]["payload"]["raw_text"], "DEMO: I want to learn about application services")
        decision = dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(decision["pipeline"]["status"], "REVIEW_REQUIRED")
        w = decision["workspace"]
        self.assertEqual(w["decision"]["input_event_ids"], [inbound_id])
        self.assertEqual(w["draft_status"], "DRAFTED")
        self.assertEqual(w["sent"], [])
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", path + "/actual-sent", {
                "demo_only": True, "confirmed_external_send": True, "actual_sent_text": "Synthetic sent text"})
        approved = dispatch(self.api, "POST", path + "/approve", {"demo_only": True})
        self.assertEqual(approved["draft_status"], "APPROVED")
        self.assertEqual(approved["sent"], [])
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", path + "/actual-sent", {
                "demo_only": True, "actual_sent_text": "Synthetic sent text"})
        sent = dispatch(self.api, "POST", path + "/actual-sent", {
            "demo_only": True, "confirmed_external_send": True, "actual_sent_text": "DEMO: exact external sent text"})
        self.assertEqual(sent["sent"][-1]["payload"]["actual_sent_text"], "DEMO: exact external sent text")
        self.assertEqual(sent["sent"][-1]["payload"]["diff_from_approved"], "CHANGED_BY_HUMAN")
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", path + "/commercial-outcome", {
                "outcome_type": "DEPOSIT_RECEIVED", "amount": 100})
        outcome = dispatch(self.api, "POST", path + "/commercial-outcome", {
            "demo_only": True, "outcome_type": "DEPOSIT_RECEIVED", "amount": 100})
        self.assertEqual(outcome["commercial_ledger"]["received_amount"], 100)
        self.assertEqual([event for event in outcome["events"]
                          if event["event_type"] == "COMMERCIAL_OUTCOME"][-1]
                         ["payload"]["verification_status"], "SELF_REPORTED")
        next_inbound = dispatch(self.api, "POST", path + "/inbound", {
            "demo_only": True, "raw_text": "DEMO: I have another question"})
        latest_inbound = [event for event in next_inbound["events"]
                          if event["event_type"] == "INBOUND_RECEIVED"][-1]
        self.assertEqual(latest_inbound["payload"]["raw_text"], "DEMO: I have another question")
        self.assertEqual(next_inbound["approved_draft_id"], None)
        second = dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(second["pipeline"]["status"], "REVIEW_REQUIRED")
        self.assertEqual(second["workspace"]["decision"]["input_event_ids"],
                         [latest_inbound["event_id"]])

    def test_rejected_decision_does_not_leave_stale_script(self):
        sid = self.create()["student_id"]
        path = f"/api/students/{sid}"
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(self.api._scripts.get(sid, []), [])
        dispatch(self.api, "POST", path + "/inbound", {"demo_only": True, "raw_text": "Synthetic message"})
        dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        with self.assertRaises(OfflineOnlyError):
            dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(self.api._scripts.get(sid, []), [])

    def test_review_reflection_and_revision_remain_internal(self):
        sid = self.create()["student_id"]
        path = f"/api/students/{sid}"
        dispatch(self.api, "POST", path + "/inbound", {"demo_only": True, "raw_text": "DEMO: hello"})
        first = dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(first["pipeline"]["status"], "REVIEW_REQUIRED")
        reviewed = dispatch(self.api, "POST", path + "/review", {
            "demo_only": True, "feedback_type": "NATURALNESS", "comment": "DEMO: make the tone more natural"})
        self.assertEqual(reviewed["draft_status"], "REVIEW_CHANGES_REQUESTED")
        revised = dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(revised["pipeline"]["status"], "REVIEW_REQUIRED", revised["pipeline"])
        self.assertEqual(len(revised["workspace"]["drafts"]), 2)
        reflections = [e for e in revised["workspace"]["events"] if e["event_type"] == "REVIEW_REFLECTION"]
        self.assertEqual(reflections[-1]["payload"]["evidence"], ["DEMO: make the tone more natural"])
        self.assertEqual(revised["workspace"]["sent"], [])

    def test_strategy_rejection_starts_new_objective_and_keeps_student_history_clean(self):
        sid = self.create()["student_id"]
        path = f"/api/students/{sid}"
        dispatch(self.api, "POST", path + "/inbound", {
            "demo_only": True, "raw_text": "DEMO: my maximum is CNY 15,000"})
        first = dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(first["pipeline"]["status"], "REVIEW_REQUIRED")
        prior_objective = first["workspace"]["decision"]["current_objective"]["objective_id"]
        dispatch(self.api, "POST", path + "/review", {
            "demo_only": True, "feedback_type": "STRATEGY_ERROR", "comment": "DEMO: address the budget ceiling first"})
        revised = dispatch(self.api, "POST", path + "/decision", {"demo_only": True})
        self.assertEqual(revised["pipeline"]["status"], "REVIEW_REQUIRED", revised["pipeline"])
        self.assertNotEqual(revised["workspace"]["decision"]["current_objective"]["objective_id"], prior_objective)
        self.assertEqual(revised["workspace"]["decision"]["current_objective"]["previous_objective_id"], prior_objective)
        self.assertEqual(len(revised["workspace"]["drafts"]), 2)
        self.assertEqual(revised["workspace"]["sent"], [])

    def test_http_host_origin_and_body_rejections(self):
        # The adapter and its SQLite connection stay on this test thread.
        server = HTTPServer(("127.0.0.1", 0), make_handler(self.api))
        port = server.server_port
        results = []

        def client(headers, body):
            connection = http.client.HTTPConnection("127.0.0.1", port)
            connection.request("POST", "/api/students", body=json.dumps(body), headers=headers)
            response = connection.getresponse()
            results.append((response.status, json.loads(response.read())))
            connection.close()

        try:
            for headers, body in [
                ({"Host": "evil.example", "Content-Type": "application/json"}, {"demo_only": True}),
                ({"Host": f"127.0.0.1:{port}", "Origin": "https://evil.example",
                  "Content-Type": "application/json"}, {"demo_only": True}),
                ({"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"},
                 {"demo_only": True, "display_name": "DEMO Student"}),
                ({"Host": f"127.0.0.1:{port}", "Content-Type": "application/json",
                  "X-Sales-Demo-Bridge": "1"},
                 {"display_name": "DEMO Student"}),
            ]:
                thread = threading.Thread(target=client, args=(headers, body))
                thread.start()
                server.handle_request()
                thread.join()
            self.assertEqual([status for status, _ in results], [403, 403, 403, 400])
            self.assertEqual(self.api.listStudents(), [])
        finally:
            server.server_close()

    def test_local_model_factory_runs_v2_gates_and_human_review(self):
        provider_calls = []

        def factory():
            inbound = api.getWorkspace(sid)["events"][-1]
            script = scripted_turn(inbound["event_id"], inbound["payload"]["raw_text"],
                                   text="Synthetic model draft: Which part do you need help with?")
            provider = DeterministicOfflineProvider({
                role: [script[role]] for role in ("tool_plan", "decision", "conversation")})
            provider_calls.append(provider)
            return provider

        with OfflineWorkspaceApi(local_provider_factory=factory) as api:
            sid = dispatch(api, "POST", "/api/students", {
                "demo_only": True, "display_name": "DEMO Student",
                "sales": {"contact_permission": "ALLOWED"}})["student_id"]
            path = f"/api/students/{sid}"
            dispatch(api, "POST", path + "/inbound", {
                "demo_only": True, "raw_text": "DEMO: I want to learn about application services"})
            result = dispatch(api, "POST", path + "/decision", {"demo_only": True})
            self.assertEqual(result["pipeline"]["status"], "REVIEW_REQUIRED")
            self.assertEqual([role for role, _ in provider_calls[0].calls],
                             ["tool_plan", "decision", "conversation"])
            self.assertEqual(api._scripts, {})
            self.assertEqual(result["workspace"]["draft_status"], "DRAFTED")
            self.assertEqual(result["workspace"]["sent"], [])
            gates = [e["payload"] for e in result["workspace"]["events"]
                     if e["event_type"] == "GATE_RESULT"]
            self.assertEqual([g["gate_stage"] for g in gates],
                             ["PRE_CONVERSATION", "POST_CONVERSATION"])
            self.assertTrue(all(g["passed"] for g in gates))

    def test_local_model_failure_is_not_replaced_by_script(self):
        class FailingProvider:
            def generate_json(self, _role, _payload):
                raise ProviderError("synthetic_local_failure")

        with OfflineWorkspaceApi(local_provider_factory=FailingProvider) as api:
            sid = dispatch(api, "POST", "/api/students", {
                "demo_only": True, "display_name": "DEMO Student",
                "sales": {"contact_permission": "ALLOWED"}})["student_id"]
            path = f"/api/students/{sid}"
            dispatch(api, "POST", path + "/inbound", {
                "demo_only": True, "raw_text": "DEMO: I want to learn about application services"})
            result = dispatch(api, "POST", path + "/decision", {"demo_only": True})
            self.assertEqual(result["pipeline"]["status"], "HANDOFF")
            self.assertIn("MODEL_PROVIDER_UNAVAILABLE_OR_INVALID", result["pipeline"]["reason_codes"])
            self.assertEqual(result["workspace"]["drafts"], [])
            self.assertEqual(api._scripts, {})

    def test_http_health_reports_server_selected_mode(self):
        with OfflineWorkspaceApi(local_provider_factory=lambda: None) as api:
            server = HTTPServer(("127.0.0.1", 0), make_handler(api))
            port = server.server_port
            received = []

            def client():
                connection = http.client.HTTPConnection("127.0.0.1", port)
                connection.request("GET", "/api/health", headers={"Host": f"127.0.0.1:{port}"})
                response = connection.getresponse()
                received.append((response.status, json.loads(response.read())))
                connection.close()

            try:
                thread = threading.Thread(target=client)
                thread.start()
                server.handle_request()
                thread.join()
                self.assertEqual(received[0][0], 200)
                self.assertEqual(received[0][1]["agent_mode"], "LOCAL_OLLAMA")
            finally:
                server.server_close()


if __name__ == "__main__":
    unittest.main()
