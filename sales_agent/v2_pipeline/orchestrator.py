"""Offline Sales V2 orchestration: evidence -> decision -> gates -> draft.

This module has no channel sender. Only V2RuntimeStore.record_sent, called by
an authenticated human-facing service, can record what was actually sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any

from sales_agent.v2_runtime import ContractViolation, V2RuntimeStore
from sales_agent.v2_tools import (
    get_program_evidence, get_sales_methodologies, program_freshness_actions,
    search_anonymized_cases, select_approved_advantages, verify_program_claims,
)
from sales_agent.v2_tools.common import envelope
from sales_agent.v2_tools.cases import bind_case_request, unverified_case_query_response
from sales_agent.v2_tools.offline_public_cases import search_offline_public_references
from sales_agent.v2_tools.program_batch import bind_program_batch

from .policy import (
    DEFAULT_PRICING_POLICY, GOLD_POLICY_VERSION, POLICY_GATE_VERSION, PolicyViolation,
    validate_conversation, validate_decision,
)
from .providers import JSONProvider, ProviderError


# Only these fixed runtime codes may leave the store as diagnostic metadata.
# Unknown exception text is deliberately discarded, even if it looks code-like.
_SAFE_RUNTIME_DIAGNOSTICS = {
    "invalid_normalized_meaning", "normalized_meaning_requires_raw_span",
    "invalid_interpretation_fields", "invalid_interpretation_hypothesis",
    "invalid_interpretation_confidence", "hypothesis_requires_raw_span",
    "invalid_contradicting_evidence", "stale_interpretation_version",
    "model_cannot_replace_human_interpretation", "stale_inbound_interpretation",
    "decision_requires_turn_context", "turn_context_is_not_current",
    "new_student_message_after_turn_context", "critical_context_incomplete",
    "stale_student_memory_revision", "stale_student_snapshot_revision",
    "student_memory_changed_after_context", "student_snapshot_changed_after_context",
    "decision_context_conversation_mismatch", "decision_input_events_do_not_match_context",
    "objective_attempt_boundary_invalid", "invalid_objective_status",
    "objective_triggers_not_in_context", "stopped_objective_cannot_have_sales_action",
    "v2_requires_human_approval", "previous_objective_assessment_mismatch",
    "previous_objective_link_mismatch", "blocked_or_completed_objective_cannot_continue",
    "continuing_objective_attempt_count_mismatch",
    "new_objective_requires_previous_assessment", "previous_objective_does_not_exist",
    "do_not_contact_requires_stop", "invalid_offer_state",
}
_SAFE_POLICY_DIAGNOSTICS = {
    "ADVANTAGE_TEAM_REGION", "ADVANTAGE_SCHOOL_INFORMATION",
    "ADVANTAGE_PERSONALIZED_WRITING", "ADVANTAGE_LOCAL_HOUSING",
    "ADVANTAGE_INTERNSHIP_INFO",
}
_SAFE_TOOL_ERROR_CODES = {
    "INVALID_REQUEST", "CASE_QUERY_FACT_UNVERIFIED", "APPROVED_CARDS_UNAVAILABLE",
    "CONTACT_STOP_REQUESTED", "APPROVED_CASE_SNAPSHOT_UNAVAILABLE",
    "CASE_SOURCE_UNAVAILABLE", "APPROVED_PROGRAM_COPY_UNAVAILABLE",
    "PROGRAM_SOURCE_UNAVAILABLE", "OFFICIAL_SEARCH_UNAVAILABLE",
    "ADVANTAGES_CATALOG_UNAVAILABLE",
}


@dataclass(frozen=True)
class ToolPorts:
    """Optional, explicitly approved data adapters; None fails closed."""

    cases_source: Any = None
    offline_public_cases_source: Any = None
    offline_homework_mode: bool = False
    program_source: Any = None
    official_source: Any = None
    # These are set only by the trusted host after checking source approval or
    # current delivery capacity. The model's tool arguments cannot grant them.
    approved_case_evidence: bool = False
    conditional_service_available: bool = False

    def call(self, name: str, args: dict) -> dict:
        if name == "methodologies":
            return get_sales_methodologies(args)
        if name == "cases":
            if self.offline_homework_mode and self.offline_public_cases_source is not None:
                return search_offline_public_references(args, source=self.offline_public_cases_source)
            return search_anonymized_cases(args, source=self.cases_source)
        if name == "programs":
            return get_program_evidence(args, source=self.program_source)
        if name == "advantages":
            if set(args) != {"region", "concern_tags"}:
                raise PolicyViolation("UNTRUSTED_ADVANTAGE_CONTROL")
            return select_approved_advantages(
                region=args["region"], concern_tags=args["concern_tags"],
                approved_case_evidence=self.approved_case_evidence,
                service_available=self.conditional_service_available,
            )
        raise PolicyViolation("UNKNOWN_TOOL")


@dataclass
class PipelineResult:
    status: str
    reason_codes: list[str] = field(default_factory=list)
    # A machine code for local diagnosis, never model text or an exception message.
    internal_diagnostic_code: str | None = None
    context_event_id: str | None = None
    decision_event_id: str | None = None
    draft_event_id: str | None = None
    intervention_event_id: str | None = None
    notification_event_ids: list[str] = field(default_factory=list)
    tool_trace_event_ids: list[str] = field(default_factory=list)
    proposal: dict | None = None


class V2SalesPipeline:
    """Generate one human-reviewable candidate from an already recorded inbound.

    `run_turn` never sends or records HUMAN_SENT. The event store is the only
    authority for contact permission, prior actual-sent history, and approvals.
    """

    def __init__(self, *, store: V2RuntimeStore, decision_provider: JSONProvider,
                 conversation_provider: JSONProvider, tools: ToolPorts | None = None,
                 pricing_policy: dict | None = None) -> None:
        self.store = store
        self.decision_provider = decision_provider
        self.conversation_provider = conversation_provider
        self.tools = tools or ToolPorts()
        self.pricing_policy = deepcopy(pricing_policy or DEFAULT_PRICING_POLICY)
        if self.pricing_policy.get("approval_status") != "APPROVED" or self.pricing_policy.get("approved_by") != "OWNER":
            raise ValueError("pricing_policy_must_be_owner_approved")

    def _model_context(self, context: dict) -> dict:
        """Minimum authorized context; no names, contact IDs or hidden Gold."""
        snapshot = self.store.get_student_snapshot(context["student_id"])
        if snapshot is None or snapshot["snapshot_revision"] != context["student_snapshot_revision"]:
            raise PolicyViolation("STUDENT_SNAPSHOT_CHANGED_DURING_TURN")
        student_record = (snapshot or {}).get("snapshot", {})
        education = student_record.get("education", {})
        education_allowlist = {"undergraduate_university_raw", "undergraduate_major_raw",
                               "undergraduate_tier", "university_raw", "major_raw",
                               "average_score", "score_raw", "score_band", "current_year", "grade",
                               "target_country", "target_university", "target_program_or_major",
                               "intake_year", "intake_batch"}
        education_context = {key: value for key, value in education.items() if key in education_allowlist}
        targets = student_record.get("targets", {})
        target_allowlist = {"countries", "universities", "programs_or_majors"}
        target_context = {key: value for key, value in targets.items() if key in target_allowlist}
        supplied_paths = {f"education.{key}" for key in education_context}
        supplied_paths.update(f"targets.{key}" for key in target_context)
        supplied_paths.update({"sales.stage", "sales.current_objection", "sales.contact_permission"})
        field_provenance = context["student_record_field_provenance"]
        recent_history = context["customer_visible_history"][-8:]
        confirmed_memory = [item for item in context["memory_items"] if item["epistemic_status"] != "AGENT_HYPOTHESIS"]
        unverified_hypotheses = [item for item in context["memory_items"] if item["epistemic_status"] == "AGENT_HYPOTHESIS"]
        observations = []
        for event in context["latest_observations"]:
            if event["event_type"] == "INBOUND_RECEIVED":
                observations.append({"event_id": event["event_id"], "kind": "STUDENT_RAW",
                                     "text": event["payload"]["raw_text"]})
            elif event["event_type"] == "REVIEW":
                reflections = [item for item in self.store.list_events(context["student_id"], event_type="REVIEW_REFLECTION")
                               if item["payload"]["review_event_id"] == event["event_id"]]
                observations.append({"event_id": event["event_id"], "kind": "INTERNAL_REVIEW",
                                     "feedback_types": event["payload"]["feedback_types"],
                                     "comment": event["payload"]["comment"],
                                     "reflection": reflections[-1]["payload"] if reflections else None})
        return {
            "context_id": context["context_id"],
            "student_snapshot_revision": context["student_snapshot_revision"],
            "student_memory_revision": context["student_memory_revision"],
            "global_goal": context["global_goal"],
            "policy_versions": context["policy_versions"],
            "current_state": context["current_state"],
            "education": education_context,
            "targets": target_context,
            "student_record_field_provenance": {
                path: value for path, value in field_provenance.items() if path in supplied_paths
            },
            "observations": observations,
            "inbound_interpretation_status": context["latest_inbound_interpretation_status"],
            "inbound_interpretation": context["latest_inbound_interpretation"],
            "customer_visible_history": recent_history,
            "long_term_memory": {"confirmed_or_customer_stated": confirmed_memory,
                                 "unverified_hypotheses": unverified_hypotheses,
                                 "mandatory_memory_ids": context["mandatory_memory_ids"]},
            "working_memory": context["working_memory"],
            "history_window_limit": 8,
        }

    def _existing_run(self, *, student_id: str, conversation_id: str,
                      inbound_event_id: str, review_event_id: str | None,
                      idempotency_key: str) -> PipelineResult | None:
        events = [event for event in self.store.list_events(student_id)
                  if event["idempotency_key"] == f"{idempotency_key}:context"]
        if not events:
            return None
        context_event = events[-1]
        context = context_event["payload"]
        expected_observations = [inbound_event_id] + ([review_event_id] if review_event_id else [])
        if context_event["conversation_id"] != conversation_id or context["latest_observation_event_ids"] != expected_observations:
            return PipelineResult(status="HANDOFF", reason_codes=["IDEMPOTENCY_KEY_REUSED_FOR_DIFFERENT_TURN"])
        same_run = [event for event in self.store.list_events(student_id)
                    if event["idempotency_key"].startswith(f"{idempotency_key}:")]
        decision = next((event for event in same_run if event["event_type"] == "DECISION_READY"), None)
        draft = next((event for event in same_run if event["event_type"] == "DRAFTED"), None)
        intervention = next((event for event in same_run if event["event_type"] == "INTERVENTION_DECISION"), None)
        tool_traces = [event for event in same_run if event["event_type"] == "TOOL_TRACE"]
        notices = [event for event in same_run if event["event_type"] == "INTERNAL_NOTIFICATION"]
        result = PipelineResult(status="HANDOFF", reason_codes=["PREVIOUS_RUN_INCOMPLETE"],
                                context_event_id=context_event["event_id"],
                                decision_event_id=decision["event_id"] if decision else None,
                                draft_event_id=draft["event_id"] if draft else None,
                                intervention_event_id=intervention["event_id"] if intervention else None,
                                tool_trace_event_ids=[event["event_id"] for event in tool_traces],
                                notification_event_ids=[event["event_id"] for event in notices])
        if draft:
            current_draft_status = self.store.draft_status(draft["event_id"])
            if current_draft_status in {"APPROVED", "HUMAN_SENT", "REJECTED", "REVIEW_CHANGES_REQUESTED"}:
                result.status = current_draft_status
                result.reason_codes = []
                return result
            gate = next((event for event in reversed(same_run) if event["event_type"] == "GATE_RESULT"
                         and event["payload"]["subject_event_id"] == draft["event_id"]), None)
            if gate:
                result.status = "REVIEW_REQUIRED" if gate["payload"]["passed"] else "REVISE_REQUIRED"
                result.reason_codes = list(gate["payload"]["hard_violations"])
        elif decision:
            offer = decision["payload"]["offer"]
            if offer["state"] == "CUSTOM_OFFER_PROPOSAL":
                result.status = "APPROVAL_REQUIRED"
                result.proposal = offer["proposal"]
                result.reason_codes = []
            elif decision["payload"]["stop_required"]:
                result.status = "STOPPED"
                result.reason_codes = ["STOP_REQUIRED"]
        elif not context["context_manifest"]["critical_context_complete"]:
            result.reason_codes = ["CRITICAL_CONTEXT_INCOMPLETE"]
        return result

    @staticmethod
    def _explicit_do_not_contact(text: str) -> bool:
        return bool(re.search(r"(?:\u4e0d\u8981|\u522b|\u8bf7\u522b)\s*(?:\u518d)?\s*\u8054\u7cfb\u6211|\u522b\u518d\u7ed9\u6211\u53d1(?:\u6d88\u606f|\u4fe1\u606f)|\b(?:do\s+not|don't|stop)\s+(?:contact(?:ing)?|message|messaging|text(?:ing)?)\s+me\b", text, re.I))

    @staticmethod
    def _trace_status(tool_status: str) -> str:
        return {"INVALID_REQUEST": "INVALID", "POLICY_BLOCKED": "INVALID"}.get(tool_status, tool_status)

    @staticmethod
    def _safe_input_summary(name: str, args: dict) -> dict:
        # Tool inputs may contain a raw university or grade. A shape digest is
        # enough to audit what class of query was made without copying PII.
        return {"tool": name, "input_keys": sorted(args),
                "request_sha256": sha256(json.dumps(args, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}

    @staticmethod
    def _returned_evidence(response: dict) -> dict[str, dict]:
        if response.get("status") != "OK":
            return {}
        result = {}
        for row in response.get("data", []):
            if not isinstance(row, dict):
                continue
            evidence_id = row.get("evidence_id") or (
                row.get("advantage_id") if response.get("source_type") == "approved_advantage" else None)
            if isinstance(evidence_id, str):
                result[evidence_id] = row
        return result

    def _record_tool_traces(self, *, student_id: str, conversation_id: str,
                            calls: list[tuple[str, dict, dict]], adopted: set[str],
                            idempotency_key: str, cause_id: str) -> list[str]:
        trace_ids = []
        for index, (name, args, response) in enumerate(calls):
            returned = list(self._returned_evidence(response))
            error = response.get("error")
            error_code = error.get("code") if isinstance(error, dict) else None
            if error_code not in _SAFE_TOOL_ERROR_CODES:
                error_code = None
            trace = self.store.record_tool_trace(
                student_id=student_id, conversation_id=conversation_id, tool_name=name,
                call_id=response.get("tool_call_id", f"unavailable-{index}"),
                input_summary=self._safe_input_summary(name, args),
                status=self._trace_status(response.get("status", "ERROR")),
                returned_evidence_ids=returned,
                adopted_evidence_ids=[eid for eid in returned if eid in adopted],
                source_version=response.get("source_version"),
                limitations=response.get("limitations", []),
                response_status=response.get("status"), error_code=error_code,
                idempotency_key=f"{idempotency_key}:tool:{index}", causation_id=cause_id,
            )
            trace_ids.append(trace["event_id"])
        return trace_ids

    @staticmethod
    def _validate_tool_plan(plan: dict) -> list[tuple[str, dict]]:
        requests = plan.get("requests") if isinstance(plan, dict) else None
        if not isinstance(requests, list) or len(requests) > 3:
            raise PolicyViolation("INVALID_TOOL_PLAN")
        parsed: list[tuple[str, dict]] = []
        for item in requests:
            if not isinstance(item, dict) or set(item) != {"name", "args"} or item["name"] not in {
                "methodologies", "cases", "programs", "advantages"
            } or not isinstance(item["args"], dict):
                raise PolicyViolation("INVALID_TOOL_REQUEST")
            if item["name"] in {name for name, _ in parsed}:
                raise PolicyViolation("DUPLICATE_TOOL_CALL")
            parsed.append((item["name"], item["args"]))
        return parsed

    def _persist_program_freshness(self, *, student_id: str, conversation_id: str,
                                   context_event_id: str, calls: list[tuple[str, dict, dict]],
                                   verification: dict | None, key: str) -> tuple[list[str], bool]:
        notifications = []
        handoff = False
        for index, (name, _args, response) in enumerate(calls):
            if name != "programs":
                continue
            actions = program_freshness_actions(response, verification)
            handoff |= actions["handoff_required"]
            for notice in actions["notifications"]:
                event = self.store.record_internal_notification(
                    student_id=student_id, conversation_id=conversation_id,
                    notification_type="PROGRAM_DB_UPDATE_REQUIRED",
                    details={"program_evidence_id": notice["evidence_id"],
                             "program_source_version": response.get("source_version")},
                    source_event_ids=[context_event_id],
                    idempotency_key=f"{key}:program-update:{index}:{notice['evidence_id']}",
                )
                notifications.append(event["event_id"])
        return notifications, handoff

    def _verify_claims(self, claims: Any, *, returned: dict[str, dict],
                       adopted: set[str]) -> tuple[dict | None, list[str]]:
        if not isinstance(claims, list):
            raise PolicyViolation("PROGRAM_CLAIMS_NOT_LIST")
        if not claims:
            return None, []
        if any(not isinstance(claim, dict) or claim.get("program_evidence_id") not in adopted
               or returned.get(claim.get("program_evidence_id"), {}).get("source_type") != "official_program_record"
               for claim in claims):
            raise PolicyViolation("PROGRAM_CLAIM_WITHOUT_ADOPTED_EVIDENCE")
        for claim in claims:
            item = returned[claim["program_evidence_id"]].get("item", {})
            if item.get("intake_batch") != claim.get("intake_batch"):
                raise PolicyViolation("PROGRAM_CLAIM_BATCH_MISMATCH")
        checked = verify_program_claims(claims, official_source=self.tools.official_source)
        if checked.get("status") != "OK" or len(checked.get("data", [])) != len(claims):
            return checked, ["PROGRAM_VERIFICATION_UNAVAILABLE"]
        failed = [c.get("claim_id", "unknown") for c in checked["data"] if c.get("status") != "VERIFIED"]
        return checked, (["PROGRAM_CLAIM_NOT_VERIFIED"] if failed else [])

    def _validate_approved_custom_offer(self, student_id: str, decision: dict) -> None:
        offer = decision["offer"]
        if offer["state"] != "APPROVED_CUSTOM_OFFER":
            return
        approval = self.store.get_event(offer.get("approval_event_id"))
        if not approval or approval["event_type"] != "CUSTOM_OFFER_APPROVED" or approval["student_id"] != student_id:
            raise PolicyViolation("CUSTOM_APPROVAL_EVENT_REQUIRED")
        if approval["payload"].get("offer_id") != offer.get("offer_id") or approval["payload"].get("offer_version") != offer.get("offer_version"):
            raise PolicyViolation("CUSTOM_APPROVAL_VERSION_MISMATCH")
        approved = approval["payload"]["approved_offer"]
        price = approved.get("approved_price")
        if type(price) is not int or price < 0:
            raise PolicyViolation("APPROVED_CUSTOM_PRICE_MISSING")
        semantic = decision["content_contract"]["semantic_draft"]
        import re
        quoted_prices = {int(group) for pair in re.findall(
            r"[¥￥$£]\s*(\d{3,6})|(?<!\d)(\d{3,6})\s*(?:\u5143|\u5757|\u4eba\u6c11\u5e01|RMB|CNY|USD|SGD|dollars?|pounds?)", semantic, re.I)
                         for group in pair if group}
        if quoted_prices and quoted_prices != {price}:
            raise PolicyViolation("APPROVED_CUSTOM_PRICE_MISMATCH")

    @staticmethod
    def _stale_program_claims(calls: list[tuple[str, dict, dict]]) -> list[dict]:
        """Deterministically verify each usable field on stale Program rows."""
        claims = []
        for name, _args, response in calls:
            if name != "programs" or response.get("status") != "OK":
                continue
            for row in response.get("data", []):
                if row.get("freshness_status") != "STALE":
                    continue
                for fact_key, fact in row.get("facts", {}).items():
                    if isinstance(fact, dict) and isinstance(fact.get("value"), str):
                        claims.append({"claim_id": f"stale-{len(claims)+1}",
                                       "university": row["university"], "program": row["program"],
                                       "intake_year": row["intake_year"], "fact_key": fact_key,
                                       **({"intake_batch": row["intake_batch"]} if "intake_batch" in row else {}),
                                       "expected_value": fact["value"],
                                       "program_evidence_id": row["evidence_id"]})
        return claims

    @staticmethod
    def _budget_amount_from_span(span: str) -> int | None:
        arabic = re.search(r"(?<!\d)(\d{3,6})(?!\d)", span)
        if arabic:
            return int(arabic.group(1))
        wan = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*\u4e07", span)
        if wan:
            return int(float(wan.group(1)) * 10000)
        digits = {"\u4e00": 1, "\u4e8c": 2, "\u4e24": 2, "\u4e09": 3, "\u56db": 4, "\u4e94": 5,
                  "\u516d": 6, "\u4e03": 7, "\u516b": 8, "\u4e5d": 9}
        match = re.search(r"([\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d])\u4e07([\u4e00\u4e8c\u4e24\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d])?", span)
        if match:
            return digits[match.group(1)] * 10000 + (digits[match.group(2)] * 1000 if match.group(2) else 0)
        return None

    @staticmethod
    def _memory_key_allowed(category: str, key: str) -> bool:
        allowed = {
            "BUDGET": {"budget.total_ceiling"},
            "DECISION_ROLE": {"payer", "decision_maker"},
            "BACKGROUND": {"undergraduate_university_raw", "undergraduate_major_raw",
                           "average_score", "grade"},
            "GOAL": {"target_country", "target_university", "target_program_or_major",
                     "target_intake_year"},
            "PREFERENCE": {"service_scope_preference", "communication_preference"},
            "COMMITMENT": {"agreed_next_step"},
            "REJECTED_PATH": {"rejected_offer_or_service"},
        }
        return key in allowed.get(category, set())

    def _validate_memory_candidates(self, decision: dict, inbound: dict) -> list[dict]:
        candidates = decision.get("memory_candidates", [])
        if not isinstance(candidates, list) or len(candidates) > 8:
            raise PolicyViolation("MEMORY_CANDIDATES_INVALID")
        if len({candidate.get("key") for candidate in candidates if isinstance(candidate, dict)}) != len(candidates):
            raise PolicyViolation("DUPLICATE_MEMORY_CANDIDATE_KEY")
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {
                "category", "key", "value", "epistemic_status", "source_event_ids", "evidence_span"
            }:
                raise PolicyViolation("MEMORY_CANDIDATE_FIELDS_INVALID")
            if candidate["epistemic_status"] != "CUSTOMER_STATED" or candidate["source_event_ids"] != [inbound["event_id"]]:
                raise PolicyViolation("MEMORY_CANDIDATE_NOT_STUDENT_FACT")
            if not self._memory_key_allowed(candidate["category"], candidate["key"]):
                raise PolicyViolation("MEMORY_KEY_NOT_APPROVED_FOR_AUTO_EXTRACTION")
            span = candidate["evidence_span"]
            if not isinstance(span, str) or not span or span not in inbound["payload"]["raw_text"]:
                raise PolicyViolation("MEMORY_CANDIDATE_SPAN_MISSING")
            value = candidate["value"]
            if candidate["category"] == "BUDGET" and type(value) is not int:
                raise PolicyViolation("BUDGET_NORMALIZED_INTEGER_REQUIRED")
            if candidate["category"] == "BUDGET" and type(value) is int:
                if re.search(r"(?:\u4e0d\u662f|\u5e76\u975e|\u4e0d\u4ee3\u8868|\u542c\u8bf4|\u636e\u8bf4|\u522b\u4eba|\u670b\u53cb|\u5979\u8bf4|\u4ed6\u8bf4|\b(?:not\s+my\s+budget|someone\s+else(?:'s)?|i\s+heard|my\s+friend)\b)", span, re.I) or not re.search(
                    r"(?:\u603b\u5171|\u603b\u9884\u7b97|\u9884\u7b97|\u6700\u591a|\u4e0a\u9650|\u5c01\u9876|\b(?:my\s+)?(?:total\s+)?budget\b|\bat\s+most\b|\bceiling\b)", span, re.I):
                    raise PolicyViolation("BUDGET_STATEMENT_NEEDS_HUMAN_CONFIRMATION")
                if self._budget_amount_from_span(span) != value:
                    raise PolicyViolation("MEMORY_BUDGET_NORMALIZATION_MISMATCH")
            elif candidate["category"] == "DECISION_ROLE":
                if not isinstance(value, str) or value not in span or re.search(
                    r"(?:\u4e0d\u662f|\u5e76\u975e|\u4e0d\u786e\u5b9a|\u53ef\u80fd|\u4e5f\u8bb8|\u542c\u8bf4|\u636e\u8bf4)", span):
                    raise PolicyViolation("DECISION_ROLE_NEEDS_HUMAN_CONFIRMATION")
                if candidate["key"] == "payer" and not re.search(r"(?:\u4ed8\u6b3e\u4eba\u662f|\u4ed8\u94b1\u7684\u662f|\u7531.{1,6}\u4ed8\u94b1|\u7531.{1,6}\u4ed8\u6b3e)", span):
                    raise PolicyViolation("PAYER_STATEMENT_NEEDS_HUMAN_CONFIRMATION")
                if candidate["key"] == "decision_maker" and not re.search(r"(?:\u51b3\u5b9a\u7684\u4eba\u662f|\u505a\u51b3\u5b9a\u7684\u662f|\u7531.{1,6}\u51b3\u5b9a)", span):
                    raise PolicyViolation("DECISION_MAKER_NEEDS_HUMAN_CONFIRMATION")
            elif not isinstance(value, str) or value not in span:
                raise PolicyViolation("MEMORY_VALUE_NOT_SOURCE_BOUND")
            if candidate["category"] in {"BACKGROUND", "GOAL", "PREFERENCE", "COMMITMENT"}:
                # A quoted value is not a positive customer statement when the local clause negates it.
                before_value = span[:span.find(value)].rsplit("，", 1)[-1].rsplit("。", 1)[-1]
                if re.search(r"(?:\u4e0d\u662f\u8981|\u4e0d\u9700\u8981|\u4e0d\u60f3\u8981|\u6ca1\u6709|\u5e76\u975e|\u4e0d\u786e\u5b9a|\u53ef\u80fd|\u4e5f\u8bb8|\u542c\u8bf4|\u636e\u8bf4)\s*$", before_value):
                    raise PolicyViolation("MEMORY_CANDIDATE_NEGATED_OR_AMBIGUOUS")
        return candidates

    def _persist_memory_candidates(self, *, candidates: list[dict], inbound: dict,
                                   student_id: str, conversation_id: str,
                                   idempotency_key: str) -> list[str]:
        saved = []
        for index, candidate in enumerate(candidates):
            event = self.store.add_memory_item(
                student_id=student_id, conversation_id=conversation_id,
                category=candidate["category"], key=candidate["key"], value=candidate["value"],
                epistemic_status="CUSTOMER_STATED",
                source_event_ids=[inbound["event_id"]], evidence_span=candidate["evidence_span"],
                idempotency_key=f"{idempotency_key}:memory:{index}")
            saved.append(event["event_id"])
        return saved

    def run_turn(self, *, student_id: str, conversation_id: str, inbound_event_id: str,
                 idempotency_key: str, review_event_id: str | None = None) -> PipelineResult:
        try:
            existing = self._existing_run(student_id=student_id, conversation_id=conversation_id,
                                          inbound_event_id=inbound_event_id,
                                          review_event_id=review_event_id,
                                          idempotency_key=idempotency_key)
        except Exception:
            return PipelineResult(status="HANDOFF", reason_codes=["RUNTIME_UNAVAILABLE"])
        if existing is not None:
            return existing
        try:
            inbound = self.store.get_event(inbound_event_id)
        except Exception:
            return PipelineResult(status="HANDOFF", reason_codes=["RUNTIME_UNAVAILABLE"])
        if not inbound or inbound["student_id"] != student_id or inbound["conversation_id"] != conversation_id or inbound["event_type"] != "INBOUND_RECEIVED":
            return PipelineResult(status="HANDOFF", reason_codes=["INBOUND_EVENT_REQUIRED"])
        if self._explicit_do_not_contact(inbound["payload"]["raw_text"]):
            try:
                self.store.set_contact_permission(
                    student_id=student_id, conversation_id=conversation_id,
                    permission="DO_NOT_CONTACT", source_event_id=inbound_event_id,
                    idempotency_key=f"{idempotency_key}:do-not-contact")
            except Exception:
                return PipelineResult(status="HANDOFF", reason_codes=["CONTACT_PERMISSION_UPDATE_FAILED"])
        observations = [inbound_event_id]
        if review_event_id is not None:
            review = self.store.get_event(review_event_id)
            if not review or review["student_id"] != student_id or review["conversation_id"] != conversation_id or review["event_type"] != "REVIEW" or review["payload"]["action"] != "REQUEST_CHANGES" or review["payload"]["route_to"] not in {"DECISION_AGENT", "BOTH"}:
                return PipelineResult(status="HANDOFF", reason_codes=["DECISION_REVIEW_REQUIRED"])
            observations.append(review_event_id)
        result = PipelineResult(status="HANDOFF")
        calls: list[tuple[str, dict, dict]] = []
        try:
            context_event = self.store.assemble_turn_context(
                student_id=student_id, conversation_id=conversation_id,
                latest_observation_event_ids=observations,
                policy_versions=[self.pricing_policy["version"], GOLD_POLICY_VERSION,
                                 POLICY_GATE_VERSION, "sales-tools.v1"],
                idempotency_key=f"{idempotency_key}:context",
            )
            context = context_event["payload"]
            result.context_event_id = context_event["event_id"]
            if not context["context_manifest"]["critical_context_complete"]:
                result.reason_codes = ["CRITICAL_CONTEXT_INCOMPLETE"] + context["context_manifest"].get("missing", [])
                return result
            if context["current_state"]["contact_permission"] == "DO_NOT_CONTACT":
                result.status = "STOPPED"
                result.reason_codes = ["DO_NOT_CONTACT"]
                return result
            if context["current_state"]["contact_permission"] == "UNKNOWN":
                result.reason_codes = ["CONTACT_PERMISSION_UNKNOWN"]
                return result
            model_context = self._model_context(context)
            plan = self.decision_provider.generate_json("tool_plan", {"turn_context": model_context})
            requests = self._validate_tool_plan(plan)
            evidence: dict[str, dict] = {}
            final_tool_results: list[dict] = []
            for name, args in requests:
                if name == "programs" and (args.get("contract_version") == "sales-tools.v1.1"
                                           or getattr(self.tools.program_source, "requires_live_official_check", False)):
                    bound = bind_program_batch(args, self.store.get_student_snapshot(student_id))
                    if bound is None:
                        response = envelope("POLICY_BLOCKED", "official_program_record",
                                            limitations=["CONFIRMED_EXACT_INTAKE_BATCH_REQUIRED"],
                                            error_code="INVALID_REQUEST")
                    else:
                        args = bound
                        response = self.tools.call(name, args)
                elif name == "cases":
                    bound = bind_case_request(
                        args, self.store.get_student_snapshot(student_id), inbound["payload"]["raw_text"])
                    if bound is None:
                        response = unverified_case_query_response()
                    else:
                        args = bound
                        response = self.tools.call(name, args)
                else:
                    response = self.tools.call(name, args)
                if not isinstance(response, dict):
                    raise PolicyViolation("TOOL_RESPONSE_INVALID")
                calls.append((name, args, response))
                error = response.get("error")
                if (response.get("status") == "UNAVAILABLE" and isinstance(error, dict)
                        and error.get("retryable") is True):
                    retry = self.tools.call(name, args)
                    if not isinstance(retry, dict):
                        raise PolicyViolation("TOOL_RESPONSE_INVALID")
                    calls.append((name, args, retry))
                    response = retry
                    if response.get("status") not in {"OK", "NO_RESULTS"}:
                        raise PolicyViolation("TOOL_RETRY_EXHAUSTED")
                final_tool_results.append({"name": name, "response": response})
                for evid, item in self._returned_evidence(response).items():
                    evidence[evid] = {"source_type": response["source_type"],
                                      "source_version": response.get("source_version"), "item": item}
            decision = self.decision_provider.generate_json(
                "decision", {"turn_context": model_context,
                             "tool_results": final_tool_results,
                             "available_evidence_ids": sorted(evidence),
                             "pricing_policy": self.pricing_policy,
                             "gold_policy_version": GOLD_POLICY_VERSION})
            decision = dict(decision)
            if not {"normalized_meaning", "normalized_meaning_spans"} <= set(decision):
                raise PolicyViolation("INBOUND_INTERPRETATION_FIELDS_MISSING")
            prior_interpretation = self.store.latest_inbound_interpretation(inbound_event_id)
            if (prior_interpretation["event_id"] if prior_interpretation else None) != context[
                "latest_inbound_interpretation_event_id"
            ]:
                raise PolicyViolation("INBOUND_INTERPRETATION_CHANGED_AFTER_CONTEXT")
            if prior_interpretation and prior_interpretation["actor"] == "SALESPERSON":
                corrected = prior_interpretation["payload"]
                for field in ("normalized_meaning", "normalized_meaning_spans", "hypotheses", "unknowns"):
                    if decision.get(field) != corrected[field]:
                        raise PolicyViolation("DECISION_CONFLICTS_WITH_HUMAN_INTERPRETATION")
            if getattr(self.decision_provider, "require_complete_output", False) and "program_claims" not in decision:
                raise PolicyViolation("MODEL_DECISION_REQUIRED_FIELDS_MISSING")
            decision.setdefault("program_claims", [])
            decision.setdefault("memory_candidates", [])
            decision.update({"input_event_ids": observations,
                             "student_snapshot_revision": context["student_snapshot_revision"],
                             "student_memory_revision": context["student_memory_revision"],
                             "turn_context_id": context["context_id"]})
            warnings = validate_decision(context, decision, evidence,
                                         pricing_policy=self.pricing_policy)
            self._validate_approved_custom_offer(student_id, decision)
            # Validate all candidates before any can be persisted. Record the
            # decision first so its referenced memory_revision remains current.
            memory_candidates = self._validate_memory_candidates(decision, inbound)
            adopted = set(decision["evidence_ids"])
            supplied_reasons = decision.get("evidence_not_used", [])
            reason_by_id = {item.get("evidence_id"): item.get("reason")
                            for item in supplied_reasons if isinstance(item, dict)
                            and item.get("reason") in {"NOT_RELEVANT", "WEAK_MATCH", "OUTDATED",
                                                       "CONFLICT", "NOT_NEEDED"}} if isinstance(supplied_reasons, list) else {}
            decision["evidence_not_used"] = [
                {"evidence_id": eid, "reason": reason_by_id.get(eid) or "NOT_SELECTED_BY_DECISION"}
                for eid in evidence if eid not in adopted
            ]
            # Program verification is deterministic and outside the model's
            # discretion. A stale record also creates an internal DB notice.
            stale_claims = self._stale_program_claims(calls)
            verification_claims = list(decision["program_claims"])
            verification_claims.extend(stale_claims)
            verification_adopted = adopted | {claim["program_evidence_id"] for claim in stale_claims}
            verification, verification_errors = self._verify_claims(
                verification_claims, returned=evidence, adopted=verification_adopted)
            if len(verification_claims) > 20:
                verification_errors = ["TOO_MANY_PROGRAM_CLAIMS_FOR_VERIFICATION"]
            notices, stale_handoff = self._persist_program_freshness(
                student_id=student_id, conversation_id=conversation_id,
                context_event_id=context_event["event_id"], calls=calls,
                verification=verification, key=idempotency_key)
            result.notification_event_ids = notices
            interpretation = prior_interpretation
            if not prior_interpretation or prior_interpretation["actor"] != "SALESPERSON":
                interpretation = self.store.record_inbound_interpretation(
                    inbound_event_id=inbound_event_id,
                    normalized_meaning=decision["normalized_meaning"],
                    normalized_meaning_spans=decision["normalized_meaning_spans"],
                    hypotheses=decision["hypotheses"], unknowns=decision["unknowns"],
                    supersedes_event_id=prior_interpretation["event_id"] if prior_interpretation else None,
                    idempotency_key=f"{idempotency_key}:interpretation")
            decision["inbound_interpretation_event_id"] = interpretation["event_id"]
            decision_event = self.store.record_decision(
                student_id=student_id, conversation_id=conversation_id,
                payload=decision, idempotency_key=f"{idempotency_key}:decision")
            result.decision_event_id = decision_event["event_id"]
            self._persist_memory_candidates(
                candidates=memory_candidates, inbound=inbound, student_id=student_id,
                conversation_id=conversation_id, idempotency_key=idempotency_key)
            result.tool_trace_event_ids = self._record_tool_traces(
                student_id=student_id, conversation_id=conversation_id,
                calls=calls, adopted=adopted, idempotency_key=idempotency_key,
                cause_id=decision_event["event_id"])
            if verification is not None:
                # Official-search trace records scoped claim IDs/status only.
                checked = self.store.record_tool_trace(
                    student_id=student_id, conversation_id=conversation_id,
                    tool_name="verify_program_claims", call_id=verification.get("tool_call_id", "verify-unavailable"),
                    input_summary={"claim_ids": [claim["claim_id"] for claim in verification_claims]},
                    status=self._trace_status(verification.get("status", "ERROR")),
                    returned_evidence_ids=[], adopted_evidence_ids=[],
                    source_version=verification.get("source_version"),
                    limitations=verification.get("limitations", []),
                    idempotency_key=f"{idempotency_key}:verify", causation_id=decision_event["event_id"])
                result.tool_trace_event_ids.append(checked["event_id"])
            if stale_handoff or verification_errors:
                violations = verification_errors or ["STALE_PROGRAM_VERIFICATION_FAILED"]
                self._gate(decision_event["event_id"], "PRE_CONVERSATION", violations, "HANDOFF", idempotency_key)
                result.reason_codes = violations
                return result
            if decision["stop_required"] or decision["current_objective"]["status"] == "STOPPED":
                self._gate(decision_event["event_id"], "PRE_CONVERSATION", ["STOP_REQUIRED"], "STOP", idempotency_key)
                result.status, result.reason_codes = "STOPPED", ["STOP_REQUIRED"]
                return result
            if decision["offer"]["state"] == "CUSTOM_OFFER_PROPOSAL":
                self._gate(decision_event["event_id"], "PRE_CONVERSATION", ["CUSTOM_OFFER_PENDING"], "APPROVE", idempotency_key)
                intervention = self._intervention(decision_event["event_id"], idempotency_key,
                                                  custom=True)
                result.status, result.proposal = "APPROVAL_REQUIRED", decision["offer"]["proposal"]
                result.intervention_event_id = intervention["event_id"]
                result.reason_codes = warnings
                return result
            self._gate(decision_event["event_id"], "PRE_CONVERSATION", [], "ALLOW", idempotency_key)
            conversation = self.conversation_provider.generate_json(
                "conversation", {"content_contract": decision["content_contract"],
                                 "offer": decision["offer"],
                                 "adopted_evidence": [evidence[x] for x in decision["evidence_ids"]],
                                 "verified_program_claims": verification.get("data", []) if verification else []})
            messages = conversation.get("messages") if isinstance(conversation, dict) else None
            style = conversation.get("style_transformations", []) if isinstance(conversation, dict) else []
            if not isinstance(style, list):
                raise PolicyViolation("INVALID_STYLE_TRANSFORMATIONS")
            # Persist rejected draft for audit, then a failing POST gate. This
            # draft remains invisible to customer history and cannot be approved.
            post_errors = []
            try:
                validate_conversation(decision, messages, verified_claims=verification.get("data", []) if verification else [])
            except PolicyViolation as exc:
                post_errors = exc.codes
            if not isinstance(messages, list) or not messages or not any(isinstance(m, dict) and m.get("type") == "text" and str(m.get("content", "")).strip() for m in messages):
                raise PolicyViolation("NO_PERSISTABLE_DRAFT")
            draft_event = self.store.create_draft(
                decision_event_id=decision_event["event_id"], messages=messages,
                style_transformations=style,
                idempotency_key=f"{idempotency_key}:draft")
            result.draft_event_id = draft_event["event_id"]
            self._gate(draft_event["event_id"], "POST_CONVERSATION", post_errors,
                       "REVISE" if post_errors else "ALLOW", idempotency_key)
            intervention = self._intervention(draft_event["event_id"], idempotency_key,
                                              custom=False, errors=post_errors)
            result.intervention_event_id = intervention["event_id"]
            result.status = "REVISE_REQUIRED" if post_errors else "REVIEW_REQUIRED"
            result.reason_codes = post_errors
            return result
        except Exception as exc:
            if isinstance(exc, PolicyViolation):
                codes = exc.codes
                if exc.diagnostic_code in _SAFE_POLICY_DIAGNOSTICS:
                    result.internal_diagnostic_code = exc.diagnostic_code
            elif isinstance(exc, ContractViolation):
                codes = ["RUNTIME_CONTRACT_VIOLATION"]
                # Do not echo arbitrary exception text into traces or the UI.
                candidate = str(exc)
                if candidate in _SAFE_RUNTIME_DIAGNOSTICS:
                    result.internal_diagnostic_code = candidate
            elif isinstance(exc, ProviderError):
                codes = ["MODEL_PROVIDER_UNAVAILABLE_OR_INVALID"]
            else:
                codes = ["PIPELINE_INVALID_DATA"]
            # Never convert failure into a customer-visible draft or claim that
            # an incomplete turn passed. Existing immutable events remain.
            if calls and not result.tool_trace_event_ids and result.context_event_id:
                try:
                    result.tool_trace_event_ids = self._record_tool_traces(
                        student_id=student_id, conversation_id=conversation_id,
                        calls=calls, adopted=set(), idempotency_key=idempotency_key,
                        cause_id=result.context_event_id)
                except Exception:
                    codes.append("TOOL_TRACE_PERSISTENCE_FAILED")
            result.status = "HANDOFF"
            result.reason_codes = codes
            return result

    def _gate(self, subject_event_id: str, stage: str, errors: list[str], action: str, key: str) -> dict:
        return self.store.record_gate_result(
            subject_event_id=subject_event_id, gate_stage=stage,
            passed=not errors, hard_violations=errors, warnings=[],
            required_action=action, idempotency_key=f"{key}:gate:{stage}")

    def _intervention(self, subject_id: str, key: str, *, custom: bool,
                      errors: list[str] | None = None) -> dict:
        errors = errors or []
        return self.store.record_intervention_decision(
            trigger_event_id=subject_id, policy_version="hitl-policy.v1",
            risk_level="HIGH" if custom or errors else "LOW",
            risk_reasons=(["CUSTOM_OFFER"] if custom else []) + errors,
            uncertainty=0.5 if custom or errors else 0.0,
            recommended_review_focus=["PRICING", "DELIVERY_FEASIBILITY"] if custom else
                                     (["FACT", "NATURALNESS"] if errors else ["NATURALNESS"]),
            route_to="PRICING_OWNER" if custom else "SALESPERSON",
            hard_gate_reasons=errors, idempotency_key=f"{key}:intervention")

    def revise_draft(self, *, review_event_id: str, idempotency_key: str) -> PipelineResult:
        """Use a human rejection as internal feedback, not a student message.

        Strategy/fact/permission feedback re-enters the Decision Agent with a
        new typed observation. Pure style feedback keeps the approved Decision
        content contract and asks only Conversation Agent to rewrite.
        """
        try:
            review = self.store.get_event(review_event_id)
        except Exception:
            return PipelineResult(status="HANDOFF", reason_codes=["RUNTIME_UNAVAILABLE"])
        if not review or review["event_type"] != "REVIEW" or review["payload"]["action"] != "REQUEST_CHANGES":
            return PipelineResult(status="HANDOFF", reason_codes=["REQUEST_CHANGES_REVIEW_REQUIRED"])
        draft = self.store.get_event(review["payload"]["draft_event_id"])
        if not draft or draft["event_type"] != "DRAFTED" or draft["student_id"] != review["student_id"] or draft["conversation_id"] != review["conversation_id"]:
            return PipelineResult(status="HANDOFF", reason_codes=["REVIEW_DRAFT_MISSING"])
        decision = self.store.get_event(draft["payload"]["decision_event_id"])
        if not decision or decision["student_id"] != review["student_id"] or decision["conversation_id"] != review["conversation_id"]:
            return PipelineResult(status="HANDOFF", reason_codes=["REVIEW_DECISION_MISSING"])
        student_id = review["student_id"]
        conversation_id = review["conversation_id"]
        route = review["payload"]["route_to"]
        if route not in {"DECISION_AGENT", "CONVERSATION_AGENT", "BOTH"}:
            return PipelineResult(status="HANDOFF", reason_codes=["REVIEW_ROUTE_UNSPECIFIED"])
        try:
            if self.store.get_contact_permission(student_id) == "DO_NOT_CONTACT":
                return PipelineResult(status="STOPPED", reason_codes=["DO_NOT_CONTACT"])
        except Exception:
            return PipelineResult(status="HANDOFF", reason_codes=["RUNTIME_UNAVAILABLE"])
        try:
            reflection = self._review_reflection(review, draft, idempotency_key)
        except ProviderError:
            return PipelineResult(status="HANDOFF", reason_codes=["REFLECTION_PROVIDER_FAILED"])
        except Exception:
            return PipelineResult(status="HANDOFF", reason_codes=["REFLECTION_INVALID_OR_UNAVAILABLE"])
        if route in {"DECISION_AGENT", "BOTH"}:
            observations = decision["payload"].get("input_event_ids", [])
            inbound_ids = [event_id for event_id in observations if
                           (event := self.store.get_event(event_id)) and event["event_type"] == "INBOUND_RECEIVED"]
            if not inbound_ids:
                return PipelineResult(status="HANDOFF", reason_codes=["REVIEW_SOURCE_INBOUND_MISSING"])
            return self.run_turn(student_id=student_id, conversation_id=conversation_id,
                                 inbound_event_id=inbound_ids[-1], review_event_id=review_event_id,
                                 idempotency_key=idempotency_key)
        try:
            if self.store.get_contact_permission(student_id) == "DO_NOT_CONTACT":
                return PipelineResult(status="STOPPED", reason_codes=["DO_NOT_CONTACT"])
            if decision["payload"]["offer"]["state"] == "CUSTOM_OFFER_PROPOSAL":
                return PipelineResult(status="HANDOFF", reason_codes=["CUSTOM_OFFER_PENDING"])
            claims = decision["payload"].get("program_claims", [])
            verification = verify_program_claims(claims, official_source=self.tools.official_source) if claims else None
            if verification and (verification.get("status") != "OK" or len(verification.get("data", [])) != len(claims) or any(
                item.get("status") != "VERIFIED" for item in verification.get("data", []))):
                return PipelineResult(status="HANDOFF", reason_codes=["PROGRAM_REVERIFICATION_REQUIRED"])
            conversation = self.conversation_provider.generate_json("conversation", {
                "content_contract": decision["payload"]["content_contract"],
                "offer": decision["payload"]["offer"],
                "human_feedback": {"feedback_types": review["payload"]["feedback_types"],
                                   "comment": review["payload"]["comment"]},
                "review_reflection": reflection["payload"],
                "rejected_messages": draft["payload"]["messages"],
                "verified_program_claims": verification.get("data", []) if verification else [],
            })
            messages = conversation.get("messages") if isinstance(conversation, dict) else None
            style = conversation.get("style_transformations", []) if isinstance(conversation, dict) else []
            if not isinstance(style, list):
                raise PolicyViolation("INVALID_STYLE_TRANSFORMATIONS")
            errors: list[str] = []
            try:
                validate_conversation(decision["payload"], messages,
                                      verified_claims=verification.get("data", []) if verification else [])
            except PolicyViolation as exc:
                errors = exc.codes
            if not isinstance(messages, list) or not messages or not any(
                isinstance(item, dict) and item.get("type") == "text" and str(item.get("content", "")).strip()
                for item in messages
            ):
                raise PolicyViolation("NO_PERSISTABLE_DRAFT")
            rewritten = self.store.create_draft(
                decision_event_id=decision["event_id"], messages=messages,
                style_transformations=style,
                idempotency_key=f"{idempotency_key}:draft")
            self._gate(rewritten["event_id"], "POST_CONVERSATION", errors,
                       "REVISE" if errors else "ALLOW", idempotency_key)
            intervention = self._intervention(rewritten["event_id"], idempotency_key,
                                              custom=False, errors=errors)
            return PipelineResult(status="REVISE_REQUIRED" if errors else "REVIEW_REQUIRED",
                                  reason_codes=errors, decision_event_id=decision["event_id"],
                                  draft_event_id=rewritten["event_id"],
                                  intervention_event_id=intervention["event_id"])
        except Exception as exc:
            return PipelineResult(status="HANDOFF", reason_codes=(exc.codes if isinstance(exc, PolicyViolation)
                                  else ["REVISION_FAILED_CLOSED"]))

    def _review_reflection(self, review: dict, draft: dict, key: str) -> dict:
        """Persist a bounded, source-linked interpretation of human feedback."""
        reflection_key = f"{key}:reflection"
        previous = [event for event in self.store.list_events(review["student_id"], event_type="REVIEW_REFLECTION")
                    if event["idempotency_key"] == reflection_key]
        if previous:
            if previous[-1]["payload"]["review_event_id"] != review["event_id"]:
                raise ContractViolation("reflection_key_reused_for_different_review")
            return previous[-1]
        provider = (self.decision_provider if review["payload"]["route_to"] in {"DECISION_AGENT", "BOTH"}
                    else self.conversation_provider)
        comment = review["payload"]["comment"]
        draft_spans = [message.get("content", "") for message in draft["payload"]["messages"]
                       if isinstance(message, dict) and isinstance(message.get("content"), str)]
        output = provider.generate_json("review_reflection", {
            "feedback_types": review["payload"]["feedback_types"],
            "review_comment": comment,
            "rejected_messages": draft["payload"]["messages"],
            "route_to": review["payload"]["route_to"],
        })
        allowed_types = {"FACT", "POLICY", "STRATEGY", "NO_PROGRESS", "TONE", "NATURALNESS", "OTHER"}
        if not isinstance(output, dict) or set(output) != {
            "failure_type", "what_was_wrong", "evidence", "revision_plan", "applies_to"
        } or output["failure_type"] not in allowed_types or output["applies_to"] != "CURRENT_DRAFT":
            raise ContractViolation("invalid_review_reflection")
        feedback_types = set(review["payload"]["feedback_types"])
        style_only = feedback_types <= {"TONE", "NATURALNESS", "LENGTH", "WORDING"}
        if style_only and output["failure_type"] in {"FACT", "POLICY", "STRATEGY", "NO_PROGRESS"}:
            raise ContractViolation("reflection_failure_type_conflicts_with_feedback")
        if not style_only and output["failure_type"] in {"TONE", "NATURALNESS"} and not feedback_types & {
            "TONE", "NATURALNESS", "LENGTH", "WORDING"
        }:
            raise ContractViolation("reflection_failure_type_conflicts_with_feedback")
        if not isinstance(output["what_was_wrong"], str) or not 1 <= len(output["what_was_wrong"].strip()) <= 300:
            raise ContractViolation("invalid_reflection_explanation")
        evidence = output["evidence"]
        plan = output["revision_plan"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3 or not all(
            isinstance(span, str) and span.strip() and len(span) <= 160 and
            any(span in source for source in [comment, *draft_spans]) for span in evidence
        ):
            raise ContractViolation("reflection_evidence_not_source_bound")
        if not isinstance(plan, list) or not 1 <= len(plan) <= 3 or not all(
            isinstance(step, str) and 1 <= len(step.strip()) <= 200 for step in plan
        ):
            raise ContractViolation("invalid_reflection_plan")
        return self.store.record_review_reflection(
            review_event_id=review["event_id"], failure_type=output["failure_type"],
            what_was_wrong=output["what_was_wrong"], evidence=evidence,
            revision_plan=plan, applies_to="CURRENT_DRAFT",
            idempotency_key=reflection_key)
