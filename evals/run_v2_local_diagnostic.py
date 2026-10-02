"""Run exactly two public synthetic first turns through local Ollama V2.

This bounded diagnostic is separate from the 24-case scripted wiring probe.
No Gold or continuation is sent to the model; no retries are made here.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen

from evals.evaluate import DEFAULT_DATA, validate_dataset
from evals.run_v2_first_turn import run_case
from sales_agent.v2_pipeline.policy import PRICE_POLICY_VERSION
from sales_agent.v2_pipeline.providers import LocalOllamaJSONProvider


MODEL = "qwen3.5:9b"
EXPECTED_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
TIMEOUT_SECONDS = 90.0
CASE_IDS = ("DEV-OFFER-01", "DEV-INTENT-01")
OUTPUT = Path(__file__).resolve().parent / "results/v2_local_two_case_diagnostic.json"


def main() -> None:
    data = json.loads(DEFAULT_DATA.read_text(encoding="utf-8"))
    errors = validate_dataset(data)
    if errors:
        raise SystemExit("Dataset invalid: " + "; ".join(errors))
    selected = {c["case_id"]: c for c in data["cases"] if c["case_id"] in CASE_IDS}
    if set(selected) != set(CASE_IDS):
        raise SystemExit("Selected diagnostic cases missing")
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
        installed = json.load(response)["models"]
    digest = next((item["digest"] for item in installed if item["name"] == MODEL), None)
    if digest != EXPECTED_DIGEST:
        raise SystemExit("Local model digest differs from the frozen diagnostic specification")
    rows = [run_case(selected[case_id], local_model=MODEL,
                     timeout_seconds=TIMEOUT_SECONDS) for case_id in CASE_IDS]
    report = {
        "evaluation_type": "TWO_CASE_LOCAL_MODEL_DIAGNOSTIC",
        "model": MODEL, "model_digest": digest,
        "prompt_version": LocalOllamaJSONProvider.PROMPT_VERSION,
        "timeout_seconds_per_provider_call": TIMEOUT_SECONDS,
        "provider_retry_policy": "No harness retry; pipeline tool retry behavior unchanged",
        "pricing_policy_version": PRICE_POLICY_VERSION,
        "dataset_sha256": sha256(json.dumps(data, sort_keys=True,
                                            ensure_ascii=False).encode("utf-8")).hexdigest(),
        "runtime_harness_sha256": sha256((Path(__file__).parent / "run_v2_first_turn.py").read_bytes()).hexdigest(),
        "selected_case_ids": list(CASE_IDS),
        "summary": {"attempted": len(rows),
                    "decision_recorded": sum(bool(r["event_lineage"].get("DECISION_READY")) for r in rows),
                    "draft_created": sum(r["draft_created"] for r in rows),
                    "failure_or_handoff": sum(r["execution_status"] not in {"REVIEW_REQUIRED", "APPROVAL_REQUIRED", "STOPPED"} for r in rows),
                    "status_counts": dict(Counter(r["execution_status"] for r in rows))},
        "per_case": rows,
        "action_label_score": "NOT_COMPUTED_NO_FAITHFUL_STRATEGY_MAP",
        "semantic_review": "PENDING", "second_turn": "NOT_EXECUTED",
        "business_effect": "NOT_MEASURED",
        "warning": "Two author-exposed synthetic cases are a local model diagnostic, not a 24-case V2 score. Gate pass does not establish response usefulness or safe autonomous sending.",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
