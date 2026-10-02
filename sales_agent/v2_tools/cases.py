"""Approved Matching-source boundary and sales-safe historical-case projection."""

from __future__ import annotations

import math
import re
import hmac
import hashlib
from copy import deepcopy
from typing import Any

from .common import approved_source, call_with_timeout, envelope, safe_public_text, timestamp

SOURCE_TYPE = "anonymized_historical_case"
LIMITATIONS = ["NOT_ADMISSION_PROBABILITY", "NOT_CAUSAL_EVIDENCE"]
MODES = {"SIMILAR_BACKGROUND", "TARGET_OUTCOME"}
DIMENSIONS = {"undergraduate_tier", "score_band", "major_family", "target"}
PROFILE_KEYS = {
    "undergraduate_university_raw", "undergraduate_major_raw", "average_score",
    "target_country", "target_university", "target_program_or_major", "fact_status",
}
TIERS = {"985", "211", "DOUBLE_FIRST_CLASS", "OTHER", "UNKNOWN"}
SCORE_BANDS = {"75-79", "80-84", "85-89", "90+", "UNKNOWN"}
OUTCOMES = {"OFFER", "REJECT", "WAITLIST", "INTERVIEW", "UNKNOWN"}
DIFFERENCES = {
    "INTERNSHIP_DIFFERENT": "该案例的实习经历与当前学生不同",
    "LANGUAGE_DIFFERENT": "该案例的语言成绩与当前学生不同",
    "MAJOR_DIFFERENT": "该案例的本科专业背景与当前学生不同",
    "SCORE_BAND_DIFFERENT": "该案例的均分区间与当前学生不同",
    "OTHER_BACKGROUND_UNKNOWN": "还有尚未核实的背景差异",
}
MAJOR_FAMILY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,49}$")
_AUDIT_REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,99}$")
_SERVICE_EVIDENCE_KINDS = {"SIGNED_SERVICE_AGREEMENT", "INTERNAL_SERVICE_DELIVERY_RECORD", "SERVICE_INVOICE"}

_PROFILE_PATHS = {
    "undergraduate_university_raw": ("education.undergraduate_university_raw", "education.university_raw"),
    "undergraduate_major_raw": ("education.undergraduate_major_raw", "education.major_raw"),
    "average_score": ("education.average_score",),
    "target_country": ("education.target_country", "targets.countries"),
    "target_university": ("education.target_university", "targets.universities"),
    "target_program_or_major": ("education.target_program_or_major", "targets.programs_or_majors"),
}
_COUNTRY_WORDS = {
    "SG": ("新加坡", "Singapore"), "HK": ("香港", "Hong Kong"),
    "UK": ("英国", "United Kingdom"), "AU": ("澳大利亚", "澳洲", "Australia"),
}


def _same_fact(actual: Any, proposed: Any, key: str) -> bool:
    if isinstance(actual, list):
        return any(_same_fact(item, proposed, key) for item in actual)
    if type(proposed) in {int, float}:
        return type(actual) in {int, float} and actual == proposed
    if key == "target_country" and isinstance(actual, str) and isinstance(proposed, str):
        return actual.strip().casefold() in {
            item.casefold() for item in (proposed, *_COUNTRY_WORDS.get(proposed, ()))
        }
    return isinstance(actual, str) and isinstance(proposed, str) and actual.strip().casefold() == proposed.strip().casefold()


def _stated_in_current_message(key: str, value: Any, raw_text: str) -> bool:
    if not isinstance(raw_text, str):
        return False
    if key == "average_score" and type(value) in {int, float}:
        candidates = (str(value),)
        prefix = r"(?:均分|平均分|成绩|分数|GPA|score)\s*(?:是|为|有)?\s*"
    elif isinstance(value, str) and value.strip():
        candidates = (value, *_COUNTRY_WORDS.get(value, ())) if key == "target_country" else (value,)
        prefix = {
            "undergraduate_university_raw": r"(?:本科|毕业于|就读于)\s*(?:是|在)?\s*",
            "undergraduate_major_raw": r"(?:本科专业|专业是|就读专业)\s*",
            "target_country": r"(?:想申请|申请|目标(?:是|为)?|计划去|考虑申请)\s*",
            "target_university": r"(?:想申请|申请|目标(?:是|为)?|计划申请|考虑申请)\s*",
            "target_program_or_major": r"(?:想申请|申请|目标(?:是|为)?|计划申请|考虑申请)\s*",
        }[key]
    else:
        return False
    # Only an unambiguous first-person affirmative sentence is enough for
    # an unconfirmed fact. Anything attributed to another person or negated
    # falls back to human-confirmed CRM input.
    for sentence in re.split(r"[。！？；;\n]", raw_text):
        if not re.search(r"(?:我|本人)", sentence) or re.search(
                r"(?:不是|并非|没有|不想|不申|不考虑|朋友|同学|别人|客户|他|她)", sentence):
            continue
        for candidate in candidates:
            token = re.escape(candidate.strip())
            boundary = r"(?![\d.])" if key == "average_score" else r"(?![A-Za-z0-9])"
            if re.search(prefix + token + boundary, sentence, re.IGNORECASE):
                return True
    return False


def bind_case_request(request: Any, snapshot: dict | None, raw_text: str) -> dict | None:
    """Bind each proposed fact to confirmed CRM state or this inbound message.

    The model's fact_status is never authority. A failed binding must not reach
    the case source, including when only one field of a larger query is invented.
    """
    if not _valid_request(request):
        return None
    student = (snapshot or {}).get("snapshot", {})
    provenance = (snapshot or {}).get("field_provenance", {})
    if not isinstance(student, dict) or not isinstance(provenance, dict):
        return None
    used_message = False
    for key, value in request["student_profile"].items():
        if key == "fact_status" or value is None:
            continue
        confirmed = False
        for path in _PROFILE_PATHS[key]:
            section, field = path.split(".", 1)
            meta = provenance.get(path)
            record_section = student.get(section)
            if (isinstance(meta, dict) and meta.get("confirmed") is True
                    and meta.get("source_type") == "SALESPERSON_INPUT"
                    and isinstance(record_section, dict)
                    and _same_fact(record_section.get(field), value, key)):
                confirmed = True
                break
        if confirmed:
            continue
        if _stated_in_current_message(key, value, raw_text):
            used_message = True
            continue
        return None
    bound = deepcopy(request)
    bound["student_profile"]["fact_status"] = "CUSTOMER_STATED" if used_message else "CRM_CONFIRMED"
    return bound


def unverified_case_query_response() -> dict:
    return envelope("POLICY_BLOCKED", SOURCE_TYPE, limitations=LIMITATIONS,
                    error_code="CASE_QUERY_FACT_UNVERIFIED")


def _valid_request(request: Any) -> bool:
    if not isinstance(request, dict) or not {"contract_version", "retrieval_mode", "student_profile", "comparison_dimensions"} <= set(request) or not set(request) <= {"contract_version", "retrieval_mode", "student_profile", "comparison_dimensions", "limit"}:
        return False
    profile = request["student_profile"]
    dims = request["comparison_dimensions"]
    if request["contract_version"] != "sales-tools.v1" or request["retrieval_mode"] not in MODES or not isinstance(profile, dict) or set(profile) != PROFILE_KEYS:
        return False
    if not isinstance(dims, list) or not dims or len(dims) != len(set(dims)) or not set(dims) <= DIMENSIONS:
        return False
    limit = request.get("limit", 2)
    if type(limit) is not int or not 1 <= limit <= 3:
        return False
    if profile["fact_status"] not in {"CRM_CONFIRMED", "CUSTOMER_STATED"} or profile["target_country"] not in {None, "SG", "HK", "UK", "AU"}:
        return False
    for key in ("undergraduate_university_raw", "undergraduate_major_raw", "target_university", "target_program_or_major"):
        value = profile[key]
        if value is not None and not safe_public_text(value, max_length=120):
            return False
    score = profile["average_score"]
    if score is not None and (type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 100):
        return False
    if request["retrieval_mode"] == "TARGET_OUTCOME" and not (profile["target_university"] or profile["target_program_or_major"]):
        return False
    return any(value is not None for key, value in profile.items() if key != "fact_status")


def case_evidence_id(source: Any, record_key: str) -> str:
    """Stable opaque provenance within one approved snapshot.

    Authorized audit code can recompute this ID from the approved snapshot's
    record key. Neither that key nor the HMAC secret crosses the model boundary.
    """
    secret = getattr(source, "provenance_secret", None)
    digest = getattr(source, "snapshot_digest", None)
    if not isinstance(secret, bytes) or len(secret) < 32 or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not safe_public_text(record_key, max_length=200):
        raise ValueError("case provenance configuration unavailable")
    payload = f"{source.source_version}|{digest}|{record_key}".encode("utf-8")
    return "ev_" + hmac.new(secret, payload, hashlib.sha256).hexdigest()[:32]


def _reviewed_internal_evidence(review: Any) -> bool:
    """Check audit metadata without ever copying it into the model projection."""
    return (
        isinstance(review, dict)
        and review.get("review_status") == "VERIFIED"
        and isinstance(review.get("reviewed_by"), str)
        and _AUDIT_REF_PATTERN.fullmatch(review["reviewed_by"]) is not None
        and timestamp(review.get("reviewed_at")) is not None
        and isinstance(review.get("evidence_ref"), str)
        and _AUDIT_REF_PATTERN.fullmatch(review["evidence_ref"]) is not None
    )


def _institution_served_source(source: Any) -> bool:
    review = getattr(source, "service_scope_review", None)
    return (
        _reviewed_internal_evidence(review)
        and review.get("scope") == "INSTITUTION_SERVED_ONLY"
        and review.get("source_version") == source.source_version
        and review.get("snapshot_digest") == source.snapshot_digest
    )


def _institution_served_record(raw: dict) -> bool:
    review = raw.get("service_provenance")
    return (
        _reviewed_internal_evidence(review)
        and review.get("service_relationship") == "INSTITUTION_SERVED"
        and review.get("evidence_kind") in _SERVICE_EVIDENCE_KINDS
    )


def _project_record(raw: Any, source: Any) -> dict | None:
    """Allowlist only; never forward raw Matching result, IDs, URLs, score or name."""
    if not isinstance(raw, dict) or not _institution_served_record(raw) or raw.get("approval_status") != "APPROVED" or raw.get("source_status") != "VERIFIED" or raw.get("privacy_review_status") != "APPROVED" or raw.get("public_facts_approved") is not True:
        return None
    n = raw.get("normalization")
    target = raw.get("target")
    comparison = raw.get("comparison")
    if not isinstance(n, dict) or not isinstance(target, dict) or not isinstance(comparison, dict):
        return None
    normalization_source = n.get("normalization_source")
    if n.get("undergraduate_tier") not in TIERS or n.get("score_band") not in SCORE_BANDS or n.get("normalization_status") not in {"RESOLVED", "UNKNOWN"} or not isinstance(n.get("major_family"), str) or not MAJOR_FAMILY_PATTERN.fullmatch(n["major_family"]) or not isinstance(normalization_source, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{1,99}", normalization_source):
        return None
    if target.get("country") not in {"SG", "HK", "UK", "AU"} or not safe_public_text(target.get("university"), max_length=120) or not safe_public_text(target.get("program"), max_length=160):
        return None
    if raw.get("outcome") not in OUTCOMES:
        return None
    matched = comparison.get("matched_dimensions")
    differences = comparison.get("important_differences")
    if not isinstance(matched, list) or not set(matched) <= DIMENSIONS or not isinstance(differences, list) or not set(differences) <= set(DIFFERENCES):
        return None
    try:
        evidence_id = case_evidence_id(source, raw.get("record_key"))
    except (TypeError, ValueError):
        return None
    return {
        "evidence_id": evidence_id,
        "normalization": {key: n[key] for key in ("undergraduate_tier", "score_band", "major_family", "normalization_status", "normalization_source")},
        "comparison": {"matched_dimensions": matched, "important_differences": [DIFFERENCES[x] for x in differences]},
        "target": {key: target[key] for key in ("country", "university", "program")},
        "outcome": raw["outcome"],
    }


def search_anonymized_cases(request: dict, *, source: Any = None) -> dict:
    """Search an approved case snapshot; no implicit fallback to source DB/index."""
    if not _valid_request(request):
        return envelope("INVALID_REQUEST", SOURCE_TYPE, limitations=LIMITATIONS, error_code="INVALID_REQUEST")
    if not approved_source(source) or getattr(source, "index_source_version", None) != source.source_version or not callable(getattr(source, "search", None)) or not isinstance(getattr(source, "provenance_secret", None), bytes) or len(source.provenance_secret) < 32 or not isinstance(getattr(source, "snapshot_digest", None), str) or not re.fullmatch(r"[0-9a-f]{64}", source.snapshot_digest) or not _institution_served_source(source):
        return envelope("UNAVAILABLE", SOURCE_TYPE, limitations=LIMITATIONS, error_code="APPROVED_CASE_SNAPSHOT_UNAVAILABLE")
    try:
        raw = call_with_timeout(
            lambda: source.search(request["retrieval_mode"], request["student_profile"].copy(), request["comparison_dimensions"][:], request.get("limit", 2)),
            2.0,
        )
        if not isinstance(raw, list):
            raise ValueError("invalid case response")
        projected = []
        for item in raw[:20]:
            safe = _project_record(item, source)
            if safe:
                projected.append(safe)
            if len(projected) == request.get("limit", 2):
                break
        return envelope("OK" if projected else "NO_RESULTS", SOURCE_TYPE, source_version=source.source_version, limitations=LIMITATIONS, data=projected)
    except Exception:
        return envelope("UNAVAILABLE", SOURCE_TYPE, source_version=source.source_version, limitations=LIMITATIONS, error_code="CASE_SOURCE_UNAVAILABLE")
