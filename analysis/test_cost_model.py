"""Focused checks of scenario arithmetic and evidence labeling."""

import copy
import json
from pathlib import Path
import unittest

from analysis.cost_model import calculate


SCENARIO = json.loads(Path(__file__).with_name("cost_scenarios.json").read_text(encoding="utf-8"))


class CostModelTests(unittest.TestCase):
    def test_zero_call_baseline_and_three_call_v2(self):
        result = calculate(SCENARIO)
        self.assertEqual(result["status"], "SCENARIO_ONLY")
        self.assertEqual(result["arms"]["B0"]["assumed_model_usd_per_enquiry"], 0)
        self.assertEqual(result["arms"]["B2"]["assumed_call_count"], 3)
        self.assertAlmostEqual(result["arms"]["B2"]["assumed_model_usd_per_enquiry"], 0.00434)

    def test_base_monthly_total_reconciles_components(self):
        row = calculate(SCENARIO)["tiers"][1]["arms"]["B2"]
        self.assertAlmostEqual(row["scenario_monthly_total_usd"], sum(
            row[key] for key in ("assumed_monthly_model_usd", "assumed_monthly_human_usd",
                            "assumed_monthly_fixed_usd", "assumed_monthly_amortized_build_usd")))
        self.assertAlmostEqual(row["scenario_monthly_total_usd"], 594.2013333333333)

    def test_increased_rework_and_review_cost_raise_b2_gap(self):
        base = calculate(SCENARIO)["tiers"][1]
        stressed = base["b2_minus_b1_sensitivity_usd_per_month"]
        self.assertGreater(stressed["if_b2_rework_probability_rises_10_percentage_points"], base["b2_minus_b1_monthly_usd"])
        self.assertGreater(stressed["if_b2_review_takes_60_more_seconds_per_enquiry"], base["b2_minus_b1_monthly_usd"])

    def test_unlabelled_and_invalid_inputs_fail(self):
        bad = copy.deepcopy(SCENARIO)
        bad["evidence_status"] = "MEASURED"
        with self.assertRaises(ValueError):
            calculate(bad)
        bad = copy.deepcopy(SCENARIO)
        bad["arms"]["B2"]["rework_probability"] = 1.1
        with self.assertRaises(ValueError):
            calculate(bad)


if __name__ == "__main__":
    unittest.main()
