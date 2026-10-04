"""Deterministic projection of Owner-approved customer-facing advantages."""

from __future__ import annotations

from pathlib import Path

from .common import envelope, read_json_limited, safe_public_text, timestamp

SOURCE_TYPE = "approved_advantage"
DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "knowledge" / "advantages.v1.json"
# Digest of the synthetic portfolio catalog, never an internal claim catalog.
APPROVED_SHA256 = "e46b8dfb8b6816e0d81e14b21e6102d3b906dc7058d958b857652e812ef67b91"
ITEM_FIELDS = {
    "advantage_id", "claim", "customer_benefit", "applicable_when",
    "must_not_claim", "regions", "tags", "approval_status", "approved_by",
    "approved_at", "version",
}


def select_approved_advantages(
    *,
    region: str | None,
    concern_tags: list[str],
    approved_case_evidence: bool = False,
    service_available: bool = False,
    catalog_path: str | Path = DEFAULT_CATALOG,
) -> dict:
    """Return at most two applicable options; the Decision Agent may ignore them.

    ``service_available`` means a human/operator has confirmed current capacity
    for services that depend on local staff or a current internship item.
    """
    if region not in {None, "SG", "HK", "UK", "AU"} or not isinstance(concern_tags, list) or len(concern_tags) > 12 or not all(safe_public_text(x, max_length=50) for x in concern_tags):
        return envelope("INVALID_REQUEST", SOURCE_TYPE, error_code="INVALID_REQUEST")
    try:
        catalog = read_json_limited(catalog_path, expected_sha256=APPROVED_SHA256)
        if not isinstance(catalog, dict) or set(catalog) != {"schema_version", "source_version", "approved_by", "approved_at", "advantages"}:
            raise ValueError("invalid catalog")
        if catalog["schema_version"] != "sales.advantages.v1" or catalog["approved_by"] != "OWNER" or not timestamp(catalog["approved_at"]):
            raise ValueError("unapproved catalog")
        source_version = catalog["source_version"]
        if not safe_public_text(source_version, max_length=100):
            raise ValueError("invalid version")
        items = catalog["advantages"]
        if not isinstance(items, list) or len(items) != 8:
            raise ValueError("invalid item count")
        seen = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != ITEM_FIELDS or item["advantage_id"] in seen:
                raise ValueError("invalid or duplicate advantage")
            seen.add(item["advantage_id"])
            if item["approval_status"] != "APPROVED" or item["approved_by"] != "OWNER" or item["version"] != source_version or not timestamp(item["approved_at"]):
                raise ValueError("unapproved advantage")
            if not all(safe_public_text(item[k], max_length=700) for k in ("advantage_id", "claim", "customer_benefit")):
                raise ValueError("invalid text")
            for key in ("applicable_when", "must_not_claim", "regions", "tags"):
                if not isinstance(item[key], list) or not item[key] or not all(safe_public_text(x, max_length=350) for x in item[key]):
                    raise ValueError("invalid list")
            if not set(item["regions"]) <= {"GLOBAL", "SG", "HK", "UK", "AU"}:
                raise ValueError("invalid region")
        tags = set(concern_tags)
        selected = []
        for item in items:
            if not tags.intersection(item["tags"]):
                continue
            if "GLOBAL" not in item["regions"] and region not in item["regions"]:
                continue
            if "CASE_EVIDENCE" in item["tags"] and not approved_case_evidence:
                continue
            if item["advantage_id"] in {"ADV-003", "ADV-006", "ADV-008"} and not service_available:
                continue
            selected.append({key: item[key] for key in ("advantage_id", "claim", "customer_benefit", "applicable_when", "must_not_claim", "regions", "tags", "version")})
            if len(selected) == 2:
                break
        return envelope("OK" if selected else "NO_RESULTS", SOURCE_TYPE, source_version=source_version, limitations=["CONTEXT_DEPENDENT", "NO_OUTCOME_GUARANTEE"], data=selected)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return envelope("UNAVAILABLE", SOURCE_TYPE, error_code="ADVANTAGES_CATALOG_UNAVAILABLE")
