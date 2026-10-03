"""Opt-in, owner-gated verification against a pinned official HTML body.

No discovery, credentials, redirects, scripts, database writes or automatic
approval. A changed page needs a new human mapping review, even if the change
looks harmless. A body digest is a change detector, not semantic proof.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .common import safe_public_text, timestamp
from .program_snapshot import ApprovedProgramSnapshot, REQUEST_KEYS, _official_url, _read_json
from .programs import program_evidence_id
from .verify import _valid_claims

MAX_PAGE_BYTES = 1_000_000
NORMALIZER = "html-body-text.v1"


class _BodyText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        if tag in {"script", "style", "noscript", "template"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag == "body":
            self.in_body = False
        if tag in {"script", "style", "noscript", "template"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if self.in_body and not self.ignored:
            self.parts.append(data)


def normalize_body(raw: bytes) -> str:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_PAGE_BYTES:
        raise ValueError("invalid page size")
    parser = _BodyText()
    parser.feed(raw.decode("utf-8"))
    parser.close()
    body = " ".join(" ".join(parser.parts).split())
    if not body:
        raise ValueError("missing HTML body")
    return body


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("official redirect requires review")


def inspect_official_page(url: str, *, official_hosts: tuple[str, ...]) -> dict:
    """One bounded unauthenticated GET for internal review; never marks approval.

    URL must already be selected from official evidence. The returned body is
    untrusted data for internal inspection only and must not enter model prompts.
    """
    if not _official_url(url, official_hosts):
        raise ValueError("official URL not allowed")
    opener = build_opener(ProxyHandler({}), _NoRedirects())
    request = Request(url, headers={"User-Agent": "SalesProgramReview/1.0", "Accept": "text/html", "Accept-Encoding": "identity"})
    started = time.monotonic()
    with opener.open(request, timeout=2.0) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("unexpected official response")
        if response.headers.get_content_type() != "text/html" or response.headers.get("Content-Encoding", "identity") != "identity":
            raise ValueError("unsupported official response")
        charset = response.headers.get_content_charset()
        if charset and charset.lower() not in {"utf-8", "utf8"}:
            raise ValueError("unsupported official charset")
        chunks = []
        size = 0
        while True:
            if time.monotonic() - started > 2.0:
                raise TimeoutError("official read deadline")
            chunk = response.read1(min(65536, MAX_PAGE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_PAGE_BYTES:
                raise ValueError("official page too large")
            chunks.append(chunk)
    raw = b"".join(chunks)
    body = normalize_body(raw)
    return {"official_url": url, "checked_at": datetime.now(timezone.utc).isoformat(),
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "raw_sha256": hashlib.sha256(raw).hexdigest(), "body": body}


class PinnedOfficialProgramSource:
    """Approved field mappings plus fresh identical official bodies.

    Only exact claims attached to the same approved Program evidence ID can be
    refreshed. Searches are bounded direct page lookups (one per unique URL),
    not a web search engine. Each lookup has one attempt and no retry.
    """

    def __init__(self, catalog_path, approval_path, *, program_source: ApprovedProgramSnapshot):
        if not isinstance(program_source, ApprovedProgramSnapshot):
            raise ValueError("approved Program snapshot required")
        raw, catalog = _read_json(catalog_path)
        _, approval = _read_json(approval_path)
        if set(catalog) != {"schema_version", "source_version", "snapshot_sha256", "normalizer", "fields"} or catalog["schema_version"] != "sales-official-catalog.v1" or catalog["normalizer"] != NORMALIZER:
            raise ValueError("invalid official catalog")
        if set(approval) != {"schema_version", "catalog_sha256", "approval_status", "reviewed_by", "reviewed_at", "refresh_owner", "approved_fields"} or approval["schema_version"] != "sales-official-approval.v1":
            raise ValueError("invalid official approval")
        if approval["approval_status"] != "APPROVED" or approval["catalog_sha256"] != hashlib.sha256(raw).hexdigest():
            raise ValueError("official mapping owner approval required")
        now = datetime.now(timezone.utc)
        for key in ("reviewed_by", "refresh_owner"):
            if not safe_public_text(approval[key], max_length=100):
                raise ValueError("missing official reviewer")
        reviewed = timestamp(approval["reviewed_at"])
        if reviewed is None or reviewed > now + timedelta(minutes=5):
            raise ValueError("invalid official review time")
        if catalog["snapshot_sha256"] != program_source.snapshot_digest or not safe_public_text(catalog["source_version"], max_length=100):
            raise ValueError("official catalog snapshot mismatch")
        fields = catalog["fields"]
        approvals = approval["approved_fields"]
        if not isinstance(fields, list) or not 1 <= len(fields) <= 100 or not isinstance(approvals, list):
            raise ValueError("invalid official field list")
        accepted = set()
        for item in approvals:
            if not isinstance(item, dict) or set(item) != {"field_id", "reviewed_by", "reviewed_at"} or not safe_public_text(item["field_id"]) or not safe_public_text(item["reviewed_by"], max_length=100):
                raise ValueError("invalid official field approval")
            checked = timestamp(item["reviewed_at"])
            if checked is None or checked > now + timedelta(minutes=5) or item["field_id"] in accepted:
                raise ValueError("invalid official field review")
            accepted.add(item["field_id"])
        mappings = []
        seen = set()
        scopes = set()
        page_digests = {}
        for field in fields:
            required = {"field_id", "record_key", "country", "university", "program", "intake_year", "fact_key", "official_url", "body_sha256", "checked_at", "applicable_batch", "locator", "anchors"}
            if not isinstance(field, dict) or set(field) != required:
                raise ValueError("invalid official field mapping")
            for key in ("field_id", "record_key", "university", "program", "applicable_batch", "locator"):
                if not safe_public_text(field[key], max_length=200):
                    raise ValueError("invalid official mapping text")
            if field["field_id"] in seen or field["fact_key"] not in REQUEST_KEYS.values() or field["country"] not in {"SG", "HK", "UK", "AU"} or type(field["intake_year"]) is not int:
                raise ValueError("duplicate or invalid mapping")
            seen.add(field["field_id"])
            scope = (field["university"].casefold(), field["program"].casefold(), field["intake_year"], field["fact_key"])
            if scope in scopes:
                raise ValueError("ambiguous official field")
            scopes.add(scope)
            url = field["official_url"]
            if not _official_url(url, program_source.official_hosts) or not isinstance(field["body_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", field["body_sha256"]):
                raise ValueError("invalid official page pin")
            if url in page_digests and page_digests[url] != field["body_sha256"]:
                raise ValueError("conflicting official page pins")
            page_digests[url] = field["body_sha256"]
            checked = timestamp(field["checked_at"])
            if checked is None or checked > now + timedelta(minutes=5):
                raise ValueError("invalid official capture time")
            anchors = field["anchors"]
            if not isinstance(anchors, list) or not 1 <= len(anchors) <= 8 or not all(safe_public_text(a, max_length=200) for a in anchors):
                raise ValueError("invalid official anchors")
            if field["field_id"] not in accepted:
                continue
            requested = next(k for k, v in REQUEST_KEYS.items() if v == field["fact_key"])
            rows = program_source.search({"country": field["country"], "university": field["university"], "program_or_major": field["program"], "intake_year": field["intake_year"], "requested_facts": [requested]})
            if len(rows) != 1 or rows[0]["record_key"] != field["record_key"]:
                raise ValueError("mapping lacks approved Program identity")
            fact = rows[0]["facts"].get(field["fact_key"])
            if fact is None or fact["official_url"] != url:
                raise ValueError("mapping lacks approved Program field")
            mappings.append({**field, "anchors": tuple(anchors), "official_value": fact["value"], "program_evidence_id": program_evidence_id(program_source, field["record_key"])})
        if accepted - seen or not mappings:
            raise ValueError("missing approved official fields")
        self._mappings = tuple(mappings)
        # Keep the exact approved snapshot identity for host-side wiring checks.
        self.program_source = program_source
        self.official_hosts = program_source.official_hosts
        self._urls = frozenset(f["official_url"] for f in mappings)
        self.source_version = catalog["source_version"]
        self.approval_status = "APPROVED"

    def is_official_url(self, url: str) -> bool:
        return _official_url(url, self.official_hosts) and url in self._urls

    def search_claims(self, claims: list[dict], *, max_searches: int = 2) -> dict:
        if not _valid_claims(claims) or type(max_searches) is not int or not 1 <= max_searches <= 2:
            raise ValueError("invalid official claim request")
        selected = []
        for claim in claims:
            matches = [f for f in self._mappings if all(f[k] == claim[k] for k in ("university", "program", "intake_year", "fact_key", "program_evidence_id"))]
            if len(matches) == 1:
                selected.append((claim, matches[0]))
        pages = {}
        for _, field in selected:
            url = field["official_url"]
            if url in pages or len(pages) >= max_searches:
                continue
            # A failure consumes the lookup budget; never search until agreement.
            pages[url] = inspect_official_page(url, official_hosts=self.official_hosts)
        facts = []
        for claim, field in selected:
            page = pages.get(field["official_url"])
            if page is None or page["official_url"] != field["official_url"] or page["body_sha256"] != field["body_sha256"] or not all(a in page["body"] for a in field["anchors"]):
                continue
            facts.append({"claim_id": claim["claim_id"], **{k: field[k] for k in ("university", "program", "intake_year", "fact_key", "official_url", "official_value")}, "checked_at": page["checked_at"], "source_kind": "OFFICIAL_PAGE_BODY"})
        return {"search_count": len(pages), "facts": facts}
