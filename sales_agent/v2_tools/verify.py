"""Fail-closed verification of concrete program claims against official facts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import re
from typing import Any
from urllib.parse import urlsplit

from .program_batch import valid_batch
from .common import approved_source, call_with_timeout, envelope, safe_public_text, timestamp

SOURCE_TYPE = "official_program_page"
CLAIM_KEYS = {"claim_id", "university", "program", "intake_year", "fact_key", "expected_value", "program_evidence_id"}
FACT_KEYS = {"academic_requirement", "language_requirement", "prerequisites", "tuition", "deadline", "course", "policy"}


def _valid_claims(claims: Any) -> bool:
    if not isinstance(claims, list) or not 1 <= len(claims) <= 20:
        return False
    seen = set()
    for claim in claims:
        if (not isinstance(claim, dict) or set(claim) not in (CLAIM_KEYS, CLAIM_KEYS | {"intake_batch"})
                or not isinstance(claim["claim_id"], str) or claim["claim_id"] in seen):
            return False
        if "intake_batch" in claim and not valid_batch(claim["intake_batch"], claim["intake_year"]):
            return False
        seen.add(claim["claim_id"])
        if not all(safe_public_text(claim[key], max_length=500 if key == "expected_value" else 160) for key in ("claim_id", "university", "program", "fact_key", "expected_value")):
            return False
        evidence_id = claim["program_evidence_id"]
        # A digest can coincidentally contain a phone-shaped digit run. Typed
        # opaque IDs are still bound to the approved snapshot by the adapter.
        if not (isinstance(evidence_id, str) and re.fullmatch(r"program_[0-9a-f]{32}", evidence_id)) and not safe_public_text(evidence_id, max_length=160):
            return False
        if claim["fact_key"] not in FACT_KEYS or type(claim["intake_year"]) is not int or not 2020 <= claim["intake_year"] <= 2100:
            return False
    return True


def _official_url(url: Any, source: Any) -> bool:
    if not isinstance(url, str) or len(url) > 500:
        return False
    parsed = urlsplit(url)
    hosts = getattr(source, "official_hosts", ())
    allowed = isinstance(hosts, (tuple, list)) and any(
        isinstance(host, str) and parsed.hostname and (parsed.hostname == host or parsed.hostname.endswith("." + host))
        for host in hosts
    )
    return bool(parsed.scheme == "https" and allowed and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and source.is_official_url(url))


def _result(claim: dict, status: str, *, url: str | None = None, checked_at: str | None = None) -> dict:
    return {
        "claim_id": claim["claim_id"], "status": status,
        "official_url": url, "checked_at": checked_at,
        "fact_scope": {"university": claim["university"], "program": claim["program"], "intake_year": claim["intake_year"], "fact_key": claim["fact_key"], **({"intake_batch": claim["intake_batch"]} if "intake_batch" in claim else {})},
    }


def verify_program_claims(claims: list[dict], *, official_source: Any = None, max_searches: int = 2) -> dict:
    """Check exact, scoped facts supplied by an approved official-page adapter.

    The adapter must enforce the bounded official-site searches. This boundary
    does not turn a search snippet, third-party page or older intake into proof.
    A scoped official value that differs from the draft is CONTRADICTED; a
    source with a different project, intake, or fact scope remains UNVERIFIED.
    """
    def response_envelope(*args, **kwargs):
        response = envelope(*args, **kwargs)
        if isinstance(claims, list) and any(isinstance(c, dict) and "intake_batch" in c for c in claims):
            response["tool_version"] = "sales-tools.v1.1"
        return response

    if not _valid_claims(claims) or type(max_searches) is not int or not 1 <= max_searches <= 2:
        return response_envelope("INVALID_REQUEST", SOURCE_TYPE, error_code="INVALID_REQUEST")
    if not approved_source(official_source) or not callable(getattr(official_source, "search_claims", None)) or not callable(getattr(official_source, "is_official_url", None)) or not getattr(official_source, "official_hosts", None):
        return response_envelope("UNAVAILABLE", SOURCE_TYPE, limitations=["OFFICIAL_SOURCE_REQUIRED"], data=[_result(c, "SOURCE_UNAVAILABLE") for c in claims], error_code="OFFICIAL_SEARCH_UNAVAILABLE")
    try:
        search_response = call_with_timeout(lambda: official_source.search_claims([c.copy() for c in claims], max_searches=max_searches), 5.0)
        if not isinstance(search_response, dict) or set(search_response) != {"search_count", "facts"} or type(search_response["search_count"]) is not int or not 0 <= search_response["search_count"] <= max_searches or not isinstance(search_response["facts"], list):
            raise ValueError("invalid official search response")
        fact_rows = [fact for fact in search_response["facts"] if isinstance(fact, dict)]
        fact_counts = Counter(fact.get("claim_id") for fact in fact_rows)
        facts = {fact.get("claim_id"): fact for fact in fact_rows}
        now = datetime.now(timezone.utc)
        data = []
        for claim in claims:
            if fact_counts[claim["claim_id"]] > 1:
                # Multiple rows for one claim could hide an official-source
                # conflict; never let last-write-wins choose a fact.
                data.append(_result(claim, "UNVERIFIED"))
                continue
            fact = facts.get(claim["claim_id"])
            if not isinstance(fact, dict):
                data.append(_result(claim, "UNVERIFIED"))
                continue
            if any(fact.get(key) != claim[key] for key in ("university", "program", "intake_year", "fact_key")):
                data.append(_result(claim, "UNVERIFIED"))
                continue
            if fact.get("intake_batch") != claim.get("intake_batch"):
                data.append(_result(claim, "UNVERIFIED"))
                continue
            url = fact.get("official_url")
            checked = timestamp(fact.get("checked_at"))
            if (not _official_url(url, official_source) or checked is None
                    or not now - timedelta(days=1) <= checked <= now + timedelta(minutes=5)
                    or fact.get("source_kind") != "OFFICIAL_PAGE_BODY"):
                data.append(_result(claim, "UNVERIFIED"))
                continue
            official_value = fact.get("official_value")
            if not safe_public_text(official_value, max_length=500):
                data.append(_result(claim, "UNVERIFIED", url=url, checked_at=checked.isoformat()))
                continue
            if search_response["search_count"] == 0:
                status = "UNVERIFIED"
            elif official_value.strip().casefold() == claim["expected_value"].strip().casefold():
                status = "VERIFIED"
            else:
                status = "CONTRADICTED"
            data.append(_result(claim, status, url=url, checked_at=checked.isoformat()))
        result = response_envelope("OK", SOURCE_TYPE, source_version=official_source.source_version, limitations=["EXACT_OFFICIAL_FACT_MATCH_ONLY", "NOT_ADMISSION_PROBABILITY"], data=data)
        result["search_count"] = search_response["search_count"]
        return result
    except Exception:
        return response_envelope("UNAVAILABLE", SOURCE_TYPE, source_version=official_source.source_version, limitations=["OFFICIAL_SOURCE_REQUIRED"], data=[_result(c, "SOURCE_UNAVAILABLE") for c in claims], error_code="OFFICIAL_SEARCH_UNAVAILABLE")
