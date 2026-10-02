"""Run public first turns through the real offline workspace and V2 gates.

This is a scripted wiring probe. The canned provider always attempts one
clarifying question; it is deliberately not a model or a Gold-derived answer.
"""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from evals.evaluate import DEFAULT_DATA, candidate_input, validate_dataset
from sales_agent.v2_offline_api import OfflineOnlyError, OfflineWorkspaceApi
from sales_agent.v2_pipeline.policy import PRICE_POLICY_VERSION
from sales_agent.v2_pipeline.providers import LocalOllamaJSONProvider


SCRIPT_VERSION = "always-clarify-wiring-v1"
QUESTION = "What would you like me to clarify first?"


def canned_script(inbound_id: str, raw: str) -> dict:
    """Build only from visible input and a fresh inbound event ID."""
    return {
        "demo_only": True,
        "tool_plan": {"requests": []},
        "decision": {
            "normalized_meaning": "The student sent a message about study abroad support.",
            "normalized_meaning_spans": [raw],
            "hypotheses": [{"code": "NEEDS_CLARIFICATION", "confidence": 0.5,
                            "supporting_spans": [raw], "contradicting_evidence": []}],
            "unknowns": ["Which detail the student wants addressed first"],
            "selected_strategy": {"strategy_code": "CLARIFY",
                                  "reason": "Use one narrow clarification question in this fixed wiring probe",
                                  "alternatives_rejected": ["Make an unsupported service claim"]},
            "previous_objective_assessment": {"objective_id": None, "result": None,
                                              "observation_event_ids": [inbound_id], "reason": None},
            "current_objective": {"objective_id": "scripted-first-turn-clarify",
                                  "previous_objective_id": None,
                                  "goal": "Find the first detail to clarify",
                                  "why_now": "An inbound message was received",
                                  "trigger_event_ids": [inbound_id], "status": "ACTIVE",
                                  "success_signals": ["The student identifies a detail"],
                                  "failure_signals": ["The student asks to stop"],
                                  "attempt_count": 1, "max_attempts": 2},
            "action_plan": {"selected_action": "Ask one clarifying question",
                            "expected_observation": "The student identifies a detail",
                            "fallback_if_blocked": "Refer to a human reviewer"},
            "offer": {"state": "NONE", "offer_id": None, "offer_version": None,
                      "proposal": None},
            "content_contract": {"must_include": [], "may_include": [],
                                 "must_not_include": [], "semantic_draft": QUESTION,
                                 "desired_next_step": "The student names a detail",
                                 "communication_emphasis": "BALANCED"},
            "evidence_ids": [], "program_claims": [], "stop_required": False,
            "handoff_reason": None, "human_approval_required": True,
            "memory_candidates": [],
        },
        "conversation": {"messages": [{"type": "text", "content": QUESTION}],
                         "style_transformations": []},
    }


def _event_ids(events: list[dict], kind: str) -> list[str]:
    return [e["event_id"] for e in events if e["event_type"] == kind]


def run_case(case: dict, *, local_model: str | None = None,
             timeout_seconds: float = 180.0) -> dict:
    visible = candidate_input(case)
    gap = []
    if visible["evidence"]:
        gap.append("PUBLIC_EVIDENCE_NOT_INJECTED_INTO_RUNTIME_TOOLS")
    if visible["prior_actual_sales_messages"]:
        gap.append("PRIOR_ACTUAL_SEND_HISTORY_NOT_REPLAYED")
    started = perf_counter()
    row = {"case_id": case["case_id"], "split": case["split"],
           "fixture_gap_codes": gap, "continuation_executed": False,
           "script_version": SCRIPT_VERSION if local_model is None else None,
           "provider_kind": "DETERMINISTIC_CANNED" if local_model is None else "LOCAL_OLLAMA",
           "candidate_input_sha256": sha256(json.dumps(visible, sort_keys=True,
                                                       ensure_ascii=False).encode("utf-8")).hexdigest(),
           "candidate_action_label": None, "action_label_match": None,
           "gold_scoring_eligible": False, "permission_safety_observation": None}
    try:
        provider_factory = (None if local_model is None else
                            lambda: LocalOllamaJSONProvider(model=local_model,
                                                            timeout_seconds=timeout_seconds))
        with OfflineWorkspaceApi(local_provider_factory=provider_factory) as api:
            created = api.createStudent({"demo_only": True,
                                         "display_name": "Demo Evaluation Student",
                                         "sales": {"contact_permission": visible["contact_permission"]}})
            sid = created["student_id"]
            workspace = api.recordInbound(sid, {"demo_only": True,
                                                "raw_text": visible["student_message"]})
            inbound = _event_ids(workspace["events"], "INBOUND_RECEIVED")[-1]
            if local_model is None and visible["contact_permission"] != "DO_NOT_CONTACT":
                api.queue_script(sid, canned_script(inbound, visible["student_message"]))
            try:
                outcome = api.requestDecision(sid)
                pipeline = outcome["pipeline"]
                workspace = outcome["workspace"]
                row["execution_status"] = pipeline["status"]
                row["reason_codes"] = pipeline["reason_codes"]
                row["pipeline_event_ids"] = {key: pipeline.get(key) for key in
                                             ("context_event_id", "decision_event_id",
                                              "draft_event_id", "intervention_event_id")}
            except OfflineOnlyError as exc:
                workspace = api.getWorkspace(sid)
                if visible["contact_permission"] == "DO_NOT_CONTACT" and str(exc) == "do_not_contact":
                    row["execution_status"] = "ADAPTER_BLOCKED"
                    row["reason_codes"] = ["do_not_contact"]
                else:
                    row["execution_status"] = "EXECUTION_ERROR"
                    row["reason_codes"] = ["UNEXPECTED_ADAPTER_ERROR", str(exc)]
                row["pipeline_event_ids"] = {}
            except Exception as exc:
                workspace = api.getWorkspace(sid)
                row["execution_status"] = "EXECUTION_ERROR"
                row["reason_codes"] = ["REQUEST_DECISION_EXCEPTION", type(exc).__name__]
                row["pipeline_event_ids"] = {}
            events = workspace["events"]
            row["event_lineage"] = {kind: _event_ids(events, kind) for kind in
                                    ("INBOUND_RECEIVED", "TURN_CONTEXT_BUILT",
                                     "DECISION_READY", "GATE_RESULT", "DRAFTED",
                                     "INTERVENTION_DECISION", "HUMAN_SENT")}
            row["inbound_event_id"] = inbound
            row["gates"] = [{"event_id": e["event_id"],
                             "subject_event_id": e["payload"]["subject_event_id"],
                             "stage": e["payload"]["gate_stage"],
                             "passed": e["payload"]["passed"],
                             "required_action": e["payload"]["required_action"],
                             "hard_violations": e["payload"]["hard_violations"]}
                            for e in events if e["event_type"] == "GATE_RESULT"]
            row["draft_created"] = bool(workspace["drafts"])
            row["draft_status"] = workspace["draft_status"]
            row["actual_send_events"] = len(workspace["sent"])
            row["runtime_contact_permission"] = workspace["sales"]["contact_permission"]
            if visible["contact_permission"] == "UNKNOWN" and row["execution_status"] == "HANDOFF" and "CONTACT_PERMISSION_UNKNOWN" in row["reason_codes"]:
                row["permission_safety_observation"] = "UNKNOWN_PERMISSION_HANDOFF"
            if visible["contact_permission"] == "DO_NOT_CONTACT" and row["execution_status"] == "ADAPTER_BLOCKED" and "do_not_contact" in row["reason_codes"]:
                row["permission_safety_observation"] = "DO_NOT_CONTACT_BLOCKED"
            decisions = [e for e in events if e["event_type"] == "DECISION_READY"]
            if decisions:
                decision = decisions[-1]["payload"]
                row["offer_state"] = decision["offer"]["state"]
                row["human_approval_required"] = decision["human_approval_required"]
                row["strategy_code"] = decision["selected_strategy"]["strategy_code"]
                # Only this exact scripted strategy has a declared label map.
                if local_model is None and row["strategy_code"] == "CLARIFY" and not decision["stop_required"]:
                    row["candidate_action_label"] = "CLARIFY"
                    row["gold_scoring_eligible"] = not gap
                    if not gap:
                        row["action_label_match"] = "CLARIFY" in case["gold"]["acceptable_actions"]
            else:
                row["offer_state"] = None
                row["human_approval_required"] = None
                row["strategy_code"] = None
    except Exception as exc:
        row["execution_status"] = "HARNESS_ERROR"
        row["reason_codes"] = [type(exc).__name__, str(exc)]
        row.setdefault("event_lineage", {})
        row.setdefault("gates", [])
        row.setdefault("draft_created", False)
        row.setdefault("actual_send_events", 0)
    row["latency_ms"] = round((perf_counter() - started) * 1000, 2)
    return row


def run_all(data: dict) -> dict:
    errors = validate_dataset(data)
    if errors:
        raise ValueError("Dataset validation failed: " + "; ".join(errors))
    rows = [run_case(case) for case in data["cases"]]
    summary = {}
    for split in ("development", "challenge", "all"):
        group = [r for r in rows if split == "all" or r["split"] == split]
        eligible = [r for r in group if r["gold_scoring_eligible"]]
        summary[split] = {
            "attempted": len(group),
            "runtime_decision_recorded": sum(bool(r["event_lineage"].get("DECISION_READY")) for r in group),
            "draft_created": sum(r["draft_created"] for r in group),
            "fixture_gap": sum(bool(r["fixture_gap_codes"]) for r in group),
            "adapter_blocked": sum(r["execution_status"] == "ADAPTER_BLOCKED" for r in group),
            "execution_error": sum(r["execution_status"] == "EXECUTION_ERROR" for r in group),
            "harness_error": sum(r["execution_status"] == "HARNESS_ERROR" for r in group),
            "human_semantic_reviewed": 0,
            "action_label_eligible": len(eligible),
            "action_label_match": sum(r["action_label_match"] is True for r in eligible),
            "unknown_permission_handoff": sum(r.get("permission_safety_observation") == "UNKNOWN_PERMISSION_HANDOFF" for r in group),
            "do_not_contact_blocked": sum(r.get("permission_safety_observation") == "DO_NOT_CONTACT_BLOCKED" for r in group),
            "status_counts": dict(Counter(r["execution_status"] for r in group)),
        }
    return {"evaluation_type": "SCRIPTED_V2_FIRST_TURN_WIRING",
            "candidate": SCRIPT_VERSION, "dataset_status": data["status"],
            "policy_version": PRICE_POLICY_VERSION,
            "harness_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "dataset_sha256": sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "summary": summary, "per_case": rows,
            "model_quality": "NOT_MEASURED", "business_effect": "NOT_MEASURED",
            "human_semantic_review": "PENDING", "second_turn": "NOT_EXECUTED",
            "fixture_parity": "INCOMPLETE_FOR_EVIDENCE_CASES",
            "warning": "Canned decision quality is not V2 model quality. Gate pass checks bounded policy, not usefulness; all drafts remain unsent."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "results/v2_first_turn_scripted.json")
    args = parser.parse_args()
    report = run_all(json.loads(args.dataset.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
