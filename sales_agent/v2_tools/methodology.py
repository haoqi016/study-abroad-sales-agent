"""Frozen, digest-pinned methodology-card projection."""

from __future__ import annotations

from pathlib import Path

from .common import envelope, read_json_limited, safe_public_text, timestamp

SOURCE_TYPE = "approved_methodology_card"
APPROVED_SHA256 = "b5cf247426a473338324a3b10b999a0ab1ce293374e1824fb2908f5456593929"
DEFAULT_CATALOG = Path(__file__).with_name("methodologies.approved.json")
FIELDS = {
    "schema_version", "id", "name", "core_idea", "lenses", "potential_signals",
    "limits", "source_refs", "review_status", "approval",
}
LIMITATIONS = ["ADVISORY_ONLY", "NOT_CUSTOMER_FACT", "MAY_BE_IGNORED_OR_COMBINED"]


def get_sales_methodologies(request: dict, *, catalog_path: str | Path = DEFAULT_CATALOG) -> dict:
    if not isinstance(request, dict) or set(request) != {"contract_version", "context"}:
        return envelope("INVALID_REQUEST", SOURCE_TYPE, error_code="INVALID_REQUEST")
    context = request.get("context")
    if request["contract_version"] != "sales-tools.v1" or not isinstance(context, dict) or set(context) != {"objection", "conversation_boundary"}:
        return envelope("INVALID_REQUEST", SOURCE_TYPE, error_code="INVALID_REQUEST")
    if context["objection"] not in {"price", "value", "timing", "competitor", "unknown"} or context["conversation_boundary"] not in {"active", "stop_requested"}:
        return envelope("INVALID_REQUEST", SOURCE_TYPE, error_code="INVALID_REQUEST")
    if context["conversation_boundary"] == "stop_requested":
        return envelope("POLICY_BLOCKED", SOURCE_TYPE, error_code="CONTACT_STOP_REQUESTED")
    try:
        cards = read_json_limited(catalog_path, expected_sha256=APPROVED_SHA256)
        if not isinstance(cards, list) or len(cards) != 3:
            raise ValueError("invalid card count")
        data = []
        seen = set()
        for card in cards:
            if not isinstance(card, dict) or set(card) != FIELDS or card["schema_version"] != "sales.methodology.v2":
                raise ValueError("invalid card schema")
            if card["review_status"] != "APPROVED" or card["id"] in seen:
                raise ValueError("unapproved or duplicate card")
            seen.add(card["id"])
            approval = card["approval"]
            if not isinstance(approval, dict) or set(approval) != {"reviewed_by", "reviewed_at"} or not safe_public_text(approval["reviewed_by"]) or not timestamp(approval["reviewed_at"]):
                raise ValueError("invalid approval")
            if not all(safe_public_text(card[k], max_length=500) for k in ("id", "name", "core_idea")):
                raise ValueError("invalid card text")
            if not all(isinstance(card[k], list) and card[k] and all(safe_public_text(x, max_length=500) for x in card[k]) for k in ("lenses", "potential_signals", "limits", "source_refs")):
                raise ValueError("invalid card lists")
            data.append({
                "evidence_id": f"methodology:{card['id']}:{APPROVED_SHA256[:12]}",
                "name": card["name"], "core_idea": card["core_idea"],
                "lenses": card["lenses"], "limits": card["limits"],
                "approval": approval,
            })
        return envelope("OK", SOURCE_TYPE, source_version=f"methodology-v2:{APPROVED_SHA256}", limitations=LIMITATIONS, data=data)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return envelope("UNAVAILABLE", SOURCE_TYPE, limitations=LIMITATIONS, error_code="APPROVED_CARDS_UNAVAILABLE")
