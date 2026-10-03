"""Explicit Program batch qualifiers; no calendar-year-to-intake inference."""

import re
from copy import deepcopy
from datetime import date


def valid_batch(value, year) -> bool:
    if not isinstance(value, str) or type(year) is not int or not 2020 <= year <= 2100:
        return False
    if re.fullmatch(r"AY[0-9]{4}/[0-9]{2}", value):
        return value == f"AY{year}/{(year + 1) % 100:02d}"
    if re.fullmatch(r"START[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value[5:]).year == year
        except ValueError:
            return False
    return False


def bind_program_batch(request, snapshot):
    """Only a confirmed host/CRM qualifier authorizes model batch selection.

    No language-model inference, free-text substring, alias or year-only fallback.
    Legacy v1 queries remain legacy, and cannot select batch-scoped records.
    """
    from .programs import _valid_request
    if not _valid_request(request):
        return None
    if request["contract_version"] == "sales-tools.v1":
        return deepcopy(request)
    record = (snapshot or {}).get("snapshot", {})
    education = record.get("education", {})
    provenance = (snapshot or {}).get("field_provenance", {})
    for field in ("intake_year", "intake_batch"):
        meta = provenance.get("education." + field, {})
        if (meta.get("confirmed") is not True or meta.get("source_type") != "SALESPERSON_INPUT"
                or type(education.get(field)) is not type(request["query"][field])
                or education.get(field) != request["query"][field]):
            return None
    return deepcopy(request)
