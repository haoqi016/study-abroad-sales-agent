"""Synthetic-only checks for the optional public Gold exercise."""

import unittest

from sales_agent.v2_offline_api import OfflineOnlyError, OfflineWorkspaceApi
from web.bridge_server import scripted_turn


class PublicGoldRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.api = OfflineWorkspaceApi(enable_gold_homework=True)
        self.addCleanup(self.api.close)
        student = self.api.createStudent({"demo_only": True, "display_name": "DEMO Student",
                                          "sales": {"contact_permission": "ALLOWED"}})
        self.student_id = student["student_id"]

    def inbound(self, raw="DEMO: What is included in your support?", strategy_code="MAKE_OFFER"):
        workspace = self.api.recordInbound(self.student_id, {"demo_only": True, "raw_text": raw})
        event = next(event for event in reversed(workspace["events"])
                     if event["event_type"] == "INBOUND_RECEIVED")
        script = scripted_turn(event["event_id"], raw)
        script["decision"]["selected_strategy"]["strategy_code"] = strategy_code
        self.api.queue_script(self.student_id, script)
        return event

    def test_pause_resume_keeps_first_decision_and_requires_review(self):
        self.inbound()
        first = self.api.requestDecision(self.student_id)
        self.assertEqual(first["pipeline"]["status"], "ASK_HUMAN")
        self.assertEqual(first["workspace"]["drafts"], [])
        pending = first["workspace"]["gold_pending"]
        self.assertEqual(pending["payload"]["stage"], "D1")
        self.assertNotIn("first_decision", pending["payload"]["output"])
        resolution = {"demo_only": True, "pause_event_id": pending["event_id"],
                      "action": "SELECT_STRATEGY", "strategy_choice": "FIRST",
                      "reason": "Keep the source-backed first decision."}
        resumed = self.api.resumeGoldPause(self.student_id, resolution)
        self.assertEqual(resumed["pipeline"]["status"], "REVIEW_REQUIRED")
        self.assertIsNone(resumed["workspace"]["gold_pending"])
        self.assertEqual(len(resumed["workspace"]["drafts"]), 1)
        self.assertEqual([event["payload"]["stage"] for event in resumed["workspace"]["gold_trace"]
                          if event["event_type"] == "GOLD_STAGE_RECORDED"],
                         ["D0", "G0", "D1", "G1", "C0", "C0_STYLE_CHECK"])
        self.assertEqual(len([event for event in resumed["workspace"]["gold_trace"]
                              if event["event_type"] == "GOLD_HUMAN_RESOLVED"]), 1)
        self.assertEqual(len(self.api._store.list_events(self.student_id, event_type="GOLD_HUMAN_RESOLVED")), 1)
        with self.assertRaisesRegex(OfflineOnlyError, "gold_pause_is_not_current"):
            self.api.resumeGoldPause(self.student_id, resolution)
        history = self.api._store.customer_visible_history(self.student_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "student")
        self.assertEqual(history[0]["text"], "DEMO: What is included in your support?")

    def test_new_inbound_makes_pause_stale(self):
        self.inbound()
        first = self.api.requestDecision(self.student_id)
        pending = first["workspace"]["gold_pending"]
        self.api.recordInbound(self.student_id, {"demo_only": True,
                                                 "raw_text": "DEMO: New question after the pause."})
        with self.assertRaisesRegex(OfflineOnlyError, "gold_pause_is_not_current|gold_pause_is_stale"):
            self.api.resumeGoldPause(self.student_id, {"demo_only": True,
                "pause_event_id": pending["event_id"], "action": "SELECT_STRATEGY",
                "strategy_choice": "FIRST", "reason": "Keep first."})

    def test_default_is_disabled_and_unsupported_choices_fail_closed(self):
        with OfflineWorkspaceApi() as default:
            self.assertFalse(default.getWorkspace(default.createStudent({
                "demo_only": True, "display_name": "DEMO Student",
                "sales": {"contact_permission": "ALLOWED"}})["student_id"])["gold_homework_enabled"])
        self.inbound()
        pending = self.api.requestDecision(self.student_id)["workspace"]["gold_pending"]
        for choice in ("FINAL", "EDIT"):
            with self.assertRaisesRegex(OfflineOnlyError, "unsupported_public_gold_resolution"):
                self.api.resumeGoldPause(self.student_id, {"demo_only": True,
                    "pause_event_id": pending["event_id"], "action": "SELECT_STRATEGY",
                    "strategy_choice": choice, "reason": "This choice is unavailable."})

    def test_style_issue_requires_human_review_when_rewrite_unavailable(self):
        raw = "DEMO: Tell me what happens next."
        event = self.api.recordInbound(self.student_id, {"demo_only": True, "raw_text": raw})
        inbound = next(item for item in reversed(event["events"])
                       if item["event_type"] == "INBOUND_RECEIVED")
        long_reply = "We can discuss the next step in plain language. " * 5
        self.api.queue_script(self.student_id, scripted_turn(inbound["event_id"], raw,
                                                       text=long_reply))
        result = self.api.requestDecision(self.student_id)
        self.assertEqual(result["pipeline"]["status"], "REVISE_REQUIRED")
        self.assertIn("STYLE_HUMAN_REVIEW_REQUIRED", result["pipeline"]["reason_codes"])
        self.assertEqual([event["payload"]["stage"] for event in result["workspace"]["gold_trace"][-3:]],
                         ["C0", "C0_STYLE_CHECK", "C0_REPAIR"])

    def test_one_style_rewrite_still_passes_content_gate(self):
        raw = "DEMO: Tell me what happens next."
        workspace = self.api.recordInbound(self.student_id, {"demo_only": True, "raw_text": raw})
        inbound = next(item for item in reversed(workspace["events"])
                       if item["event_type"] == "INBOUND_RECEIVED")
        long_reply = "We can discuss the next step in plain language. " * 5
        script = scripted_turn(inbound["event_id"], raw, text=long_reply)
        script["conversation_rewrite"] = {"messages": [{"type": "text",
                                                         "content": "We can discuss your next step. Which part would help most?"}],
                                         "style_transformations": []}
        self.api.queue_script(self.student_id, script)
        result = self.api.requestDecision(self.student_id)
        self.assertEqual(result["pipeline"]["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["workspace"]["drafts"][0]["text"],
                         "We can discuss your next step. Which part would help most?")
        self.assertEqual([event["payload"]["stage"] for event in result["workspace"]["gold_trace"][-4:]],
                         ["C0", "C0_STYLE_CHECK", "C0_REPAIR", "C0_STYLE_RECHECK"])

    def test_failed_resume_is_explicit_handoff_without_a_draft(self):
        self.inbound()
        first = self.api.requestDecision(self.student_id)
        pause = first["workspace"]["gold_pending"]
        resolution = {"demo_only": True, "pause_event_id": pause["event_id"],
                      "action": "SELECT_STRATEGY", "strategy_choice": "FIRST",
                      "reason": "Keep the recorded decision."}
        self.api._scripts[self.student_id][0].pop("conversation")
        failed = self.api.resumeGoldPause(self.student_id, resolution)
        self.assertEqual(failed["pipeline"]["status"], "HANDOFF")
        self.assertEqual(failed["workspace"]["gold_pending_status"], "RESUME_HANDOFF")
        self.assertEqual(failed["workspace"]["drafts"], [])
        with self.assertRaisesRegex(OfflineOnlyError, "gold_resume_requires_human_recovery"):
            self.api.resumeGoldPause(self.student_id, resolution)
        self.assertEqual(len(self.api._store.list_events(self.student_id, event_type="GOLD_HUMAN_RESOLVED")), 1)
        self.assertEqual(self.api.getWorkspace(self.student_id)["drafts"], [])
        self.api.recordInbound(self.student_id, {"demo_only": True,
                                                 "raw_text": "DEMO: A new question for a reviewed turn."})
        self.assertIsNone(self.api.getWorkspace(self.student_id)["gold_pending"])


if __name__ == "__main__":
    unittest.main()
