"""Minimal model payload and redacted audit boundary for a future backend.

This module has no model client or storage access. The service must construct
DataUsePolicy from server-owned classification and approval records, never from
request JSON. Regex checks catch only obvious identifiers, not all personal data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any


class DataBoundaryError(ValueError):
    """A candidate cannot cross this data-use boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Purpose(str, Enum):
    DRAFT_ASSISTANCE = "DRAFT_ASSISTANCE"
    SYNTHETIC_DEMO = "SYNTHETIC_DEMO"
    SHARED_KNOWLEDGE = "SHARED_KNOWLEDGE"
    TEST_EVAL = "TEST_EVAL"


class Classification(str, Enum):
    STUDENT_PRIVATE = "STUDENT_PRIVATE"
    SYNTHETIC = "SYNTHETIC"
    PUBLIC_REFERENCE = "PUBLIC_REFERENCE"


@dataclass(frozen=True)
class DataUsePolicy:
    """Server-owned purpose, provenance and model transfer approval."""

    purpose: Purpose
    classification: Classification
    model_transfer_approved: bool


_ALLOWED_STRUCTURE = {
    "education": frozenset({"undergraduate_tier", "score_band", "current_year"}),
    "targets": frozenset({"countries", "programs_or_majors"}),
    "sales": frozenset({"stage", "current_objection"}),
}
_TIERS = {"985", "211", "DOUBLE_FIRST_CLASS", "OTHER", "UNKNOWN"}
_SCORE_BANDS = {"75-79", "80-84", "85-89", "90+", "UNKNOWN"}
_YEARS = {"YEAR_1", "YEAR_2", "YEAR_3", "YEAR_4", "GRADUATED", "OTHER", "UNKNOWN"}
_STAGES = {
    "NEW", "CONSULTING", "QUALIFIED", "PRICE_OBJECTION", "LIKELY_TO_PAY",
    "WAITING_STUDENT", "WAITING_DECISION_MAKER", "READY_TO_SIGN", "WON",
    "LOST", "DO_NOT_CONTACT",
}
_MODEL_COUNTRIES = {"SG", "HK", "UK", "AU"}
_ENUM_FIELDS = {
    "education.undergraduate_tier": _TIERS,
    "education.score_band": _SCORE_BANDS,
    "education.current_year": _YEARS,
    "sales.stage": _STAGES,
}
_DIRECT_IDENTIFIER_PATTERNS = (
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\+65[-\s]?[3689]\d{7}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    re.compile(r"(?i)\b(?:https?://|www\.)\S+"),
    # Unicode escapes keep this English-only source while matching common
    # Chinese platform-account labels in untrusted free text.
    re.compile(r"(?:\u5fae\u4fe1\u53f7|\u5fae\u4fe1ID|WeChat\s*ID|\u5c0f\u7ea2\u4e66\u53f7|QQ\u53f7)\s*[:\uff1a]?\s*\S+", re.I),
)


def _safe_text(value: Any, *, max_length: int, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise DataBoundaryError("invalid_" + path.replace(".", "_"))
    if any(pattern.search(value) for pattern in _DIRECT_IDENTIFIER_PATTERNS):
        raise DataBoundaryError("direct_identifier_detected")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise DataBoundaryError("control_character_detected")
    return value.strip()


def _safe_string_list(value: Any, *, path: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 5:
        raise DataBoundaryError("invalid_" + path.replace(".", "_"))
    return [_safe_text(item, max_length=80, path=path) for item in value]


def prepare_model_payload(policy: DataUsePolicy, candidate: dict) -> dict:
    """Allowlist and validate a purpose-limited model payload.

    The candidate is a server-assembled small record, not an entire workspace.
    Unknown fields fail closed rather than being silently forwarded or dropped.
    The returned object is newly allocated and contains no direct record IDs.
    """

    if not isinstance(policy, DataUsePolicy) or not isinstance(policy.purpose, Purpose) or not isinstance(policy.classification, Classification):
        raise DataBoundaryError("server_policy_required")
    allowed_pair = (
        policy.purpose is Purpose.DRAFT_ASSISTANCE
        and policy.classification is Classification.STUDENT_PRIVATE
        and policy.model_transfer_approved is True
    ) or (
        policy.purpose is Purpose.SYNTHETIC_DEMO
        and policy.classification is Classification.SYNTHETIC
    )
    if not allowed_pair:
        raise DataBoundaryError("purpose_or_transfer_not_approved")
    if not isinstance(candidate, dict):
        raise DataBoundaryError("candidate_object_required")
    if set(candidate) - (set(_ALLOWED_STRUCTURE) | {"student_message"}):
        raise DataBoundaryError("unsupported_model_field")
    if "student_message" not in candidate:
        raise DataBoundaryError("student_message_required")
    payload: dict[str, Any] = {
        "student_message": _safe_text(candidate["student_message"], max_length=4000, path="student_message"),
    }
    for section, allowed_fields in _ALLOWED_STRUCTURE.items():
        if section not in candidate:
            continue
        source = candidate[section]
        if not isinstance(source, dict) or set(source) - allowed_fields:
            raise DataBoundaryError("unsupported_model_field")
        projection: dict[str, Any] = {}
        for key, value in source.items():
            path = section + "." + key
            if value is None:
                continue
            if path in _ENUM_FIELDS:
                if not isinstance(value, str) or value not in _ENUM_FIELDS[path]:
                    raise DataBoundaryError("invalid_" + path.replace(".", "_"))
                projection[key] = value
            elif path == "targets.countries":
                countries = _safe_string_list(value, path=path)
                if any(country not in _MODEL_COUNTRIES for country in countries):
                    raise DataBoundaryError("invalid_targets_countries")
                projection[key] = countries
            elif path == "targets.programs_or_majors":
                projection[key] = _safe_string_list(value, path=path)
            else:
                projection[key] = _safe_text(value, max_length=500, path=path)
        if projection:
            payload[section] = projection
    return payload


_AUDIT_OUTCOMES = {"ALLOWED", "DENIED"}
_AUDIT_REASONS = {
    "ok", "server_policy_required", "purpose_or_transfer_not_approved",
    "candidate_object_required", "unsupported_model_field", "student_message_required",
    "direct_identifier_detected", "control_character_detected", "invalid_field",
}


def audit_reason_for(error: DataBoundaryError) -> str:
    """Map detailed validation errors to an approved, non-content audit code."""

    if not isinstance(error, DataBoundaryError):
        raise DataBoundaryError("audit_error_type_required")
    if error.code in _AUDIT_REASONS:
        return error.code
    if error.code.startswith("invalid_"):
        return "invalid_field"
    raise DataBoundaryError("audit_code_not_allowed")


def redacted_audit_metadata(
    policy: DataUsePolicy, *, outcome: str, reason_code: str,
    projected_payload: dict | None = None,
) -> dict:
    """Return fixed-vocabulary metadata without values or direct identifiers."""

    if not isinstance(policy, DataUsePolicy) or not isinstance(policy.purpose, Purpose) or not isinstance(policy.classification, Classification):
        raise DataBoundaryError("server_policy_required")
    if outcome not in _AUDIT_OUTCOMES or reason_code not in _AUDIT_REASONS:
        raise DataBoundaryError("audit_code_not_allowed")
    if (outcome == "ALLOWED") != (reason_code == "ok"):
        raise DataBoundaryError("audit_outcome_reason_mismatch")
    if outcome == "ALLOWED" and projected_payload is None:
        raise DataBoundaryError("allowed_payload_required")
    if projected_payload is not None and outcome != "ALLOWED":
        raise DataBoundaryError("denied_payload_forbidden")
    field_paths: list[str] = []
    if projected_payload is not None:
        if not isinstance(projected_payload, dict):
            raise DataBoundaryError("projected_payload_object_required")
        if set(projected_payload) - (set(_ALLOWED_STRUCTURE) | {"student_message"}):
            raise DataBoundaryError("unsupported_audit_field")
        for key, value in projected_payload.items():
            if key == "student_message":
                field_paths.append(key)
            elif isinstance(value, dict) and not set(value) - _ALLOWED_STRUCTURE[key]:
                field_paths.extend(f"{key}.{nested}" for nested in value)
            else:
                raise DataBoundaryError("unsupported_audit_field")
    return {
        "event_type": "MODEL_DATA_BOUNDARY",
        "purpose": policy.purpose.value,
        "classification": policy.classification.value,
        "outcome": outcome,
        "reason_code": reason_code,
        "projected_field_paths": sorted(field_paths),
    }
