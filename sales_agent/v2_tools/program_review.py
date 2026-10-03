"""Read-only review of an explicitly supplied Program candidate bundle.

No candidate bundle or approval ships with the public repository. Inspection
never activates a source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .official_program_source import inspect_official_page
from .program_snapshot import _read_json


def review_candidate(directory: str | Path, *, live: bool = False) -> dict:
    root = Path(directory)
    raw, snapshot = _read_json(root / "snapshot.candidate.json")
    catalog_raw, catalog = _read_json(root / "official-catalog.candidate.json")
    _, program_approval = _read_json(root / "program-approval.pending.json")
    _, official_approval = _read_json(root / "official-approval.pending.json")
    digest = hashlib.sha256(raw).hexdigest()
    if (catalog["snapshot_sha256"] != digest
            or program_approval["snapshot_sha256"] != digest
            or official_approval["catalog_sha256"] != hashlib.sha256(catalog_raw).hexdigest()):
        raise ValueError("candidate bundle hash mismatch")
    if (program_approval["approval_status"] != "PENDING_OWNER_REVIEW"
            or official_approval["approval_status"] != "PENDING_OWNER_REVIEW"
            or official_approval["approved_fields"]
            or any(record["approved_facts"] for record in program_approval["records"])):
        raise ValueError("review only accepts unapproved candidates")
    hosts = program_approval["official_hosts"]
    urls = list(dict.fromkeys(field["official_url"] for field in catalog["fields"]))
    if len(urls) > 2:
        raise ValueError("review is limited to two official pages")
    pages = {}
    if live:
        for url in urls:
            try:
                pages[url] = inspect_official_page(url, official_hosts=tuple(hosts))
            except Exception:
                pages[url] = None
    fields = []
    for field in catalog["fields"]:
        rows = [row for row in snapshot["records"] if row["record_key"] == field["record_key"]]
        if (len(rows) != 1
                or any(rows[0][key] != field[key] for key in ("university", "program", "country", "intake_year"))
                or rows[0]["facts"][field["fact_key"]]["official_url"] != field["official_url"]):
            raise ValueError("candidate field identity mismatch")
        page = pages.get(field["official_url"])
        status = "NOT_LIVE_CHECKED"
        if live:
            status = "SOURCE_UNAVAILABLE" if page is None else "BODY_CHANGED_REVIEW_REQUIRED"
            if page and page["body_sha256"] == field["body_sha256"] and all(anchor in page["body"] for anchor in field["anchors"]):
                status = "BODY_UNCHANGED_OWNER_PENDING"
        fields.append({"field_id": field["field_id"], "status": status,
                       "official_url": field["official_url"],
                       "checked_at": page["checked_at"] if page else None})
    return {"status": "OWNER_REVIEW_REQUIRED", "runtime_activated": False,
            "approved_field_count": 0, "network_attempts": len(pages), "fields": fields}


def review_delegated(directory: str | Path, *, provenance_secret: bytes, live: bool = False) -> dict:
    """Inspect a configured delegated bundle; no bundled authority is implied."""
    from .delegated_program import MODE, load_delegated_program_sources
    from .programs import program_evidence_id
    from .verify import verify_program_claims

    program, official = load_delegated_program_sources(directory, provenance_secret=provenance_secret)
    claims = [{"claim_id": field["field_id"],
               **{key: field[key] for key in ("university", "program", "intake_year", "intake_batch", "fact_key")},
               "expected_value": field["official_value"],
               "program_evidence_id": program_evidence_id(program, field["record_key"])}
              for field in program._mappings]
    verification = verify_program_claims(claims, official_source=official) if live else None
    return {"authorization_mode": MODE, "manual_owner_field_review": False,
            "configured_host_process_started": False,
            "snapshot_sha256": program.snapshot_digest,
            "candidate_field_count": len(claims), "verification": verification}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    result = review_candidate(args.directory, live=args.live)
    print(json.dumps(result, indent=2))
    if args.live and any(field["status"] != "BODY_UNCHANGED_OWNER_PENDING" for field in result["fields"]):
        raise SystemExit(1)
