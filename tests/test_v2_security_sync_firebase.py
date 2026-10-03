"""Synthetic SDK-stub tests for Firebase identity and server-owned binding."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import ModuleType
import unittest
from unittest.mock import patch

from sales_agent.v2_security.access import AccessDenied, AccessPolicy, Device, Employee
from sales_agent.v2_security.entry import GuardedWorkspaceDispatcher
from sales_agent.v2_security.firebase_verifier import (
    EnrolledTokenBinding, FirebaseIdTokenVerifier, FirebaseVerificationError,
    token_fingerprint,
)


NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
NOW_SECONDS = int(NOW.timestamp())


class FakeAdminSDK:
    def __init__(self, claims):
        self.claims = claims
        self.calls = []
        self.errors = {}

    def verify_id_token(self, token, *, check_revoked, clock_skew_seconds):
        self.calls.append((token, check_revoked, clock_skew_seconds))
        if token in self.errors:
            raise self.errors[token]
        if token not in self.claims:
            raise ValueError("invalid synthetic token")
        return self.claims[token]


class FirebaseVerifierTests(unittest.TestCase):
    def setUp(self):
        self.token = "synthetic.firebase.id.token"
        self.claims = {
            "uid": "employee-a", "sub": "employee-a",
            "iss": "https://securetoken.google.com/project-a",
            "aud": "project-a", "iat": NOW_SECONDS - 60,
            "exp": NOW_SECONDS + 300, "auth_time": NOW_SECONDS - 120,
        }
        self.sdk = FakeAdminSDK({self.token: self.claims})
        self.digest = token_fingerprint(self.token)
        self.bindings = {
            self.digest: EnrolledTokenBinding(
                token_sha256=self.digest, uid="employee-a",
                session_id="server-session-a", device_id="enrolled-phone-a",
                active=True, expires_at=NOW + timedelta(minutes=5),
            ),
        }
        self.employees = {
            "employee-a": Employee("employee-a", True, frozenset({"SALESPERSON"}),
                                    frozenset({"student-a"})),
        }
        self.devices = {"enrolled-phone-a": Device("enrolled-phone-a", "employee-a", True)}
        self.backend_calls = []
        self.verifier = FirebaseIdTokenVerifier(
            project_id="project-a", sdk_verify_id_token=self.sdk.verify_id_token,
            binding_lookup=self.bindings.get, now=lambda: NOW,
        )
        policy = AccessPolicy(
            verifier=self.verifier, employee_lookup=self.employees.get,
            device_lookup=self.devices.get,
            issuer="https://securetoken.google.com/project-a",
            audience="project-a", now=lambda: NOW,
        )
        self.dispatcher = GuardedWorkspaceDispatcher(policy, self.backend)

    def backend(self, actor, method, path, body):
        self.backend_calls.append((actor, method, path, body))
        return actor

    def read(self, token=None):
        return self.dispatcher.dispatch(token or self.token, "GET", "/api/students/student-a")

    def test_verified_uid_and_enrolled_binding_supply_actor(self):
        actor = self.read()
        self.assertEqual((actor.employee_id, actor.session_id, actor.device_id),
                         ("employee-a", "server-session-a", "enrolled-phone-a"))
        self.assertEqual(self.sdk.calls, [(self.token, True, 0)])
        self.assertEqual(len(self.backend_calls), 1)

    def test_forged_client_claims_and_unknown_token_never_reach_backend(self):
        with self.assertRaises(AccessDenied):
            self.dispatcher.dispatch(
                self.token, "POST", "/api/students/student-a/inbound",
                {"raw_text": "fictional sample", "session_id": "attacker-session", "device_id": "attacker-phone"},
            )
        with self.assertRaises(AccessDenied):
            self.read('{"uid":"employee-a","device_id":"enrolled-phone-a"}')
        with self.assertRaises(AccessDenied):
            self.dispatcher.dispatch(
                self.token, "POST", "/api/students/student-a/inbound",
                {"raw_text": "fictional sample", "session_id": "server-session-a"},
            )
        self.assertEqual(self.backend_calls, [])

    def test_missing_or_mismatched_binding_fails_closed(self):
        mutations = [
            None,
            replace(self.bindings[self.digest], uid="employee-b"),
            replace(self.bindings[self.digest], token_sha256="0" * 64),
            replace(self.bindings[self.digest], active=False),
            replace(self.bindings[self.digest], expires_at=NOW),
        ]
        original = self.bindings[self.digest]
        for binding in mutations:
            if binding is None:
                self.bindings.pop(self.digest, None)
            else:
                self.bindings[self.digest] = binding
            with self.subTest(binding=binding), self.assertRaises(AccessDenied):
                self.read()
            self.assertEqual(self.backend_calls, [])
        self.bindings[self.digest] = original

    def test_wrong_project_uid_or_time_rejected_before_binding_lookup(self):
        original = self.sdk.claims[self.token]
        changes = [
            {"aud": "other-project"},
            {"iss": "https://securetoken.google.com/other-project"},
            {"sub": "employee-b"},
            {"exp": NOW_SECONDS},
            {"iat": NOW_SECONDS + 1},
            {"auth_time": NOW_SECONDS + 1},
        ]
        for change in changes:
            self.sdk.claims[self.token] = {**original, **change}
            with self.subTest(change=change), self.assertRaises(AccessDenied):
                self.read()
        self.sdk.claims[self.token] = original
        self.assertEqual(self.backend_calls, [])

    def test_sdk_revoked_disabled_and_network_failures_are_denied(self):
        for error in (RuntimeError("revoked"), RuntimeError("disabled"), RuntimeError("SDK unavailable")):
            self.sdk.errors[self.token] = error
            with self.subTest(error=str(error)), self.assertRaises(AccessDenied):
                self.read()
        self.assertEqual(self.backend_calls, [])
        self.assertTrue(all(call[1:] == (True, 0) for call in self.sdk.calls))

    def test_server_session_device_and_account_revocations_are_checked_each_request(self):
        self.read()
        self.bindings[self.digest] = replace(self.bindings[self.digest], active=False)
        with self.assertRaises(AccessDenied):
            self.read()
        self.bindings[self.digest] = replace(self.bindings[self.digest], active=True)
        self.devices["enrolled-phone-a"] = replace(self.devices["enrolled-phone-a"], active=False)
        with self.assertRaises(AccessDenied):
            self.read()
        self.devices["enrolled-phone-a"] = replace(self.devices["enrolled-phone-a"], active=True)
        self.employees["employee-a"] = replace(self.employees["employee-a"], active=False)
        with self.assertRaises(AccessDenied):
            self.read()
        self.assertEqual(len(self.backend_calls), 1)

    def test_admin_app_factory_calls_official_shape_without_credentials(self):
        fake_auth = ModuleType("firebase_admin.auth")
        calls = []

        def sdk_verify(token, *, app, check_revoked, clock_skew_seconds):
            calls.append((token, app, check_revoked, clock_skew_seconds))
            return self.claims

        fake_auth.verify_id_token = sdk_verify
        fake_package = ModuleType("firebase_admin")
        fake_package.auth = fake_auth
        app = object()
        with patch.dict(sys.modules, {"firebase_admin": fake_package, "firebase_admin.auth": fake_auth}):
            verifier = FirebaseIdTokenVerifier.for_admin_app(
                project_id="project-a", app=app,
                binding_lookup=self.bindings.get, now=lambda: NOW,
            )
            evidence = verifier.verify(self.token)
        self.assertEqual(evidence.subject, "employee-a")
        self.assertEqual(calls, [(self.token, app, True, 0)])


if __name__ == "__main__":
    unittest.main()
