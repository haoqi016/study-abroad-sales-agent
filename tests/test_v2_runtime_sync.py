"""Synthetic checks for explicit V2 runtime sources and remote provider boundaries."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sales_agent.v2_offline_api import OfflineOnlyError, OfflineWorkspaceApi
from sales_agent.v2_pipeline import OpenAICompatibleProvider, ProviderError
from sales_agent.v2_tools.offline_public_cases import OfflinePublicCasesSource
from web.bridge_server import load_api_config, load_private_secret


class RuntimeSyncTests(unittest.TestCase):
    def test_private_config_and_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "provider.json"
            config.write_text(json.dumps({"base_url": "https://model.example.test/v1",
                                          "model": "synthetic-model", "api_key": "synthetic-key"}))
            config.chmod(0o600)
            self.assertEqual(load_api_config(config)["model"], "synthetic-model")
            config.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "owner_only"):
                load_api_config(config)
            secret = root / "audit.key"
            secret.write_bytes(b"synthetic-audit-key-32-bytes-abcdef")
            secret.chmod(0o600)
            self.assertEqual(load_private_secret(secret), b"synthetic-audit-key-32-bytes-abcdef")

    def test_remote_cases_require_both_opt_ins(self):
        with tempfile.TemporaryDirectory() as directory:
            source = OfflinePublicCasesSource(path=Path(directory) / "synthetic.sqlite",
                                              expected_sha256="0" * 64)
            factory = lambda: OpenAICompatibleProvider(base_url="https://model.example.test/v1",
                                                        model="synthetic-model", api_key="synthetic-key")
            with self.assertRaisesRegex(OfflineOnlyError, "explicit_api_opt_in"):
                OfflineWorkspaceApi(local_provider_factory=factory, provider_mode="REMOTE_API",
                                    offline_public_cases_source=source)
            with self.assertRaisesRegex(OfflineOnlyError, "explicit_api_opt_in"):
                OfflineWorkspaceApi(local_provider_factory=factory, provider_mode="REMOTE_API",
                                    allow_remote_public_cases=True)
            with OfflineWorkspaceApi(local_provider_factory=factory, provider_mode="REMOTE_API",
                                     offline_public_cases_source=source,
                                     allow_remote_public_cases=True) as api:
                self.assertEqual(api.agent_mode, "REMOTE_API")

    def test_remote_provider_rejects_bad_endpoint_and_duplicate_json(self):
        for endpoint in ("http://model.example.test/v1", "https://user@model.example.test/v1",
                         "https://model.example.test/v1?secret=x"):
            with self.assertRaises(ValueError):
                OpenAICompatibleProvider(base_url=endpoint, model="synthetic")
        provider = OpenAICompatibleProvider(base_url="https://model.example.test/v1",
                                            model="synthetic", api_key="synthetic-key")

        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, *_args):
                return json.dumps({"choices": [{"message": {"content": '{"x":1,"x":2}'}}]}).encode()

        with patch("sales_agent.v2_pipeline.providers.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(ProviderError):
                provider.generate_json("review_reflection", {})
        class ValidResponse(FakeResponse):
            def read(self, *_args):
                return json.dumps({"choices": [{"message": {"content": '{"requests":[]}'}}]}).encode()

        with patch("sales_agent.v2_pipeline.providers.request.urlopen", return_value=ValidResponse()) as send:
            self.assertEqual(provider.generate_json("tool_plan", {"turn_context": {}}), {"requests": []})
        submitted = json.loads(send.call_args.args[0].data)
        self.assertIn("sales-tools.v1.1", submitted["messages"][0]["content"])
        self.assertNotIn("synthetic-key", json.dumps(submitted))

    def test_synthetic_case_source_emits_only_coarse_english_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.sqlite"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE cases (id INTEGER PRIMARY KEY, dedupe_key TEXT, country TEXT, "
                           "institution TEXT, program_name TEXT, decision TEXT, payload_json TEXT, source_url TEXT)")
                for index in range(3):
                    db.execute("INSERT INTO cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (index + 1, f"synthetic-{index}", "Singapore", "National University of Singapore",
                                "Computer Science", "offer",
                                json.dumps({"synthetic_flag": True,
                                            "source_quality": "traceable_structured",
                                            "applicant_profile": {"university_tier": "985", "gpa": 86,
                                                                  "undergraduate_major": "Computer Science"},
                                            "private_note": "Must not leave the source"}),
                                "https://m.compassedu.hk/synthetic/example"))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            source = OfflinePublicCasesSource(path=path, expected_sha256=digest, secret=b"s" * 32)
            request = {"contract_version": "sales-tools.v1", "retrieval_mode": "SIMILAR_BACKGROUND",
                       "student_profile": {"undergraduate_university_raw": "Synthetic College",
                                           "undergraduate_major_raw": "Computer Science", "average_score": 86,
                                           "target_country": "SG", "target_university": None,
                                           "target_program_or_major": None, "fact_status": "CRM_CONFIRMED"},
                       "comparison_dimensions": ["score_band", "major_family"], "limit": 2}
            result = source.search(request)
            self.assertEqual(result["status"], "OK")
            rendered = json.dumps(result)
            self.assertNotIn("private_note", rendered)
            self.assertNotIn("Must not leave", rendered)
            self.assertNotIn("compassedu.hk", rendered)
            self.assertIn("not an institution-served case", rendered)
            with sqlite3.connect(path) as db:
                db.execute("UPDATE cases SET payload_json = json_set(payload_json, '$.synthetic_flag', 0)")
            non_synthetic = OfflinePublicCasesSource(path=path,
                expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), secret=b"s" * 32)
            self.assertEqual(non_synthetic.search(request)["status"], "NO_RESULTS")


if __name__ == "__main__":
    unittest.main()
