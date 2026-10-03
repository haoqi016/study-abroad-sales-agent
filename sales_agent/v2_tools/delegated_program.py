"""Official-only Program facts under a recorded Owner delegation.

This is a curated official-source snapshot, not a master DB approval or a
claim that Owner manually reviewed fields. Files are trusted host configuration;
hashes bind their contents, not the identity of the file writer.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import re
from pathlib import Path

from .common import safe_public_text, timestamp
from .official_program_source import NORMALIZER, PinnedOfficialProgramSource, inspect_official_page
from .program_batch import valid_batch
from .program_snapshot import ApprovedProgramSnapshot, COUNTRIES, FACT_KEYS, REQUEST_KEYS, _official_url, _read_json
from .programs import program_evidence_id
from .verify import _valid_claims

MODE = "OWNER_DELEGATED_OFFICIAL_VERIFICATION"
MAX_CHECK_AGE = timedelta(hours=24)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _text(value, limit=200):
    return safe_public_text(value, max_length=limit) and value == value.strip()


def _fresh(value):
    checked = timestamp(value)
    now = datetime.now(timezone.utc)
    return checked is not None and now - MAX_CHECK_AGE <= checked <= now + timedelta(minutes=5)


class DelegatedProgramSnapshot(ApprovedProgramSnapshot):
    """Batch-scoped source; validates authority/receipts, then GETs before use."""

    requires_live_official_check = True
    authorization_mode = MODE

    def __init__(self, directory, *, provenance_secret: bytes):
        if not isinstance(provenance_secret, bytes) or len(provenance_secret) < 32:
            raise ValueError("caller-held provenance secret required")
        root = Path(directory)
        sr, snapshot = _read_json(root / "snapshot.delegated.json")
        cr, catalog = _read_json(root / "official-catalog.delegated.json")
        rr, receipts = _read_json(root / "verification-receipts.delegated.json")
        _, auth = _read_json(root / "authorization.delegated.json")
        if (set(auth) != {"schema_version", "authorization_mode", "approval_status", "authorized_by", "authorized_at", "scope", "official_hosts", "snapshot_sha256", "catalog_sha256", "receipts_sha256", "max_check_age_hours", "manual_owner_field_review", "school_confirmed_mapping"}
                or auth["schema_version"] != "sales-program-authorization.v2"
                or auth["authorization_mode"] != MODE or auth["approval_status"] != "APPROVED"
                or not _text(auth["authorized_by"], 100)
                or timestamp(auth["authorized_at"]) is None
                or timestamp(auth["authorized_at"]) > datetime.now(timezone.utc) + timedelta(minutes=5)
                or auth["scope"] != "CURRENT_OFFICIAL_EXACT_PROGRAM_AND_BATCH_FACTS_ONLY"
                or auth["manual_owner_field_review"] is not False or auth["school_confirmed_mapping"] is not False
                or type(auth["max_check_age_hours"]) is not int or auth["max_check_age_hours"] != 24):
            raise ValueError("invalid delegated authorization provenance")
        hosts = auth["official_hosts"]
        if (not isinstance(hosts, list) or not hosts or len(hosts) != len(set(hosts))
                or not all(isinstance(h, str) and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", h) for h in hosts)):
            raise ValueError("invalid delegated official hosts")
        hosts = tuple(hosts)
        if (auth["snapshot_sha256"] != _digest(sr) or auth["catalog_sha256"] != _digest(cr)
                or auth["receipts_sha256"] != _digest(rr)):
            raise ValueError("delegated artifact hash mismatch")
        if set(snapshot) != {"schema_version", "source_version", "records"} or snapshot["schema_version"] != "sales-program-snapshot.v2" or not _text(snapshot["source_version"], 100):
            raise ValueError("invalid delegated snapshot")
        if (set(catalog) != {"schema_version", "source_version", "snapshot_sha256", "normalizer", "fields"}
                or catalog["schema_version"] != "sales-official-catalog.v2" or catalog["normalizer"] != NORMALIZER
                or catalog["snapshot_sha256"] != _digest(sr) or catalog["source_version"] != snapshot["source_version"]):
            raise ValueError("invalid delegated catalog")
        if (set(receipts) != {"schema_version", "snapshot_sha256", "catalog_sha256", "generated_by", "fields"}
                or receipts["schema_version"] != "sales-official-verification-receipts.v1"
                or receipts["snapshot_sha256"] != _digest(sr) or receipts["catalog_sha256"] != _digest(cr)
                or receipts["generated_by"] != "CODEX_OFFICIAL_RESEARCH"):
            raise ValueError("invalid delegated receipts")
        rows = snapshot["records"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
            raise ValueError("invalid delegated records")
        records = {}
        identities = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"record_key", "university", "program", "country", "intake_year", "intake_batch", "official_sources", "facts"}:
                raise ValueError("invalid delegated record schema")
            if (not all(_text(row[k], 150) for k in ("record_key", "university", "program"))
                    or row["record_key"] in records or row["country"] not in COUNTRIES
                    or not valid_batch(row["intake_batch"], row["intake_year"])):
                raise ValueError("invalid delegated identity")
            identity = (row["university"].casefold(), row["program"].casefold(), row["intake_year"], row["intake_batch"])
            if identity in identities:
                raise ValueError("ambiguous delegated identity")
            identities.add(identity)
            sources = row["official_sources"]
            if not isinstance(sources, list) or not sources or not all(isinstance(s, dict) and set(s) == {"label", "url"} and _text(s["label"], 80) and _official_url(s["url"], hosts) for s in sources):
                raise ValueError("invalid delegated sources")
            if not isinstance(row["facts"], dict) or not row["facts"] or not set(row["facts"]) <= FACT_KEYS:
                raise ValueError("invalid delegated facts")
            for fact in row["facts"].values():
                if (not isinstance(fact, dict) or not {"value", "official_url", "last_checked"} <= set(fact)
                        or not set(fact) <= {"value", "official_url", "last_checked", "currency", "requirement_type"}
                        or not _text(fact["value"], 500) or not _fresh(fact["last_checked"])
                        or fact["official_url"] not in {s["url"] for s in sources}
                        or ("currency" in fact and not _text(fact["currency"], 10))
                        or ("requirement_type" in fact and fact["requirement_type"] not in {"hard_requirement", "recommended", "preferred", "not_specified"})):
                    raise ValueError("invalid or expired delegated fact")
            records[row["record_key"]] = deepcopy(row)
        fields = catalog["fields"]
        receipt_rows = receipts["fields"]
        if not isinstance(fields, list) or not fields or not isinstance(receipt_rows, list) or len(fields) != len(receipt_rows):
            raise ValueError("missing delegated field receipts")
        by_id = {}
        for receipt in receipt_rows:
            if not isinstance(receipt, dict) or not isinstance(receipt.get("field_id"), str) or receipt["field_id"] in by_id:
                raise ValueError("duplicate delegated receipt")
            by_id[receipt["field_id"]] = receipt
        mapped = set()
        ids = set()
        page_pins = {}
        mappings = []
        for field in fields:
            keys = {"field_id", "record_key", "country", "university", "program", "intake_year", "intake_batch", "fact_key", "official_url", "body_sha256", "checked_at", "applicable_batch", "locator", "anchors"}
            if not isinstance(field, dict) or set(field) != keys or not _text(field["field_id"]) or field["field_id"] in ids:
                raise ValueError("invalid delegated mapping")
            ids.add(field["field_id"])
            if not isinstance(field["record_key"], str) or not isinstance(field["fact_key"], str):
                raise ValueError("invalid delegated field identity")
            row = records.get(field["record_key"], {})
            fact = row.get("facts", {}).get(field["fact_key"])
            if fact is None or any(row[k] != field[k] for k in ("university", "program", "country", "intake_year", "intake_batch")):
                raise ValueError("delegated field scope mismatch")
            pair = (field["record_key"], field["fact_key"])
            if pair in mapped or field["official_url"] != fact["official_url"] or field["checked_at"] != fact["last_checked"]:
                raise ValueError("conflicting delegated field mapping")
            mapped.add(pair)
            if not isinstance(field["body_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", field["body_sha256"]):
                raise ValueError("invalid delegated body hash")
            url = field["official_url"]
            if url in page_pins and page_pins[url] != field["body_sha256"]:
                raise ValueError("conflicting delegated page hash")
            page_pins[url] = field["body_sha256"]
            if not _text(field["locator"]) or field["applicable_batch"] != field["intake_batch"] or not isinstance(field["anchors"], list) or not 1 <= len(field["anchors"]) <= 8 or not all(_text(a) for a in field["anchors"]):
                raise ValueError("invalid delegated evidence locators")
            receipt = by_id.get(field["field_id"], {})
            receipt_keys = {"field_id", "record_key", "university", "program", "country", "intake_year", "intake_batch", "fact_key", "official_url", "body_sha256", "raw_sha256", "checked_at", "value_sha256", "verification_method", "table_alignment", "conflict_status"}
            if (set(receipt) != receipt_keys or any(receipt[k] != field[k] for k in receipt_keys & keys)
                    or receipt["value_sha256"] != _digest(fact["value"].encode())
                    or not isinstance(receipt["raw_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", receipt["raw_sha256"])
                    or receipt["verification_method"] != "OFFICIAL_BODY_PIN_AND_FIELD_MAPPING"
                    or receipt["table_alignment"] != "CHECKED_BY_CODEX"
                    or receipt["conflict_status"] != "NO_CONFLICT_IN_REGISTERED_SOURCE"):
                raise ValueError("invalid delegated verification receipt")
            mappings.append({**deepcopy(field), "official_value": fact["value"]})
        if mapped != {(r["record_key"], f) for r in rows for f in r["facts"]} or ids != set(by_id):
            raise ValueError("unverified delegated field")
        if len(page_pins) > 2:
            raise ValueError("delegated batch exceeds official page budget")
        self._rows = tuple(records.values())
        self._mappings = tuple(mappings)
        self.snapshot_digest = _digest(sr)
        self.source_version = snapshot["source_version"]
        self.provenance_secret = provenance_secret
        self.official_hosts = hosts
        self._urls = frozenset(page_pins)
        self.approval_status = "APPROVED"  # Existing internal adapter readiness flag, not manual review.

    def is_official_url(self, url):
        return _official_url(url, self.official_hosts) and url in self._urls

    def _fresh_mappings(self):
        if not all(_fresh(f["checked_at"]) for f in self._mappings):
            raise ValueError("delegated receipts expired; new official check required")
        return self._mappings

    @staticmethod
    def _page_matches(page, field):
        return (page["official_url"] == field["official_url"] and _fresh(page["checked_at"])
                and page["body_sha256"] == field["body_sha256"]
                and _digest(page["body"].encode()) == field["body_sha256"]
                and all(a in page["body"] for a in field["anchors"]))

    def search(self, query):
        if not isinstance(query, dict) or not valid_batch(query.get("intake_batch"), query.get("intake_year")):
            return []
        self._fresh_mappings()
        requested = query.get("requested_facts")
        if not isinstance(requested, list) or not requested or not all(isinstance(f, str) and f in REQUEST_KEYS for f in requested):
            raise ValueError("invalid delegated requested fields")
        selected = []
        pages = {}
        for row in self._rows:
            if any(row[k] != query[k] for k in ("intake_year", "intake_batch")):
                continue
            if query.get("country") and row["country"] != query["country"]:
                continue
            if any(query.get(q) and row[r].casefold() != query[q].strip().casefold() for q, r in (("university", "university"), ("program_or_major", "program"))):
                continue
            facts = {}
            for field in self._mappings:
                if field["record_key"] != row["record_key"] or field["fact_key"] not in {REQUEST_KEYS[f] for f in requested}:
                    continue
                url = field["official_url"]
                if url not in pages:
                    pages[url] = inspect_official_page(url, official_hosts=self.official_hosts)
                if not self._page_matches(pages[url], field):
                    raise ValueError("official body changed; mapping re-verification required")
                facts[field["fact_key"]] = {**row["facts"][field["fact_key"]], "last_checked": pages[url]["checked_at"]}
            if facts:
                selected.append({**deepcopy(row), "facts": facts, "approval_status": "APPROVED", "source_status": "OFFICIAL_VERIFIED"})
        return selected


class DelegatedOfficialProgramSource(PinnedOfficialProgramSource):
    """Exact batch/field/evidence ID binding and a fresh GET for claim checking."""

    authorization_mode = MODE

    def __init__(self, program_source):
        if not isinstance(program_source, DelegatedProgramSnapshot):
            raise ValueError("delegated Program source required")
        self.program_source = program_source
        self.official_hosts = program_source.official_hosts
        self.source_version = program_source.source_version
        self.approval_status = "APPROVED"

    def is_official_url(self, url):
        return self.program_source.is_official_url(url)

    def search_claims(self, claims, *, max_searches=2):
        if not _valid_claims(claims) or type(max_searches) is not int or not 1 <= max_searches <= 2:
            raise ValueError("invalid delegated claims")
        mappings = self.program_source._fresh_mappings()
        selected = []
        for claim in claims:
            for field in mappings:
                if (all(claim.get(k) == field[k] for k in ("university", "program", "intake_year", "intake_batch", "fact_key"))
                        and claim["program_evidence_id"] == program_evidence_id(self.program_source, field["record_key"])):
                    selected.append((claim, field))
        pages = {}
        facts = []
        for claim, field in selected:
            url = field["official_url"]
            if url not in pages and len(pages) < max_searches:
                pages[url] = inspect_official_page(url, official_hosts=self.official_hosts)
            if url in pages and self.program_source._page_matches(pages[url], field):
                facts.append({"claim_id": claim["claim_id"], **{k: field[k] for k in ("university", "program", "intake_year", "intake_batch", "fact_key", "official_url", "official_value")}, "checked_at": pages[url]["checked_at"], "source_kind": "OFFICIAL_PAGE_BODY"})
        return {"search_count": len(pages), "facts": facts}


def load_delegated_program_sources(directory, *, provenance_secret):
    program = DelegatedProgramSnapshot(directory, provenance_secret=provenance_secret)
    return program, DelegatedOfficialProgramSource(program)
