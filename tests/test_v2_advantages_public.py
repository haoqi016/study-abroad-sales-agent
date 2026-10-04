"""Synthetic checks for the approved advantage evidence boundary."""

import unittest

from sales_agent.v2_tools.advantages import select_approved_advantages


class PublicAdvantageBoundaryTests(unittest.TestCase):
    def test_case_tag_requires_host_approved_case_evidence(self):
        blocked = select_approved_advantages(
            region=None, concern_tags=["CASE_EVIDENCE"])
        self.assertEqual(blocked["status"], "NO_RESULTS")
        self.assertEqual(blocked["data"], [])

        allowed = select_approved_advantages(
            region=None, concern_tags=["CASE_EVIDENCE"],
            approved_case_evidence=True)
        self.assertEqual(allowed["status"], "OK")
        self.assertEqual({item["advantage_id"] for item in allowed["data"]},
                         {"ADV-001", "ADV-007"})
        self.assertTrue(all("CASE_EVIDENCE" in item["tags"] for item in allowed["data"]))

    def test_unrelated_approved_card_remains_available_without_case_evidence(self):
        result = select_approved_advantages(
            region=None, concern_tags=["PERSONALIZATION"])
        self.assertEqual(result["status"], "OK")
        self.assertEqual([item["advantage_id"] for item in result["data"]], ["ADV-004"])
        self.assertTrue(all("CASE_EVIDENCE" not in item["tags"] for item in result["data"]))


if __name__ == "__main__":
    unittest.main()
