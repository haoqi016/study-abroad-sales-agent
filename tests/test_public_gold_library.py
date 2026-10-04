"""Boundaries for the public, synthetic development examples."""

import json
import re
import unittest

from evals.evaluate import DEFAULT_DATA
from sales_agent.v2_pipeline import public_gold


class PublicGoldLibraryTests(unittest.TestCase):
    def test_examples_are_english_synthetic_development_material(self):
        evaluation_ids = {
            case["case_id"] for case in json.loads(DEFAULT_DATA.read_text())["cases"]
        }
        for kind in ("decision", "language"):
            catalog = public_gold.catalog(kind)
            self.assertEqual(catalog["split"], "development")
            self.assertEqual(catalog["source_type"], "SYNTHETIC_PUBLIC_EXAMPLE")
            self.assertTrue(catalog["entries"])
            for example in catalog["entries"]:
                self.assertNotIn(example["gold_id"], evaluation_ids)
                self.assertEqual(example["kind"], kind)
                self.assertNotIn("cues", example)
                serialized = json.dumps(example, ensure_ascii=False)
                self.assertIsNone(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", serialized))
                self.assertIsNone(re.search(r"\b[a-f0-9]{64}\b", serialized))
                self.assertNotIn("list_price", serialized)
                self.assertNotIn("approved_offer", serialized)

    def test_selection_is_bounded_and_does_not_treat_unknown_as_a_match(self):
        selected = public_gold.select("decision", "What is included in support?")
        self.assertEqual(selected["status"], "MATCH")
        self.assertEqual(selected["gold_ids"], ["PUBLIC-SCOPE-01"])
        unknown = public_gold.select("decision", "Can you confirm the deadline?")
        self.assertEqual(unknown["status"], "NO_MATCH")
        self.assertEqual(unknown["gold_ids"], [])
        self.assertEqual(public_gold.select("language", "A generic question")["status"], "MATCH")

    def test_catalog_and_get_return_copies(self):
        first = public_gold.catalog("decision")
        first["entries"][0]["decision_move"] = "tampered"
        self.assertNotEqual(public_gold.catalog("decision")["entries"][0]["decision_move"], "tampered")
        example = public_gold.get("PUBLIC-SCOPE-01", "decision")
        example["decision_move"] = "tampered"
        self.assertNotEqual(public_gold.get("PUBLIC-SCOPE-01", "decision")["decision_move"], "tampered")
        with self.assertRaises(ValueError):
            public_gold.get("DEV-FLOW-01", "decision")

    def test_style_review_reports_risks_without_claiming_approval(self):
        language = public_gold.get("PUBLIC-LANGUAGE-01", "language")
        review = public_gold.style_review(
            [{"type": "text", "content": "Regarding your question, " + "x" * 181}], language
        )
        self.assertEqual(review["status"], "REVISE")
        self.assertEqual({issue["code"] for issue in review["issues"]},
                         {"FORMAL_OPENING", "LONG_MESSAGE"})
        self.assertNotIn("approved", review)


if __name__ == "__main__":
    unittest.main()
