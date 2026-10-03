"""Opt-in adapter for an independently reviewed, Sales-safe Program JSON snapshot.

This format is intentionally unrelated to the Application System SQLite schema.
Constructing this adapter requires a separate owner manifest and a caller-held
provenance secret; importing it never activates a Program source.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .common import safe_public_text, timestamp

SNAPSHOT_SCHEMA = "sales-program-snapshot.v1"
APPROVAL_SCHEMA = "sales-program-approval.v1"
FACT_KEYS = {"academic_requirement", "prerequisites", "language_requirement", "tuition", "deadline"}
REQUEST_KEYS = {"entry_requirements": "academic_requirement", "prerequisites": "prerequisites", "language": "language_requirement", "tuition": "tuition", "deadline": "deadline"}
COUNTRIES = {"SG", "HK", "UK", "AU"}
REQUIREMENT_TYPES = {"hard_requirement", "recommended", "preferred", "not_specified"}
MAX_BYTES = 1_000_000


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path: str | Path) -> tuple[bytes, dict]:
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError("snapshot or approval missing or too large")
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    if not isinstance(value, dict):
        raise ValueError("invalid JSON root")
    return raw, value


def _official_url(url: object, hosts: tuple[str, ...]) -> bool:
    if not isinstance(url, str) or len(url) > 500 or any(ord(c) < 33 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        return bool(parsed.scheme == "https" and parsed.hostname in hosts
                    and parsed.port in (None, 443) and not parsed.username
                    and not parsed.password and not parsed.query and not parsed.fragment
                    and parsed.path.startswith("/"))
    except ValueError:
        return False


def _public(value: object, limit: int) -> bool:
    return safe_public_text(value, max_length=limit) and value == value.strip()


class ApprovedProgramSnapshot:
    """Read-only search source; raises on any incomplete source-level approval."""

    def __init__(self, snapshot_path: str | Path, approval_path: str | Path, *, provenance_secret: bytes):
        if not isinstance(provenance_secret, bytes) or len(provenance_secret) < 32:
            raise ValueError("caller-held provenance secret required")
        raw, snapshot = _read_json(snapshot_path)
        _, approval = _read_json(approval_path)
        digest = hashlib.sha256(raw).hexdigest()
        if set(snapshot) != {"schema_version", "source_version", "records"} or snapshot["schema_version"] != SNAPSHOT_SCHEMA:
            raise ValueError("invalid Sales snapshot schema")
        if set(approval) != {"schema_version", "snapshot_sha256", "source_version", "approval_status", "reviewed_by", "reviewed_at", "refresh_owner", "official_hosts", "allowed_scopes", "records"} or approval["schema_version"] != APPROVAL_SCHEMA:
            raise ValueError("invalid approval manifest schema")
        if approval["snapshot_sha256"] != digest or approval["source_version"] != snapshot["source_version"] or approval["approval_status"] != "APPROVED":
            raise ValueError("missing or mismatched owner approval")
        if not _public(snapshot["source_version"], 100) or not all(_public(approval[key], 100) for key in ("reviewed_by", "refresh_owner")) or timestamp(approval["reviewed_at"]) is None:
            raise ValueError("incomplete owner approval")
        hosts = approval["official_hosts"]
        if not isinstance(hosts, list) or not hosts or len(hosts) != len(set(hosts)) or not all(isinstance(h, str) and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", h) and h == h.lower() for h in hosts):
            raise ValueError("invalid official host allowlist")
        self.official_hosts = tuple(hosts)
        scopes = approval["allowed_scopes"]
        if not isinstance(scopes, list) or not scopes:
            raise ValueError("missing approved scope")
        allowed_scopes = set()
        for scope in scopes:
            if not isinstance(scope, dict) or set(scope) != {"country", "intake_year"} or scope["country"] not in COUNTRIES or type(scope["intake_year"]) is not int or not 2020 <= scope["intake_year"] <= 2100:
                raise ValueError("invalid approved scope")
            allowed_scopes.add((scope["country"], scope["intake_year"]))
        if len(allowed_scopes) != len(scopes):
            raise ValueError("duplicate approved scope")
        rows = snapshot["records"]
        approvals = approval["records"]
        if not isinstance(rows, list) or not isinstance(approvals, list) or not rows or len(rows) != len(approvals):
            raise ValueError("each record requires approval")
        approved = {}
        for item in approvals:
            if not isinstance(item, dict) or set(item) != {"record_key", "approval_status", "approved_facts", "reviewed_by", "reviewed_at"} or not _public(item["record_key"], 200) or item["approval_status"] != "APPROVED" or not _public(item["reviewed_by"], 100) or timestamp(item["reviewed_at"]) is None:
                raise ValueError("invalid record approval")
            if item["record_key"] in approved or not isinstance(item["approved_facts"], list):
                raise ValueError("duplicate or invalid record approval")
            facts = {}
            for fact in item["approved_facts"]:
                if not isinstance(fact, dict) or set(fact) != {"key", "official_url", "reviewed_by", "reviewed_at"} or fact["key"] not in FACT_KEYS or fact["key"] in facts or not _official_url(fact["official_url"], self.official_hosts) or not _public(fact["reviewed_by"], 100) or timestamp(fact["reviewed_at"]) is None:
                    raise ValueError("invalid field approval")
                facts[fact["key"]] = fact
            approved[item["record_key"]] = facts
        cleaned = []
        identities = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"record_key", "university", "program", "country", "intake_year", "official_sources", "facts"}:
                raise ValueError("invalid Sales record schema")
            key = row["record_key"]
            if not _public(key, 200) or key not in approved or not _public(row["university"], 150) or not _public(row["program"], 180) or row["country"] not in COUNTRIES or type(row["intake_year"]) is not int or (row["country"], row["intake_year"]) not in allowed_scopes:
                raise ValueError("record lacks approved identity or scope")
            identity = (row["country"], row["intake_year"], row["university"].casefold(), row["program"].casefold())
            if identity in identities:
                raise ValueError("ambiguous Program identity")
            identities.add(identity)
            sources = row["official_sources"]
            if not isinstance(sources, list) or not sources or not all(isinstance(s, dict) and set(s) == {"label", "url"} and _public(s["label"], 80) and _official_url(s["url"], self.official_hosts) for s in sources):
                raise ValueError("invalid official source URL")
            facts = row["facts"]
            if not isinstance(facts, dict) or not set(facts) <= FACT_KEYS:
                raise ValueError("non-allowlisted field")
            projected_facts = {}
            for fact_key, fact in facts.items():
                if not isinstance(fact, dict) or not {"value", "last_checked", "official_url"} <= set(fact) or not set(fact) <= {"value", "last_checked", "official_url", "requirement_type", "currency"} or not _public(fact["value"], 500) or timestamp(fact["last_checked"]) is None or not _official_url(fact["official_url"], self.official_hosts):
                    raise ValueError("invalid official fact")
                if "requirement_type" in fact and fact["requirement_type"] not in REQUIREMENT_TYPES:
                    raise ValueError("invalid requirement type")
                if "currency" in fact and not _public(fact["currency"], 10):
                    raise ValueError("invalid currency")
                field_approval = approved[key].get(fact_key)
                if field_approval is None:
                    continue
                if field_approval["official_url"] != fact["official_url"]:
                    raise ValueError("field source differs from approval")
                checked = timestamp(fact["last_checked"])
                if checked > datetime.now(timezone.utc) + timedelta(days=1):
                    raise ValueError("future official check")
                projected_facts[fact_key] = dict(fact)
            if set(approved[key]) - set(facts):
                raise ValueError("approved field absent from snapshot")
            cleaned.append({"approval_status": "APPROVED", "source_status": "OFFICIAL_VERIFIED", **{k: row[k] for k in ("record_key", "university", "program", "country", "intake_year")}, "official_sources": [dict(s) for s in sources], "facts": projected_facts})
        self._rows = tuple(cleaned)
        self.approval_status = "APPROVED"
        self.source_version = snapshot["source_version"]
        self.snapshot_digest = digest
        self.provenance_secret = provenance_secret

    def is_official_url(self, url: str) -> bool:
        return _official_url(url, self.official_hosts)

    def search(self, query: dict) -> list[dict]:
        if not isinstance(query, dict) or not {"country", "university", "program_or_major", "intake_year", "requested_facts"} <= set(query):
            raise ValueError("invalid Program query")
        rows = []
        for row in self._rows:
            if row["intake_year"] != query["intake_year"] or query["country"] and row["country"] != query["country"]:
                continue
            if query["university"] and row["university"].casefold() != query["university"].strip().casefold():
                continue
            if query["program_or_major"] and row["program"].casefold() != query["program_or_major"].strip().casefold():
                continue
            rows.append({**row, "official_sources": [dict(s) for s in row["official_sources"]], "facts": {k: dict(v) for k, v in row["facts"].items() if k in {REQUEST_KEYS[f] for f in query["requested_facts"]}}})
        return rows[:20]
