"""Synthetic data-use and audit tests; no provider or real student data."""

from __future__ import annotations

import unittest

from sales_agent.v2_security.data import (
    Classification, DataBoundaryError, DataUsePolicy, Purpose,
    audit_reason_for, prepare_model_payload, redacted_audit_metadata,
)


PRIVATE_APPROVED = DataUsePolicy(
    Purpose.DRAFT_ASSISTANCE, Classification.STUDENT_PRIVATE, True,
)
SYNTHETIC = DataUsePolicy(Purpose.SYNTHETIC_DEMO, Classification.SYNTHETIC, False)


def safe_candidate():
    return {
        "student_message": "A fictional student asks about service scope and fees.",
        "education": {"undergraduate_tier": "UNKNOWN", "score_band": "80-84",
                      "current_year": "YEAR_4"},
        "targets": {"countries": ["SG"], "programs_or_majors": ["business"]},
        "sales": {"stage": "PRICE_OBJECTION", "current_objection": "I want to understand the service scope first."},
    }


class DataBoundaryTests(unittest.TestCase):
    def test_minimal_private_projection_is_new_and_allowlisted(self):
        candidate = safe_candidate()
        payload = prepare_model_payload(PRIVATE_APPROVED, candidate)
        self.assertEqual(payload, candidate)
        payload["targets"]["countries"].append("US")
        self.assertEqual(candidate["targets"]["countries"], ["SG"])
        self.assertNotIn("student_id", payload)

    def test_unapproved_transfer_and_cross_purpose_use_fail_closed(self):
        policies = [
            DataUsePolicy(Purpose.DRAFT_ASSISTANCE, Classification.STUDENT_PRIVATE, False),
            DataUsePolicy(Purpose.SHARED_KNOWLEDGE, Classification.STUDENT_PRIVATE, True),
            DataUsePolicy(Purpose.TEST_EVAL, Classification.STUDENT_PRIVATE, True),
            DataUsePolicy(Purpose.DRAFT_ASSISTANCE, Classification.SYNTHETIC, True),
            DataUsePolicy("DRAFT_ASSISTANCE", Classification.STUDENT_PRIVATE, True),
        ]
        for policy in policies:
            with self.subTest(policy=policy), self.assertRaises(DataBoundaryError):
                prepare_model_payload(policy, safe_candidate())
        self.assertEqual(prepare_model_payload(SYNTHETIC, safe_candidate()), safe_candidate())

    def test_record_identifiers_and_unknown_fields_do_not_cross(self):
        variations = [
            {"student_id": "opaque-123"},
            {"display_name": "Fictional Student"},
            {"source": {"platform_handle": "handle"}},
            {"education": {"undergraduate_university_raw": "Fictional University"}},
            {"targets": {"universities": ["NTU"]}},
            {"sales": {"contact_permission": "ALLOWED"}},
            {"metadata": {"classification": "SYNTHETIC"}},
        ]
        for extra in variations:
            candidate = safe_candidate()
            for key, value in extra.items():
                if key in candidate and isinstance(value, dict):
                    candidate[key].update(value)
                else:
                    candidate[key] = value
            with self.subTest(extra=extra), self.assertRaisesRegex(
                DataBoundaryError, "unsupported_model_field"
            ):
                prepare_model_payload(PRIVATE_APPROVED, candidate)

    def test_obvious_direct_identifiers_in_free_text_rejected(self):
        snippets = [
            "Please email student@example.test", "Phone 13800138000",
            "Call +65 91234567", "Passport 11010519491231002X",
            "WeChat ID: student_handle", "\u5fae\u4fe1\u53f7: student_handle",
            "See https://example.test/profile",
        ]
        for text in snippets:
            with self.subTest(text=text), self.assertRaisesRegex(
                DataBoundaryError, "direct_identifier_detected"
            ):
                prepare_model_payload(PRIVATE_APPROVED, {"student_message": text})
            candidate = safe_candidate()
            candidate["sales"]["current_objection"] = text
            with self.subTest(text=text, field="objection"), self.assertRaisesRegex(
                DataBoundaryError, "direct_identifier_detected"
            ):
                prepare_model_payload(PRIVATE_APPROVED, candidate)

    def test_wrong_types_length_and_unknown_enums_rejected(self):
        candidates = [
            {"student_message": None},
            {"student_message": " "},
            {"student_message": "x" * 4001},
            {"student_message": "fictional sample", "education": {"score_band": "exact-87.3"}},
            {"student_message": "fictional sample", "targets": {"countries": ["x"] * 6}},
            {"student_message": "fictional sample", "targets": {"countries": ["student_handle"]}},
            {"student_message": "fictional sample", "sales": {"stage": "CLOSED"}},
        ]
        for candidate in candidates:
            with self.subTest(candidate=str(candidate)[:50]), self.assertRaises(DataBoundaryError):
                prepare_model_payload(PRIVATE_APPROVED, candidate)

    def test_redacted_audit_contains_only_fixed_codes_and_field_names(self):
        candidate = safe_candidate()
        candidate["student_message"] = "A fictional prospect asks about payment options."
        payload = prepare_model_payload(PRIVATE_APPROVED, candidate)
        audit = redacted_audit_metadata(
            PRIVATE_APPROVED, outcome="ALLOWED", reason_code="ok", projected_payload=payload,
        )
        self.assertEqual(audit["event_type"], "MODEL_DATA_BOUNDARY")
        self.assertIn("student_message", audit["projected_field_paths"])
        self.assertNotIn("A fictional prospect", str(audit))
        self.assertNotIn("business", str(audit))
        denied = redacted_audit_metadata(
            PRIVATE_APPROVED, outcome="DENIED", reason_code="direct_identifier_detected",
        )
        self.assertEqual(denied["projected_field_paths"], [])

    def test_error_audit_maps_field_validation_without_input_text(self):
        try:
            prepare_model_payload(PRIVATE_APPROVED, {"student_message": ""})
        except DataBoundaryError as error:
            reason = audit_reason_for(error)
        else:
            self.fail("expected rejection")
        self.assertEqual(reason, "invalid_field")
        audit = redacted_audit_metadata(PRIVATE_APPROVED, outcome="DENIED", reason_code=reason)
        self.assertNotIn("student_message", str(audit))
        for bad in [
            {"outcome": "DENIED", "reason_code": "raw phone 13800138000"},
            {"outcome": "ALLOWED", "reason_code": "direct_identifier_detected"},
            {"outcome": "ALLOWED", "reason_code": "ok"},
            {"outcome": "DENIED", "reason_code": "ok"},
        ]:
            with self.subTest(bad=bad), self.assertRaises(DataBoundaryError):
                redacted_audit_metadata(PRIVATE_APPROVED, **bad)

    def test_audit_rejects_arbitrary_field_names(self):
        with self.assertRaisesRegex(DataBoundaryError, "unsupported_audit_field"):
            redacted_audit_metadata(
                PRIVATE_APPROVED, outcome="ALLOWED", reason_code="ok",
                projected_payload={"display_name": "Fictional Name"},
            )


if __name__ == "__main__":
    unittest.main()
