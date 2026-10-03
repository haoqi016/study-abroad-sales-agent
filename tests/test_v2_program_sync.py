"""Synthetic Program batch and official evidence boundary checks."""

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sales_agent.v2_tools.delegated_program import load_delegated_program_sources
from sales_agent.v2_tools.common import safe_public_text
from sales_agent.v2_tools.program_batch import bind_program_batch
from sales_agent.v2_tools.programs import get_program_evidence, program_evidence_id
from sales_agent.v2_tools.verify import verify_program_claims


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ProgramSyncTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = datetime.now(timezone.utc).isoformat()
        self.url = "https://university.example.edu/synthetic/fees"
        self.body = "Synthetic University Example MSc AY2027/28 tuition 100 credits"
        self.page = {"official_url": self.url, "body": self.body,
                     "body_sha256": digest(self.body.encode()),
                     "raw_sha256": digest(self.body.encode()), "checked_at": self.now}
        self.row = {"record_key": "synthetic-example", "university": "Synthetic University",
                    "program": "Example MSc", "country": "SG", "intake_year": 2027,
                    "intake_batch": "AY2027/28",
                    "official_sources": [{"label": "Synthetic fixture", "url": self.url}],
                    "facts": {"tuition": {"value": "100 credits", "official_url": self.url,
                                          "last_checked": self.now, "currency": "SGD"}}}
        self.snapshot = {"schema_version": "sales-program-snapshot.v2",
                         "source_version": "synthetic-test-v1", "records": [self.row]}
        self.field = {"field_id": "synthetic-tuition", "record_key": self.row["record_key"],
                      **{key: self.row[key] for key in ("country", "university", "program", "intake_year", "intake_batch")},
                      "fact_key": "tuition", "official_url": self.url,
                      "body_sha256": self.page["body_sha256"], "checked_at": self.now,
                      "applicable_batch": "AY2027/28", "locator": "Synthetic fee line",
                      "anchors": ["AY2027/28", "100 credits"]}
        self.catalog = {"schema_version": "sales-official-catalog.v2",
                        "source_version": "synthetic-test-v1", "snapshot_sha256": "",
                        "normalizer": "html-body-text.v1", "fields": [self.field]}
        self.receipt = {**{key: self.field[key] for key in (
            "field_id", "record_key", "university", "program", "country", "intake_year",
            "intake_batch", "fact_key", "official_url", "body_sha256", "checked_at")},
            "raw_sha256": self.page["raw_sha256"], "value_sha256": digest(b"100 credits"),
            "verification_method": "OFFICIAL_BODY_PIN_AND_FIELD_MAPPING",
            "table_alignment": "CHECKED_BY_CODEX", "conflict_status": "NO_CONFLICT_IN_REGISTERED_SOURCE"}
        self.receipts = {"schema_version": "sales-official-verification-receipts.v1",
                         "snapshot_sha256": "", "catalog_sha256": "",
                         "generated_by": "CODEX_OFFICIAL_RESEARCH", "fields": [self.receipt]}
        self.auth = {"schema_version": "sales-program-authorization.v2",
                     "authorization_mode": "OWNER_DELEGATED_OFFICIAL_VERIFICATION",
                     "approval_status": "APPROVED", "authorized_by": "SYNTHETIC_OWNER",
                     "authorized_at": self.now,
                     "scope": "CURRENT_OFFICIAL_EXACT_PROGRAM_AND_BATCH_FACTS_ONLY",
                     "official_hosts": ["university.example.edu"],
                     "snapshot_sha256": "", "catalog_sha256": "", "receipts_sha256": "",
                     "max_check_age_hours": 24, "manual_owner_field_review": False,
                     "school_confirmed_mapping": False}
        self.save()

    def write(self, name, value):
        raw = json.dumps(value, sort_keys=True).encode()
        (self.root / name).write_bytes(raw)
        return digest(raw)

    def save(self):
        snapshot_sha = self.write("snapshot.delegated.json", self.snapshot)
        self.catalog["snapshot_sha256"] = snapshot_sha
        catalog_sha = self.write("official-catalog.delegated.json", self.catalog)
        self.receipts.update(snapshot_sha256=snapshot_sha, catalog_sha256=catalog_sha)
        receipts_sha = self.write("verification-receipts.delegated.json", self.receipts)
        self.auth.update(snapshot_sha256=snapshot_sha, catalog_sha256=catalog_sha,
                         receipts_sha256=receipts_sha)
        self.write("authorization.delegated.json", self.auth)

    def load(self):
        return load_delegated_program_sources(
            self.root, provenance_secret=b"synthetic-test-only-secret-32-bytes!")

    def request(self):
        return {"contract_version": "sales-tools.v1.1",
                "query": {"country": "SG", "university": "Synthetic University",
                          "program_or_major": "Example MSc", "intake_year": 2027,
                          "intake_batch": "AY2027/28", "requested_facts": ["tuition"]},
                "applicant_context": {"score_band": None, "degree_background": None}}

    def claim(self, source):
        return {"claim_id": "synthetic-claim", "university": "Synthetic University",
                "program": "Example MSc", "intake_year": 2027, "intake_batch": "AY2027/28",
                "fact_key": "tuition", "expected_value": "100 credits",
                "program_evidence_id": program_evidence_id(source, self.row["record_key"])}

    def test_exact_batch_projects_only_after_live_body_check(self):
        source, official = self.load()
        with patch("sales_agent.v2_tools.delegated_program.inspect_official_page",
                   return_value=deepcopy(self.page)) as fetch:
            result = get_program_evidence(self.request(), source=source)
            verification = verify_program_claims([self.claim(source)], official_source=official)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["tool_version"], "sales-tools.v1.1")
        self.assertEqual(result["data"][0]["intake_batch"], "AY2027/28")
        self.assertEqual(verification["data"][0]["status"], "VERIFIED")
        self.assertEqual(fetch.call_count, 2)

    def test_batch_absent_mismatch_and_unconfirmed_snapshot_do_not_fetch(self):
        source, _ = self.load()
        request = self.request()
        legacy = deepcopy(request)
        legacy["contract_version"] = "sales-tools.v1"
        del legacy["query"]["intake_batch"]
        wrong = deepcopy(request)
        wrong["query"]["intake_batch"] = "START2027-01-01"
        snapshot = {"snapshot": {"education": {"intake_year": 2027, "intake_batch": "AY2027/28"}},
                    "field_provenance": {"education." + key: {"source_type": "SALESPERSON_INPUT",
                                                               "confirmed": True}
                                         for key in ("intake_year", "intake_batch")}}
        self.assertEqual(bind_program_batch(request, snapshot), request)
        snapshot["field_provenance"]["education.intake_batch"]["confirmed"] = False
        self.assertIsNone(bind_program_batch(request, snapshot))
        with patch("sales_agent.v2_tools.delegated_program.inspect_official_page") as fetch:
            self.assertEqual(get_program_evidence(legacy, source=source)["status"], "NO_RESULTS")
            self.assertEqual(get_program_evidence(wrong, source=source)["status"], "NO_RESULTS")
        fetch.assert_not_called()

    def test_expired_and_changed_evidence_fails_closed(self):
        source, official = self.load()
        changed = {**self.page, "body": self.body + " changed"}
        changed["body_sha256"] = digest(changed["body"].encode())
        with patch("sales_agent.v2_tools.delegated_program.inspect_official_page",
                   return_value=changed):
            self.assertEqual(get_program_evidence(self.request(), source=source)["status"], "UNAVAILABLE")
            check = verify_program_claims([self.claim(source)], official_source=official)
            self.assertEqual(check["data"][0]["status"], "UNVERIFIED")
        stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.row["facts"]["tuition"]["last_checked"] = stale
        self.field["checked_at"] = stale
        self.receipt["checked_at"] = stale
        self.save()
        with self.assertRaises(ValueError):
            self.load()

    def test_scope_or_digest_tampering_rejected(self):
        self.field["applicable_batch"] = "AY2028/29"
        self.save()
        with self.assertRaises(ValueError):
            self.load()
        self.field["applicable_batch"] = "AY2027/28"
        self.save()
        self.row["facts"]["tuition"]["value"] = "200 credits"
        self.save()
        with self.assertRaises(ValueError):
            self.load()

    def test_unicode_escaped_private_labels_still_block_public_text(self):
        labels = ("\u5fae\u4fe1", "\u624b\u673a\u53f7", "\u8eab\u4efd\u8bc1",
                  "\u5ba2\u6237\u7f16\u53f7", "\u59d3\u540d")
        for label in labels:
            with self.subTest(label=label):
                self.assertFalse(safe_public_text(label + ": synthetic value"))
                self.assertFalse(safe_public_text(label + "\uFF1A synthetic value"))
        self.assertTrue(safe_public_text("Synthetic University"))


if __name__ == "__main__":
    unittest.main()
