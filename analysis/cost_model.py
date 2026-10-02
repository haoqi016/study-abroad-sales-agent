"""Reproduce explicitly hypothetical B0/B1/B2 V2 cost scenarios.

This calculator has no network or model dependency. It never treats its inputs
as observations. All money is USD; all human time is seconds per enquiry.
"""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path


DEFAULT_INPUT = Path(__file__).with_name("cost_scenarios.json")


def _number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def calculate(scenario: dict) -> dict:
    if scenario.get("schema_version") != "v2-cost-scenario-1" or scenario.get("evidence_status") != "SCENARIO_ONLY":
        raise ValueError("input must be a versioned, explicitly hypothetical scenario")
    if scenario.get("currency") != "USD" or set(scenario.get("arms", {})) != {"B0", "B1", "B2"}:
        raise ValueError("input must include B0/B1/B2 and USD currency")
    prices = scenario["assumed_model_prices_usd_per_million_tokens"]
    input_price = _number(prices["input"], "input price")
    output_price = _number(prices["output"], "output price")
    arms = {}
    for name, arm in scenario["arms"].items():
        calls = arm["calls"]
        if name == "B0" and calls:
            raise ValueError("B0 must have zero model calls")
        input_tokens = sum(_number(c["input_tokens"], f"{name} input tokens") for c in calls)
        output_tokens = sum(_number(c["output_tokens"], f"{name} output tokens") for c in calls)
        model_usd = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
        human = arm["human_seconds"]
        if set(human) != {"review", "routine_correction", "escalation"}:
            raise ValueError(f"{name} needs all human handling categories")
        routine = sum(_number(value, f"{name} {key}") for key, value in human.items())
        rework_rate = _number(arm["rework_probability"], f"{name} rework probability")
        accepted_rate = _number(arm["accepted_action_probability"], f"{name} acceptance probability")
        if rework_rate > 1 or not 0 < accepted_rate <= 1:
            raise ValueError("probabilities must lie in their valid ranges")
        rework_seconds = rework_rate * _number(arm["rework_seconds_if_needed"], f"{name} rework seconds")
        arms[name] = {
            "assumed_call_count": len(calls),
            "assumed_input_tokens": input_tokens,
            "assumed_output_tokens": output_tokens,
            "assumed_model_usd_per_enquiry": model_usd,
            "assumed_routine_human_seconds_per_enquiry": routine,
            "assumed_expected_rework_seconds_per_enquiry": rework_seconds,
            "assumed_total_human_seconds_per_enquiry": routine + rework_seconds,
            "assumed_fixed_monthly_usd": _number(arm["fixed_monthly_usd"], f"{name} fixed cost"),
            "assumed_build_hours": _number(arm["build_hours"], f"{name} build hours"),
            "assumed_accepted_action_probability": accepted_rate,
        }
    tiers = []
    for tier in scenario["tiers"]:
        volume = _number(tier["monthly_enquiries"], "monthly enquiries", positive=True)
        if int(volume) != volume:
            raise ValueError("monthly enquiries must be a whole number")
        hourly = _number(tier["loaded_human_usd_per_hour"], "hourly labour", positive=True)
        months = _number(tier["build_amortization_months"], "amortization months", positive=True)
        rows = {}
        for name, arm in arms.items():
            api = volume * arm["assumed_model_usd_per_enquiry"]
            labour = volume * arm["assumed_total_human_seconds_per_enquiry"] * hourly / 3600
            fixed = arm["assumed_fixed_monthly_usd"]
            amortized_build = arm["assumed_build_hours"] * hourly / months
            total = api + labour + fixed + amortized_build
            rows[name] = {
                "assumed_monthly_model_usd": api,
                "assumed_monthly_human_usd": labour,
                "assumed_monthly_fixed_usd": fixed,
                "assumed_monthly_amortized_build_usd": amortized_build,
                "scenario_monthly_total_usd": total,
                "scenario_cost_per_enquiry_usd": total / volume,
                "scenario_cost_per_accepted_action_usd": total / (volume * arm["assumed_accepted_action_probability"]),
            }
        # Positive means B2 must save this many additional human seconds per
        # enquiry to match B1's modeled monthly total. Negative means it is
        # already below B1 on these assumptions.
        delta = rows["B2"]["scenario_monthly_total_usd"] - rows["B1"]["scenario_monthly_total_usd"]
        fixed_delta = (rows["B2"]["assumed_monthly_fixed_usd"] + rows["B2"]["assumed_monthly_amortized_build_usd"]
                       - rows["B1"]["assumed_monthly_fixed_usd"] - rows["B1"]["assumed_monthly_amortized_build_usd"])
        variable_delta = (arms["B2"]["assumed_model_usd_per_enquiry"] - arms["B1"]["assumed_model_usd_per_enquiry"]
                          + (arms["B2"]["assumed_total_human_seconds_per_enquiry"]
                             - arms["B1"]["assumed_total_human_seconds_per_enquiry"]) * hourly / 3600)
        break_even_volume = fixed_delta / -variable_delta if variable_delta < 0 and fixed_delta > 0 else None
        ten_point_b2_rework_swing = volume * 0.10 * scenario["arms"]["B2"]["rework_seconds_if_needed"] * hourly / 3600
        one_minute_b2_review_swing = volume * 60 * hourly / 3600
        doubled_model_price_swing = volume * (arms["B2"]["assumed_model_usd_per_enquiry"]
                                               - arms["B1"]["assumed_model_usd_per_enquiry"])
        tiers.append({
            "name": tier["name"], "status": "SCENARIO",
            "monthly_enquiries": int(volume), "loaded_human_usd_per_hour": hourly,
            "build_amortization_months": months, "arms": rows,
            "b2_minus_b1_monthly_usd": delta,
            "additional_b2_seconds_per_enquiry_to_break_even_with_b1": max(0, delta * 3600 / (volume * hourly)),
            "b2_minus_b1_cost_per_accepted_action_usd": rows["B2"]["scenario_cost_per_accepted_action_usd"] - rows["B1"]["scenario_cost_per_accepted_action_usd"],
            "b2_vs_b1_scenario_break_even_monthly_volume_at_this_hourly_rate": break_even_volume,
            "b2_minus_b1_sensitivity_usd_per_month": {
                "if_b2_rework_probability_rises_10_percentage_points": delta + ten_point_b2_rework_swing,
                "if_b2_review_takes_60_more_seconds_per_enquiry": delta + one_minute_b2_review_swing,
                "if_both_arms_model_prices_double": delta + doubled_model_price_swing,
            },
        })
    return {"status": "SCENARIO_ONLY", "currency": "USD", "arms": arms, "tiers": tiers}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, help="optional JSON output file; stdout by default")
    args = parser.parse_args()
    result = json.dumps(calculate(json.loads(args.input.read_text(encoding="utf-8"))), indent=2) + "\n"
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
