"""Synthetic negative checks for the proposed service security boundary."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sales_agent.v2_security import (
    AccessDenied, AccessPolicy, Action, Device, Employee, IdentityEvidence,
    check_deployment_config,
)
from sales_agent.v2_security.entry import GuardedWorkspaceDispatcher


NOW = datetime(2026, 10, 3, 10, tzinfo=timezone.utc)


class FakeVerifier:
    """Test fixture only; a real backend requires a genuine provider verifier."""

    def __init__(self, identities):
        self.identities = identities

    def verify(self, bearer_token):
        return self.identities[bearer_token]


class SecurityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.identity = IdentityEvidence(
            subject="sales-a", issuer="trusted-issuer", audience="sales-v2",
            session_id="session-a", device_id="phone-a",
            issued_at=NOW - timedelta(minutes=5),
            expires_at=NOW + timedelta(minutes=30),
        )
        self.verifier = FakeVerifier({"signed-a": self.identity})
        self.employees = {
            "sales-a": Employee("sales-a", True, frozenset({"SALESPERSON"}), frozenset({"student-a"})),
        }
        self.devices = {"phone-a": Device("phone-a", "sales-a", True)}
        self.calls = []
        policy = AccessPolicy(
            verifier=self.verifier, employee_lookup=self.employees.get,
            device_lookup=self.devices.get, issuer="trusted-issuer",
            audience="sales-v2", now=lambda: NOW,
        )
        self.dispatcher = GuardedWorkspaceDispatcher(policy, self._backend)

    def _backend(self, actor, method, path, body):
        self.calls.append((actor, method, path, body))
        return {"actor": actor.employee_id, "student": actor.student_id}

    def test_authorized_read_and_write_pass_scoped_actor(self):
        result = self.dispatcher.dispatch("signed-a", "GET", "/api/students/student-a")
        self.assertEqual(result, {"actor": "sales-a", "student": "student-a"})
        self.dispatcher.dispatch("signed-a", "POST", "/api/students/student-a/inbound", {"raw_text": "a fictional question"})
        self.assertEqual([call[0].action for call in self.calls], [Action.READ, Action.WRITE])

    def test_missing_forged_expired_wrong_audience_rejected_before_backend(self):
        cases = [
            ("", None), ("forged", None),
            ("expired", replace(self.identity, expires_at=NOW)),
            ("wrong-audience", replace(self.identity, audience="another-service")),
            ("future", replace(self.identity, issued_at=NOW + timedelta(minutes=1))),
        ]
        for token, evidence in cases:
            if evidence is not None:
                self.verifier.identities[token] = evidence
            with self.subTest(token=token), self.assertRaises(AccessDenied):
                self.dispatcher.dispatch(token, "GET", "/api/students/student-a")
        self.assertEqual(self.calls, [])

    def test_student_id_tampering_and_client_role_assertions_rejected(self):
        for method, path, body in [
            ("GET", "/api/students/student-b", None),
            ("PUT", "/api/students/student-b", {"display_name": "fictional sample"}),
            ("POST", "/api/students/student-a/approve", {"roles": ["PRICING"]}),
            ("POST", "/api/students/student-a/offer", {"confirmed_roles": ["PRODUCT", "DELIVERY", "PRICING"]}),
            ("POST", "/api/students/student-a/inbound", {"student_id": "student-b"}),
            ("POST", "/api/students/student-a/inbound", {"metadata": {"approved_by": "sales-a"}}),
            ("GET", "/api/students/student-a%2Fstudent-b", None),
            ("GET", "/api/students", None),
            ("POST", "/api/students", {"display_name": "fictional sample"}),
        ]:
            with self.subTest(path=path, method=method), self.assertRaises(AccessDenied):
                self.dispatcher.dispatch("signed-a", method, path, body)
        self.assertEqual(self.calls, [])

    def test_employee_and_device_revocation_take_effect_on_next_call(self):
        self.dispatcher.dispatch("signed-a", "GET", "/api/students/student-a")
        self.employees["sales-a"] = replace(self.employees["sales-a"], active=False)
        with self.assertRaises(AccessDenied):
            self.dispatcher.dispatch("signed-a", "GET", "/api/students/student-a")
        self.employees["sales-a"] = replace(self.employees["sales-a"], active=True)
        self.devices["phone-a"] = replace(self.devices["phone-a"], active=False)
        with self.assertRaises(AccessDenied):
            self.dispatcher.dispatch("signed-a", "GET", "/api/students/student-a")
        self.assertEqual(len(self.calls), 1)

    def test_old_session_rejected_after_revocation_cutoff(self):
        self.employees["sales-a"] = replace(
            self.employees["sales-a"], sessions_revoked_before=NOW - timedelta(minutes=2),
        )
        with self.assertRaisesRegex(AccessDenied, "session_revoked"):
            self.dispatcher.dispatch("signed-a", "GET", "/api/students/student-a")
        self.assertEqual(self.calls, [])

    def test_device_owner_and_export_role_are_independent_gates(self):
        self.devices["phone-a"] = replace(self.devices["phone-a"], employee_id="sales-b")
        with self.assertRaises(AccessDenied):
            self.dispatcher.dispatch("signed-a", "GET", "/api/students/student-a")
        self.assertEqual(self.calls, [])
        self.devices["phone-a"] = replace(self.devices["phone-a"], employee_id="sales-a")
        with self.assertRaisesRegex(AccessDenied, "role_denied"):
            self.dispatcher._policy.authorize(
                "signed-a", student_id="student-a", action=Action.EXPORT,
            )
        self.devices["phone-a"] = replace(
            self.devices["phone-a"], sessions_revoked_before=NOW,
        )
        with self.assertRaisesRegex(AccessDenied, "session_revoked"):
            self.dispatcher.dispatch("signed-a", "GET", "/api/students/student-a")
        self.assertEqual(self.calls, [])

    def test_absent_verifier_cannot_authorize(self):
        policy = AccessPolicy(
            verifier=None, employee_lookup=self.employees.get,
            device_lookup=self.devices.get, issuer="trusted-issuer",
            audience="sales-v2", now=lambda: NOW,
        )
        guarded = GuardedWorkspaceDispatcher(policy, self._backend)
        with self.assertRaisesRegex(AccessDenied, "invalid_identity"):
            guarded.dispatch("signed-a", "GET", "/api/students/student-a")
        self.assertEqual(self.calls, [])

    def test_offer_role_is_server_owned_and_version_bound(self):
        approvals = []
        with self.assertRaises(AccessDenied):
            self.dispatcher.approve_offer_role(
                "signed-a", student_id="student-a", offer_id="offer-a", offer_version="v2",
                role=Action.APPROVE_PRICING, record_approval=approvals.append,
            )
        self.assertEqual(approvals, [])
        self.employees["sales-a"] = replace(
            self.employees["sales-a"], roles=frozenset({"SALESPERSON", "PRICING"}),
        )
        for offer_id, version in (("offer-a", ""), ("offer-a", None), ("", "v2")):
            with self.assertRaises(AccessDenied):
                self.dispatcher.approve_offer_role(
                    "signed-a", student_id="student-a", offer_id=offer_id, offer_version=version,
                    role=Action.APPROVE_PRICING, record_approval=approvals.append,
                )
        self.dispatcher.approve_offer_role(
            "signed-a", student_id="student-a", offer_id="offer-a", offer_version="v2",
            role=Action.APPROVE_PRICING, record_approval=approvals.append,
        )
        self.assertEqual([(a.employee_id, a.offer_id, a.offer_version, a.action) for a in approvals],
                         [("sales-a", "offer-a", "v2", Action.APPROVE_PRICING)])


class DeploymentConfigTests(unittest.TestCase):
    def test_missing_and_unsafe_config_fail_closed(self):
        issues = check_deployment_config({"public_base_url": "http://127.0.0.1:8765",
                                          "cloud_run_public_invoker": True,
                                          "lockscreen_content": "FULL_DRAFT"})
        self.assertIn("public_https_origin_required", issues)
        self.assertIn("public_invoker_forbidden", issues)
        self.assertIn("generic_lockscreen_required", issues)
        self.assertIn("data_lifecycle_approval_required", issues)

    def test_complete_manifest_only_passes_static_fields(self):
        config = {
            "public_base_url": "https://sales.example.test", "firebase_project_id": "project-a",
            "firestore_project_id": "project-a", "fcm_project_id": "project-a",
            "cloud_run_auth_required": True, "cloud_run_public_invoker": False,
            "service_account_least_privilege_reviewed": True,
            "lockscreen_content": "GENERIC_ONLY", "backup_restore_drill_passed": True,
            "budget_alert_enabled": True, "retention_export_delete_plan_approved": True,
            "audit_logs_redacted": True,
        }
        self.assertEqual(check_deployment_config(config), ())
        config["firestore_project_id"] = "project-b"
        self.assertIn("service_project_mismatch", check_deployment_config(config))


if __name__ == "__main__":
    unittest.main()
