"""Official Program evidence from an explicitly approved read-only Sales copy."""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from .common import approved_source, call_with_timeout, envelope, safe_public_text, timestamp

SOURCE_TYPE = "official_program_record"
LIMITATIONS = ["ONLY_SOURCED_FIELDS", "MINIMUM_REQUIREMENT_NOT_ADMISSION_PROBABILITY"]
FACT_INPUTS = {"entry_requirements", "prerequisites", "language", "tuition", "deadline"}
FACT_OUTPUTS = {
    "entry_requirements": "academic_requirement",
    "prerequisites": "prerequisites",
    "language": "language_requirement",
    "tuition": "tuition",
    "deadline": "deadline",
}
REQUIREMENT_TYPES = {"hard_requirement", "recommended", "preferred", "not_specified"}


def program_evidence_id(source: Any, record_key: str) -> str:
    secret = getattr(source, "provenance_secret", None)
    digest = getattr(source, "snapshot_digest", None)
    if not isinstance(secret, bytes) or len(secret) < 32 or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not safe_public_text(record_key, max_length=200):
        raise ValueError("program provenance configuration unavailable")
    payload = f"{source.source_version}|{digest}|{record_key}".encode("utf-8")
    return "program_" + hmac.new(secret, payload, hashlib.sha256).hexdigest()[:32]


def _valid_request(request: Any) -> bool:
    if not isinstance(request, dict) or set(request) != {"contract_version", "query", "applicant_context"}:
        return False
    query = request["query"]
    applicant = request["applicant_context"]
    if request["contract_version"] != "sales-tools.v1" or not isinstance(query, dict) or set(query) != {"country", "university", "program_or_major", "intake_year", "requested_facts"}:
        return False
    if not isinstance(applicant, dict) or set(applicant) != {"score_band", "degree_background"}:
        return False
    if query["country"] not in {None, "SG", "HK", "UK", "AU"} or type(query["intake_year"]) is not int or not 2020 <= query["intake_year"] <= 2100:
        return False
    for key in ("university", "program_or_major"):
        if query[key] is not None and not safe_public_text(query[key], max_length=150):
            return False
    facts = query["requested_facts"]
    if not isinstance(facts, list) or not facts or len(facts) != len(set(facts)) or not set(facts) <= FACT_INPUTS:
        return False
    if applicant["score_band"] not in {None, "75-79", "80-84", "85-89", "90+"} or (applicant["degree_background"] is not None and not safe_public_text(applicant["degree_background"], max_length=120)):
        return False
    return bool(query["university"] or query["program_or_major"] or query["country"])


def _safe_official_url(url: Any, source: Any) -> bool:
    if not isinstance(url, str) or len(url) > 500:
        return False
    parsed = urlsplit(url)
    hosts = getattr(source, "official_hosts", ())
    allowed = isinstance(hosts, (tuple, list)) and any(
        isinstance(host, str) and parsed.hostname and (parsed.hostname == host or parsed.hostname.endswith("." + host))
        for host in hosts
    )
    return bool(parsed.scheme == "https" and allowed and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and source.is_official_url(url))


def _project_fact(raw: Any, source: Any, date_now: datetime) -> tuple[dict, bool, bool]:
    """Return projected fact, stale flag, missing flag."""
    result = {"value": None, "last_checked": None}
    if not isinstance(raw, dict):
        return result, False, True
    value = raw.get("value")
    checked = timestamp(raw.get("last_checked"))
    url = raw.get("official_url")
    if not safe_public_text(value, max_length=500) or checked is None or checked > date_now + timedelta(days=1) or not _safe_official_url(url, source):
        return result, False, True
    result["value"] = value
    result["last_checked"] = checked.isoformat()
    requirement_type = raw.get("requirement_type")
    if requirement_type in REQUIREMENT_TYPES:
        result["requirement_type"] = requirement_type
    elif requirement_type is not None:
        return {"value": None, "last_checked": None}, False, True
    if raw.get("currency") is not None:
        if not safe_public_text(raw["currency"], max_length=10):
            return {"value": None, "last_checked": None}, False, True
        result["currency"] = raw["currency"]
    return result, date_now - checked > timedelta(days=365), False


def _project_record(raw: Any, query: dict, source: Any, date_now: datetime) -> dict | None:
    if not isinstance(raw, dict) or raw.get("approval_status") != "APPROVED" or raw.get("source_status") != "OFFICIAL_VERIFIED":
        return None
    if raw.get("country") not in {"SG", "HK", "UK", "AU"} or type(raw.get("intake_year")) is not int:
        return None
    if not safe_public_text(raw.get("university"), max_length=150) or not safe_public_text(raw.get("program"), max_length=180):
        return None
    if query["country"] and raw["country"] != query["country"]:
        return None
    if query["university"] and raw["university"].casefold().strip() != query["university"].casefold().strip():
        return None
    if query["program_or_major"] and raw["program"].casefold().strip() != query["program_or_major"].casefold().strip():
        return None
    if raw["intake_year"] != query["intake_year"]:
        return None
    urls = raw.get("official_sources")
    if not isinstance(urls, list) or not urls or not all(isinstance(u, dict) and set(u) == {"label", "url"} and safe_public_text(u["label"], max_length=80) and _safe_official_url(u["url"], source) for u in urls):
        return None
    facts_raw = raw.get("facts")
    if not isinstance(facts_raw, dict):
        return None
    try:
        evidence_id = program_evidence_id(source, raw.get("record_key"))
    except (TypeError, ValueError):
        return None
    facts = {}
    missing = []
    stale = False
    for requested in query["requested_facts"]:
        key = FACT_OUTPUTS[requested]
        fact, is_stale, is_missing = _project_fact(facts_raw.get(key), source, date_now)
        facts[key] = fact
        stale |= is_stale
        if is_missing:
            missing.append(key)
    return {
        "evidence_id": evidence_id,
        "university": raw["university"], "program": raw["program"],
        "country": raw["country"], "intake_year": raw["intake_year"],
        "facts": facts,
        "official_sources": [{"label": u["label"], "url": u["url"]} for u in urls],
        "freshness_status": "STALE" if stale else "UNKNOWN" if missing else "CURRENT",
        "missing_official_information": missing,
    }


def get_program_evidence(request: dict, *, source: Any = None) -> dict:
    """No source means unavailable; this function never reads Application System."""
    if not _valid_request(request):
        return envelope("INVALID_REQUEST", SOURCE_TYPE, limitations=LIMITATIONS, error_code="INVALID_REQUEST")
    if not approved_source(source) or not callable(getattr(source, "search", None)) or not callable(getattr(source, "is_official_url", None)) or not getattr(source, "official_hosts", None) or not isinstance(getattr(source, "provenance_secret", None), bytes) or len(source.provenance_secret) < 32 or not isinstance(getattr(source, "snapshot_digest", None), str) or not re.fullmatch(r"[0-9a-f]{64}", source.snapshot_digest):
        return envelope("UNAVAILABLE", SOURCE_TYPE, limitations=LIMITATIONS, error_code="APPROVED_PROGRAM_COPY_UNAVAILABLE")
    try:
        rows = call_with_timeout(lambda: source.search(request["query"].copy()), 1.0)
        if not isinstance(rows, list):
            raise ValueError("invalid source result")
        date_now = datetime.now(timezone.utc)
        data = [item for raw in rows[:20] if (item := _project_record(raw, request["query"], source, date_now)) is not None][:5]
        return envelope("OK" if data else "NO_RESULTS", SOURCE_TYPE, source_version=source.source_version, limitations=LIMITATIONS, data=data)
    except Exception:
        return envelope("UNAVAILABLE", SOURCE_TYPE, source_version=source.source_version, limitations=LIMITATIONS, error_code="PROGRAM_SOURCE_UNAVAILABLE")


def program_freshness_actions(program_response: dict, verification_response: dict | None = None) -> dict:
    """Workflow must perform these actions; no DB write occurs here."""
    stale = []
    if isinstance(program_response, dict) and program_response.get("status") == "OK":
        stale = [item["evidence_id"] for item in program_response.get("data", []) if isinstance(item, dict) and item.get("freshness_status") == "STALE"]
    failed_verification = bool(
        stale
        and verification_response is not None
        and (
            verification_response.get("status") != "OK"
            or any(item.get("status") != "VERIFIED" for item in verification_response.get("data", []))
            or not verification_response.get("data")
        )
    )
    return {
        "must_verify_official_source": bool(stale),
        "handoff_required": failed_verification,
        "notifications": [{"type": "PROGRAM_DB_UPDATE_REQUIRED", "evidence_id": identifier} for identifier in stale],
    }
