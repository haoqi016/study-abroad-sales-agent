"""Source-linked, bounded context views for synthetic Sales V2 model calls.

The topic index is a navigation aid, not a summary of customer statements.
Only customer-visible history can enter the recent message window. Drafts and
internal reviews remain outside that window.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


HISTORY_MAX_MESSAGES = 16
HISTORY_TEXT_CHAR_BUDGET = 3000  # Approximate budget, not a tokenizer limit.
OLDER_HISTORY_INDEX_LIMIT = 8
CONTEXT_VERSION = "sales.common-fact-package.v1"

_TOPICS = (
    ("PRICE_OR_PAYMENT", re.compile(
        r"\b(?:price|pricing|quote|quoted|budget|cost|fee|payment|pay|deposit|"
        r"contract|sign|installments?)\b|(?:[$£€]\s*\d)", re.I)),
    ("SERVICE_SCOPE", re.compile(
        r"\b(?:service|scope|support|essay|personal statement|school selection|"
        r"application|submission|documents?)\b", re.I)),
    ("TIMING", re.compile(
        r"\b(?:deadline|timing|timeline|when|next week|tomorrow|this month|"
        r"intake|start date)\b", re.I)),
    ("TARGET", re.compile(
        r"\b(?:university|college|school|program(?:me)?|major|degree|"
        r"Singapore|Hong Kong|United Kingdom|Australia|NTU|NUS)\b", re.I)),
)
_KEY_STUDENT_SIGNAL = re.compile(
    r"\b(?:ready to (?:sign|pay|enrol|enroll)|(?:sign|pay|decide) today|"
    r"if (?:the )?price (?:works|is right|is acceptable)|"
    r"(?:please )?(?:do not|don't|stop) (?:contact(?:ing)?|message|messaging|text(?:ing)?) me|"
    r"(?:budget|deposit|first payment)\b.{0,18}(?:[$£€]\s*)?\d+|"
    r"(?:sign|review) (?:the )?contract)\b", re.I,
)
_TOPIC_LABEL = {
    "PRICE_OR_PAYMENT": "price, payment, or contract",
    "SERVICE_SCOPE": "service scope or application work",
    "TIMING": "timing or application deadline",
    "TARGET": "target institution or program",
}


def _topics(text: str) -> list[str]:
    return [code for code, pattern in _TOPICS if pattern.search(text)]


def _excerpt(text: str, limit: int = 450) -> dict[str, Any]:
    return {"text": text[:limit], "truncated": len(text) > limit}


def _signal_quote(text: str) -> dict[str, Any] | None:
    match = _KEY_STUDENT_SIGNAL.search(text)
    if match is None:
        return None
    start = max(0, match.start() - 55)
    end = min(len(text), match.end() + 85)
    return {"exact_text": text[start:end], "truncated": start > 0 or end < len(text)}


def project_customer_history(
    visible_history: list[dict], *, latest_student_text: str,
    max_messages: int = HISTORY_MAX_MESSAGES,
    text_char_budget: int = HISTORY_TEXT_CHAR_BUDGET,
) -> dict:
    """Keep a contiguous recent tail, then index older source events.

    The newest message is never clipped, even when it exceeds the budget.
    A long preceding message stops the tail rather than being skipped to pick
    older, shorter messages out of order.
    """
    if max_messages < 1 or text_char_budget < 1:
        raise ValueError("invalid_history_projection_limit")
    selected_reversed: list[dict] = []
    used_chars = 0
    for item in reversed(visible_history):
        if item.get("role") not in {"student", "sales"}:
            raise ValueError("non_customer_visible_history_item")
        if len(selected_reversed) >= max_messages:
            break
        message = item["text"]
        if selected_reversed and used_chars + len(message) > text_char_budget:
            break
        selected_reversed.append(item)
        used_chars += len(message)
    recent = list(reversed(selected_reversed))
    older = visible_history[:len(visible_history) - len(recent)]
    latest_topics = set(_topics(latest_student_text))
    ranked = []
    for position, item in enumerate(older):
        topics = _topics(item["text"])
        if topics:
            ranked.append((bool(latest_topics.intersection(topics)), position, item, topics))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    chosen = sorted(ranked[:OLDER_HISTORY_INDEX_LIMIT], key=lambda row: row[1])
    older_index = [{
        "source_event_id": item["event_id"],
        "role": item["role"],
        "topic_codes": topics,
        "summary": ("Student previously mentioned " if item["role"] == "student"
                    else "Salesperson actually sent content about ")
                   + ", ".join(_TOPIC_LABEL[topic] for topic in topics),
        "summary_kind": "DETERMINISTIC_TOPIC_INDEX_NOT_VERBATIM",
    } for _, _, item, topics in chosen]
    key_quotes = [{"source_event_id": item["event_id"], **quote}
                  for item in reversed(older) if item["role"] == "student"
                  if (quote := _signal_quote(item["text"])) is not None][:2]
    key_quotes.reverse()
    return {
        "customer_visible_history": recent,
        "older_history_index": older_index,
        "older_key_original_quotes": key_quotes,
        "history_projection": {
            "max_recent_messages": max_messages,
            "text_char_budget": text_char_budget,
            "recent_count": len(recent),
            "older_count": len(older),
            "older_unindexed_count": len(older) - len(older_index),
            "truncated_customer_history": bool(older),
            "latest_message_kept_exactly": bool(recent) and recent[-1]["text"] == latest_student_text,
        },
    }


def build_commercial_decision_view(
    *, context: dict, all_events: list[dict], memory_items: list[dict],
    active_outcome_ids: set[str],
) -> dict:
    """Separate observed commercial events from a previous model hypothesis."""
    by_id = {event["event_id"]: event for event in all_events}
    sent = [event for event in all_events if event["event_type"] == "HUMAN_SENT"]
    quote_memories = [item for item in memory_items
                      if item.get("active") and item.get("category") == "OFFER_HISTORY"]
    actual_quotes = []
    for item in quote_memories[-3:]:
        value = item["value"]
        sent_ids = item.get("source_event_ids", [])
        sent_id = sent_ids[0] if sent_ids else None
        if sent_id not in by_id or by_id[sent_id]["event_type"] != "HUMAN_SENT":
            continue
        actual_quotes.append({
            "source_sent_event_id": sent_id,
            "price": value.get("price"),
            "price_status": value.get("price_status", "UNKNOWN"),
            "currency": value.get("currency"),
            "payment_terms": value.get("payment_terms", []),
            "actual_sent_content": _excerpt(value["actual_sent_text"]),
        })
    linked_replies = []
    for event in all_events:
        if event["event_type"] != "PROGRESS_ASSESSMENT":
            continue
        sent_id = event["payload"].get("reply_to_sent_event_id")
        inbound_id = event["payload"].get("inbound_event_id")
        inbound = by_id.get(inbound_id)
        if (not sent_id or sent_id not in by_id or by_id[sent_id]["event_type"] != "HUMAN_SENT"
                or inbound is None or inbound["event_type"] != "INBOUND_RECEIVED"):
            continue
        linked_replies.append({
            "source_inbound_event_id": inbound_id,
            "reply_to_actual_sent_event_id": sent_id,
            "student_raw": _excerpt(inbound["payload"]["raw_text"]),
            "link_status": "RECORDED_LINK_NOT_CAUSAL_PROOF",
        })
    outcomes = [{
        "source_event_id": event["event_id"],
        "outcome_type": event["payload"]["outcome_type"],
        "amount": event["payload"].get("amount"),
        "currency": event["payload"].get("currency"),
        "verification_status": event["payload"].get("verification_status", "UNKNOWN"),
        "source_type": event["payload"].get("source_type"),
    } for event in all_events if event["event_type"] == "COMMERCIAL_OUTCOME"
        and event["event_id"] in active_outcome_ids][-5:]
    last_decision = next((event for event in reversed(all_events)
                          if event["event_type"] == "DECISION_READY"), None)
    working = context["working_memory"]
    prior = None
    if last_decision:
        strategy = last_decision["payload"].get("selected_strategy", {})
        sent_ids = working.get("previous_customer_message_sent_event_ids", [])
        prior = {
            "source_decision_event_id": last_decision["event_id"],
            "epistemic_status": "AGENT_HYPOTHESIS_UNVERIFIED",
            "assumed_purchase_blocker": strategy.get("purchase_blocker", "UNKNOWN"),
            "intended_customer_action": strategy.get("next_milestone") or
                                        last_decision["payload"].get("content_contract", {}).get("desired_next_step"),
            "why_now": last_decision["payload"].get("current_objective", {}).get("why_now"),
            "previous_goal": last_decision["payload"].get("current_objective", {}).get("goal"),
            "actual_sent_event_ids_for_decision": sent_ids,
            "student_observation_event_id": context.get("latest_student_message_event_id"),
            "outcome_assessment": "UNKNOWN",
        }
        matching = [event for event in all_events if event["event_type"] == "PROGRESS_ASSESSMENT"
                    and event["payload"].get("inbound_event_id") == context.get("latest_student_message_event_id")
                    and event["payload"].get("reply_to_sent_event_id") in sent_ids]
        if matching:
            assessment = matching[-1]
            prior["outcome_assessment"] = assessment["payload"]["predicted_signal"]
            prior["outcome_assessment_source_event_id"] = assessment["event_id"]
            prior["outcome_assessment_epistemic_status"] = "AGENT_ASSESSMENT_UNVERIFIED"
    latest_sent = sent[-1] if sent else None
    return {
        "schema_version": "sales.commercial-decision-view.v0",
        "observed_facts": {
            "latest_student_message": {
                "source_event_id": context.get("latest_student_message_event_id"),
                "raw_text": context.get("latest_student_raw_text"),
            },
            "latest_actual_sent": ({"source_event_id": latest_sent["event_id"],
                                    "actual_sent_content": _excerpt(latest_sent["payload"]["actual_sent_text"])}
                                   if latest_sent else None),
            "actual_sent_quotes": actual_quotes,
            "customer_replies_with_recorded_link": linked_replies[-3:],
            "commercial_outcomes": outcomes,
        },
        "previous_working_judgment": prior,
        "questions_for_this_turn": [
            "What does the student's exact statement show about purchase readiness? Mark uncertainty explicitly.",
            "What is the nearest unresolved blocker to a voluntary next commitment, and what remains a hypothesis?",
            "Which single voluntary action is appropriate to invite in this turn?",
        ],
    }


def conversation_goal_from_decision(decision: dict) -> dict:
    """Pass the contract's proposed step without granting new authority."""
    objective = decision.get("current_objective", {})
    contract = decision.get("content_contract", {})
    next_step = contract.get("desired_next_step")
    return {
        "current_objective": objective.get("goal"),
        "why_now": objective.get("why_now"),
        "desired_customer_action": next_step,
        "contract_next_step_pre_review": next_step,
        "instruction_status": "PRE_HUMAN_REVIEW_INTERNAL_GUIDANCE_NOT_CUSTOMER_FACT",
    }

def build_common_fact_package(
    *, context: dict, all_events: list[dict], memory_items: list[dict] | None = None,
    active_outcome_ids: set[str] | None = None,
    authoritative_business_materials: dict | None = None,
    tool_results: list[dict] | None = None,
    student_record: dict | None = None,
) -> dict:
    """Build an isolated D0/D1 snapshot from one student's ordered ledger prefix.

    Caller supplies events strictly before TURN_CONTEXT_BUILT and the matching
    student snapshot (with revision checked by the caller). Tool results and
    authority materials must be current authorized tool/policy returns, never
    evaluation cases, examples, or arbitrary CRM dumps. No IO or persistent mutation.
    Use ``project_tool_planning_context(package)`` for T0; reuse the returned
    package unchanged for D0 and D1 so both decisions see the same facts.
    """
    events = deepcopy(all_events)
    student_id = context.get("student_id")
    if student_id and any(event.get("student_id", student_id) != student_id for event in events):
        raise ValueError("cross_student_context_event")
    by_id = {event["event_id"]: event for event in events}
    if len(by_id) != len(events):
        raise ValueError("duplicate_context_event_id")
    latest_id = context.get("latest_student_message_event_id")
    latest = by_id.get(latest_id)
    if not latest or latest.get("event_type") != "INBOUND_RECEIVED":
        raise ValueError("latest_student_event_missing")
    latest_text = latest["payload"]["raw_text"]
    inbound_ids = [event["event_id"] for event in events if event["event_type"] == "INBOUND_RECEIVED"]
    if inbound_ids[-1] != latest_id:
        raise ValueError("stale_latest_student_event")
    visible = []
    for event in events:
        kind, payload = event["event_type"], event["payload"]
        if kind not in {"INBOUND_RECEIVED", "HUMAN_SENT"}:
            continue
        visible.append({"event_id": event["event_id"],
                        "role": "student" if kind == "INBOUND_RECEIVED" else "sales",
                        "text": payload["raw_text" if kind == "INBOUND_RECEIVED" else "actual_sent_text"],
                        "verification_status": payload.get("verification_status",
                            "CUSTOMER_STATED" if kind == "INBOUND_RECEIVED" else "HUMAN_RECORDED_SEND")})
    history = project_customer_history(visible, latest_student_text=latest_text)
    # The observation always preserves the latest inbound in full, even when a
    # subsequent HUMAN_SENT event fills the recent-history budget on a rerun.
    observations = [{"event_id": latest_id, "kind": "STUDENT_RAW", "text": latest_text,
                     "verification_status": latest["payload"].get("verification_status", "CUSTOMER_STATED")}]
    observation_ids = context.get("latest_observation_event_ids", [])
    for event_id in observation_ids:
        event = by_id.get(event_id)
        if event and event["event_type"] == "REVIEW":
            payload = event["payload"]
            observations.append({"event_id": event_id, "kind": "INTERNAL_REVIEW",
                                 "feedback_types": deepcopy(payload.get("feedback_types", [])),
                                 "comment": payload.get("comment", ""),
                                 "route_to": payload.get("route_to"),
                                 "verification_status": "INTERNAL_REVIEW"})

    categories = {"BACKGROUND", "GOAL", "BUDGET", "PREFERENCE", "DECISION_ROLE",
                  "CONTACT_PERMISSION", "OFFER_HISTORY", "COMMITMENT", "REJECTED_PATH"}
    memories = []
    for item in memory_items if memory_items is not None else context.get("memory_items", []):
        if not item.get("active") or item.get("category") not in categories:
            continue
        projected = {key: deepcopy(item[key]) for key in (
            "memory_id", "category", "key", "value", "epistemic_status", "source_event_ids", "active") if key in item}
        sources = projected.get("source_event_ids", [])
        if any(any(marker in by_id.get(source, {}).get("event_type", "")
                   for marker in ("GOLD", "HOLDOUT")) for source in sources):
            continue
        if projected["category"] == "OFFER_HISTORY":
            sent_sources = [by_id[source] for source in sources if by_id.get(source, {}).get("event_type") == "HUMAN_SENT"]
            value = projected.get("value", {})
            if not isinstance(value, dict) or not any(
                event["payload"]["actual_sent_text"] == value.get("actual_sent_text") for event in sent_sources
            ):
                continue
        projected["source_status"] = "SOURCE_EVENTS_PRESENT" if sources and all(source in by_id for source in sources) else "SOURCE_MISSING"
        projected.setdefault("epistemic_status", "UNVERIFIED")
        # A draft or model judgment cannot supply confirmed student facts.
        internal_types = {"DRAFTED", "DECISION_READY", "REVIEW", "REVIEW_REFLECTION"}
        if any(by_id.get(source, {}).get("event_type") in internal_types for source in sources):
            projected["epistemic_status"] = "AGENT_HYPOTHESIS"
            projected["source_status"] = "INTERNAL_SOURCE_UNVERIFIED"
        elif projected["source_status"] == "SOURCE_EVENTS_PRESENT":
            status = projected["epistemic_status"]
            source_events = [by_id[source] for source in sources]
            if (status == "CUSTOMER_STATED" and
                    any(event["event_type"] != "INBOUND_RECEIVED" for event in source_events)):
                projected["epistemic_status"] = "AGENT_HYPOTHESIS"
                projected["source_status"] = "SOURCE_TYPE_MISMATCH"
            elif (status == "HUMAN_CONFIRMED" and
                  not any(event.get("actor") == "SALESPERSON" for event in source_events)):
                projected["epistemic_status"] = "AGENT_HYPOTHESIS"
                projected["source_status"] = "SOURCE_TYPE_MISMATCH"
            elif (status == "SYSTEM_VERIFIED" and projected["category"] == "OFFER_HISTORY" and
                  any(event["event_type"] != "HUMAN_SENT" for event in source_events)):
                projected["epistemic_status"] = "AGENT_HYPOTHESIS"
                projected["source_status"] = "SOURCE_TYPE_MISMATCH"
        memories.append(projected)
    confirmed = {"CUSTOMER_STATED", "HUMAN_CONFIRMED", "SYSTEM_VERIFIED"}
    confirmed_memories = [item for item in memories if item["epistemic_status"] in confirmed
                          and item["source_status"] == "SOURCE_EVENTS_PRESENT"]
    hypotheses = [item for item in memories if item not in confirmed_memories]
    canonical = deepcopy(context)
    canonical["latest_student_raw_text"] = latest_text
    if active_outcome_ids is None:
        corrected = {event["payload"].get("corrects_event_id") for event in events
                     if event["event_type"] == "COMMERCIAL_OUTCOME"}
        active_outcome_ids = {event["event_id"] for event in events
                              if event["event_type"] == "COMMERCIAL_OUTCOME" and event["event_id"] not in corrected}
    graph = build_commercial_decision_view(context=canonical, all_events=events,
                                          memory_items=memories, active_outcome_ids=active_outcome_ids)
    record = student_record or {}
    education_keys = {"undergraduate_university_raw", "undergraduate_major_raw", "undergraduate_tier",
                      "university_raw", "major_raw", "average_score", "score_raw", "score_band",
                      "current_year", "grade", "target_country", "target_university",
                      "target_program_or_major", "intake_year", "intake_batch"}
    education = {key: deepcopy(value) for key, value in record.get("education", {}).items() if key in education_keys}
    targets = {key: deepcopy(value) for key, value in record.get("targets", {}).items()
               if key in {"countries", "universities", "programs_or_majors"}}
    supplied_paths = {f"education.{key}" for key in education} | {f"targets.{key}" for key in targets}
    supplied_paths |= {"sales.stage", "sales.current_objection", "sales.contact_permission"}
    provenance = {key: deepcopy(value) for key, value in context.get("student_record_field_provenance", {}).items()
                  if key in supplied_paths}
    working_keys = {"previous_objective", "previous_action_plan", "previous_customer_message_sent_event_ids",
                    "current_hypotheses", "open_questions", "blocked_paths", "pending_approvals"}
    working = {key: deepcopy(value) for key, value in context.get("working_memory", {}).items() if key in working_keys}
    working.update({"verification_status": "UNVERIFIED", "actual_observation_event_id": latest_id})
    turn = {key: deepcopy(context[key]) for key in (
        "context_id", "student_snapshot_revision", "student_memory_revision", "global_goal", "policy_versions") if key in context}
    turn.update({"context_version": CONTEXT_VERSION, "education": education, "targets": targets,
                 "current_state": deepcopy(context.get("current_state", {})),
                 "student_record_field_provenance": provenance,
                 "state_source_status": "FIELD_PROVENANCE_ATTACHED" if provenance else "SOURCE_MISSING",
                 "observations": observations, "latest_student_message_event_id": latest_id,
                 "latest_student_raw_text": latest_text, **history,
                 "long_term_memory": {"confirmed_or_customer_stated": confirmed_memories,
                                      "unverified_hypotheses": hypotheses},
                 "working_memory": working,
                 "history_window_limit": HISTORY_MAX_MESSAGES})
    return {"commercial_decision_view": graph, "turn_context": turn,
            "tool_results": deepcopy(tool_results or []),
            "authoritative_business_materials": deepcopy(authoritative_business_materials or {}),
            "context_version": CONTEXT_VERSION,
            "source_event_ids": list(dict.fromkeys(
                [item["event_id"] for item in visible] + observation_ids +
                [source for item in memories for source in item.get("source_event_ids", [])] +
                [event["event_id"] for event in events if event["event_type"] in
                 {"COMMERCIAL_OUTCOME", "PROGRESS_ASSESSMENT", "DECISION_READY"}]))}


def project_tool_planning_context(package: dict) -> dict:
    """T0 only receives observable student context, never previous agent plans."""
    turn = package["turn_context"]
    keys = ("context_version", "education", "targets", "current_state", "student_record_field_provenance",
            "state_source_status", "latest_student_message_event_id", "latest_student_raw_text",
            "customer_visible_history", "older_history_index", "older_key_original_quotes", "history_projection")
    result = {key: deepcopy(turn[key]) for key in keys if key in turn}
    result["long_term_memory"] = deepcopy(turn["long_term_memory"]["confirmed_or_customer_stated"])
    return result
